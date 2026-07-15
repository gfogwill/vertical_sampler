import argparse
import json
import math
import queue
import threading
import time
from enum import Enum
import datetime
import struct

import serial


class Payload(Enum):
    MATOROVA = "matorova"
    KENTTAROVA = "kenttarova"

    def __str__(self) -> str:
        return self.value


class State(Enum):
    ON = "on"
    OFF = "off"

    def __str__(self) -> str:
        return self.value


FILL_INT   = -999999
FILL_UINT  = 4294967295
FILL_FLOAT = -1e9

PAYLOAD_CYCLE_S  = 35
RETRY_INTERVAL_S = 5
MAX_RETRIES      = int(PAYLOAD_CYCLE_S / RETRY_INTERVAL_S) + 1

# Wire format constants (must match payload/pack.py)
_MSG_TYPE_LEN  = 12   # field is 12s in struct format
_MSG_TELEMETRY = "telemetry"
_MSG_CMD_ACK   = "cmd_ack"
_MSG_CMD_ERR   = "cmd_err"
_CMD_RESPONSE_TYPES = {_MSG_CMD_ACK, _MSG_CMD_ERR}

_UTC = datetime.timezone.utc

FIELDS = [
    ("gps_latitude",                "Latitude",          "\u00b0",    "GPS"),
    ("gps_longitude",               "Longitude",         "\u00b0",    "GPS"),
    ("gps_altitude",                "Altitude (GPS)",    "m",     "GPS"),
    ("gps_time",                    "Time (GPS)",        "UTC",   "GPS"),
    ("rh_sensor_humidity",          "Humidity",          "%RH",   "Atmosphere"),
    ("rh_sensor_temperature",       "Temperature (RH)",  "\u00b0C",   "Atmosphere"),
    ("pressure_sensor_pressure",    "Pressure",          "hPa",   "Atmosphere"),
    ("pressure_sensor_temperature", "Temperature (P)",   "\u00b0C",   "Atmosphere"),
    ("_pressure_altitude",          "Altitude (P)",      "m",     "Atmosphere"),
    ("flow",                        "Flow",              "L/min", "Sampler"),
    ("pump_front_state",            "Pump front",        "",      "Sampler"),
    ("pump_back_state",             "Pump back",         "",      "Sampler"),
    ("valve_state",                 "Valve",             "",      "Sampler"),
    ("battery_voltage",             "Battery",           "V",     "System"),
    ("cpu_temperature",             "CPU temp",          "\u00b0C",   "System"),
    ("rssi",                        "RSSI",              "dBm",   "System"),
]

GROUP_ORDER = ["GPS", "Atmosphere", "Sampler", "System"]

# Control key map: key -> (payload_index, actuator, label)
# Uppercase = matorova (index 0), lowercase = kenttarova (index 1)
_CTRL_KEYS = {
    ord('F'): (0, "pump", "front"),
    ord('f'): (1, "pump", "front"),
    ord('B'): (0, "pump", "back"),
    ord('b'): (1, "pump", "back"),
    ord('V'): (0, "valve", None),
    ord('v'): (1, "valve", None),
}


# Per-payload additive pressure offsets (hPa) calibrated against TSI 4100 reference
_PRESSURE_OFFSET_HPA = {
    Payload.MATOROVA:   +2.1,   # offset = P_TSI_4100 - P_sensor_crudo (medido en calibración)
    Payload.KENTTAROVA: -1.9,
}


def _apply_pressure_offset(payload, pressure_hpa):
    if pressure_hpa is None:
        return None
    offset = _PRESSURE_OFFSET_HPA.get(payload, 0.0)
    return pressure_hpa + offset


def _pressure_altitude(pressure_hpa, qnh_hpa):
    try:
        return 44330.0 * (1.0 - math.pow(pressure_hpa / qnh_hpa, 0.1903))
    except Exception:
        return None


def _fmt_value(key, val):
    if val is None:
        return "N/A", True
    if isinstance(val, float) and abs(val - FILL_FLOAT) < 1:
        return "N/A", True
    if isinstance(val, int) and val in (FILL_INT, FILL_UINT):
        return "N/A", True
    if key == "gps_time":
        try:
            ts = datetime.datetime.fromtimestamp(val, tz=_UTC)
            return ts.strftime("%H:%M:%S"), False
        except Exception:
            return str(val), False
    if key in ("pump_front_state", "pump_back_state"):
        return ("ON" if val else "OFF"), False
    if key == "valve_state":
        return str(val), False
    if isinstance(val, float):
        return "{:.2f}".format(val), False
    return str(val), False


def pretty_print(data, payload_id=""):
    COL_LABEL = 26
    COL_VALUE = 10
    COL_UNIT  = 7
    W = COL_LABEL + COL_VALUE + COL_UNIT + 6
    title = "  {}  ".format(payload_id.upper() if payload_id else "TELEMETRY")
    lines = []
    lines.append("\u2554" + "\u2550" * W + "\u2557")
    lines.append("\u2551" + title.center(W) + "\u2551")
    lines.append("\u2560" + "\u2550" * (COL_LABEL + 2) + "\u2566" + "\u2550" * (COL_VALUE + 2) + "\u2566" + "\u2550" * (COL_UNIT + 2) + "\u2563")
    grouped = {g: [] for g in GROUP_ORDER}
    for key, label, unit, group in FIELDS:
        if key.startswith("_"):
            continue
        grouped[group].append((key, label, unit))
    for g_idx, group in enumerate(GROUP_ORDER):
        group_title = "  " + group
        lines.append("\u2551 " + group_title.ljust(COL_LABEL) + " \u2551 " + " " * COL_VALUE + " \u2551 " + " " * COL_UNIT + " \u2551")
        for key, label, unit in grouped[group]:
            val = data.get(key)
            val_str, is_fill = _fmt_value(key, val)
            label_col = "    " + label
            if is_fill:
                val_str = "N/A"
                unit = ""
            lines.append(
                "\u2551 " + label_col.ljust(COL_LABEL) +
                " \u2551 " + val_str.rjust(COL_VALUE) +
                " \u2551 " + unit.ljust(COL_UNIT) + " \u2551"
            )
        if g_idx < len(GROUP_ORDER) - 1:
            lines.append("\u2560" + "\u2550" * (COL_LABEL + 2) + "\u256c" + "\u2550" * (COL_VALUE + 2) + "\u256c" + "\u2550" * (COL_UNIT + 2) + "\u2563")
    lines.append("\u255a" + "\u2550" * (COL_LABEL + 2) + "\u2569" + "\u2550" * (COL_VALUE + 2) + "\u2569" + "\u2550" * (COL_UNIT + 2) + "\u255d")
    print("\n" + "\n".join(lines) + "\n")


def find_serial(baudrate=9600, timeout=2):
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        if "Pico" in (port.description or "") or "CircuitPython" in (port.description or ""):
            return serial.Serial(port.device, baudrate=baudrate, timeout=timeout)
    if ports:
        return serial.Serial(ports[0].device, baudrate=baudrate, timeout=timeout)
    raise RuntimeError("No serial port found. Is the ground station Pico connected?")


def _build_cmd(args):
    if args.subcommand == "pump":
        return "{} {} {} {}\n".format(args.payload, args.subcommand, args.pump_location, args.state).encode()
    elif args.subcommand == "valve":
        return "{} {} {}\n".format(args.payload, args.subcommand, args.state).encode()
    elif args.subcommand == "data":
        return "{} {}\n".format(args.payload, args.subcommand).encode()
    else:
        raise ValueError("Unknown subcommand: {}".format(args.subcommand))


def _read_json_line(ser, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline()
            text = raw.decode(errors="ignore").strip()
            if not text:
                continue
            if text.startswith("{") or text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
        else:
            time.sleep(0.05)
    return None


def _drain_serial(ser, timeout_s=0.5):
    """Discard stale bytes/lines already in the serial buffer."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ser.in_waiting:
            ser.readline()  # discard
        else:
            time.sleep(0.05)


def _read_cmd_response(ser, timeout_s, accept_telemetry=False):
    """Read lines until we get a suitable response packet.

    If accept_telemetry=True (used for the 'data' command), a
    msg_type='telemetry' packet is also accepted as a valid response.
    If accept_telemetry=False (default), only cmd_ack and cmd_err are
    accepted; heartbeats are skipped so they cannot be mistaken for a
    command acknowledgement.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline()
            if not raw or raw == b"\x00" * len(raw):
                continue
            text = raw.decode(errors="ignore").strip()
            if not text:
                continue
            if text.startswith("{"):
                try:
                    d = json.loads(text)
                    msg_type = d.get("msg_type", _MSG_TELEMETRY)
                    if msg_type in _CMD_RESPONSE_TYPES:
                        return d
                    if accept_telemetry and msg_type == _MSG_TELEMETRY:
                        return d
                except json.JSONDecodeError:
                    pass
        else:
            time.sleep(0.05)
    return None


def _enrich_data(data, payload, qnh_hpa=1013.25):
    """Apply pressure offset and compute pressure altitude in-place."""
    p = data.get("pressure_sensor_pressure")
    if p is not None and isinstance(p, (int, float)) and abs(p - FILL_FLOAT) > 1:
        p_corrected = _apply_pressure_offset(payload, p)
        data["pressure_sensor_pressure"] = p_corrected
        data["_pressure_altitude"] = _pressure_altitude(p_corrected, qnh_hpa)
    else:
        data["_pressure_altitude"] = None
    return data


def relay_cmd(args):
    cmd = _build_cmd(args)
    is_data = (args.subcommand == "data")
    with find_serial() as ser:
        time.sleep(0.2)
        _drain_serial(ser, timeout_s=0.5)
        for attempt in range(1, MAX_RETRIES + 1):
            print("Sending command (attempt {}/{})...".format(attempt, MAX_RETRIES), end="", flush=True)
            ser.write(cmd)
            ser.flush()
            data = _read_cmd_response(ser, timeout_s=RETRY_INTERVAL_S,
                                      accept_telemetry=is_data)
            if data is not None:
                print(" OK" if data.get("msg_type") != _MSG_CMD_ERR else " ERROR")
                if args.subcommand == "data":
                    _enrich_data(data, args.payload)
                    pretty_print(data, payload_id=str(args.payload))
                else:
                    print(json.dumps(data, indent=2))
                return
            print(" no response, retrying...")
        print("ERROR: no response from {} after {} attempts (~{}s).".format(
            args.payload, MAX_RETRIES, MAX_RETRIES * RETRY_INTERVAL_S
        ))


# ---------------------------------------------------------------------------
# Monitor TUI
# ---------------------------------------------------------------------------

MAX_EVENTS = 8
_PAYLOADS_ALL = [Payload.MATOROVA, Payload.KENTTAROVA]

_CMD_POLL_ALL  = "poll_all"
_CMD_POLL_ONE  = "poll_one"
_CMD_CONTROL   = "control"
_CMD_QUIT      = "quit"


class PollWorker(threading.Thread):
    def __init__(self, payloads, qnh, log_file):
        super().__init__(daemon=True)
        self.payloads = payloads
        self.qnh = qnh
        self.log_file = log_file
        self.cmd_q = queue.Queue()
        self.result_q = queue.Queue()

    def _enrich(self, d, payload):
        p = d.get("pressure_sensor_pressure")
        if p is not None and isinstance(p, (int, float)) and abs(p - FILL_FLOAT) > 1:
            p_corrected = _apply_pressure_offset(payload, p)
            d["pressure_sensor_pressure_corrected"] = p_corrected
            d["_pressure_altitude"] = _pressure_altitude(p_corrected, self.qnh)
        else:
            d["pressure_sensor_pressure_corrected"] = None
            d["_pressure_altitude"] = None
        return d

    def _log(self, d):
        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    f.write(json.dumps(d) + "\n")
            except Exception:
                pass

    def _poll_one(self, ser, payload):
        cmd = "{} data\n".format(payload).encode()
        _drain_serial(ser, timeout_s=0.5)
        ser.write(cmd)
        ser.flush()
        d = _read_cmd_response(ser, timeout_s=6, accept_telemetry=True)
        if d is not None:
            d["_ts"] = time.time()
            self._enrich(d, payload)
            self._log(d)
        self.result_q.put((payload, d))

    def _send_control(self, ser, cmd_bytes, payload):
        _drain_serial(ser, timeout_s=0.5)
        ser.write(cmd_bytes)
        ser.flush()
        d = _read_cmd_response(ser, timeout_s=8, accept_telemetry=True)
        if d is not None:
            d["_ts"] = time.time()
            self._enrich(d, payload)
            self._log(d)
        return d

    def _try_parse_heartbeat(self, ser):
        if ser.in_waiting:
            raw = ser.readline()
            if not raw or raw == b"\x00" * len(raw):
                return
            text = raw.decode(errors="ignore").strip()
            if text.startswith("{"):
                try:
                    d = json.loads(text)
                    msg_type = d.get("msg_type", _MSG_TELEMETRY)
                    if msg_type not in (_MSG_TELEMETRY, ""):
                        return
                    d["_ts"] = time.time()
                    # Heartbeats may omit a payload id; pass None so offset defaults to 0.0
                    self._enrich(d, None)
                    self._log(d)
                    self.result_q.put((None, d))
                except Exception:
                    pass

    def run(self):
        try:
            ser = find_serial(baudrate=9600, timeout=0.5)
        except Exception as e:
            self.result_q.put((None, {"_error": str(e)}))
            return

        with ser:
            time.sleep(0.2)
            ser.reset_input_buffer()

            while True:
                try:
                    item = self.cmd_q.get_nowait()
                    if item == _CMD_QUIT:
                        break
                    elif item == _CMD_POLL_ALL:
                        for p in self.payloads:
                            self._poll_one(ser, p)
                    elif isinstance(item, tuple) and item[0] == _CMD_POLL_ONE:
                        self._poll_one(ser, item[1])
                    elif isinstance(item, tuple) and item[0] == _CMD_CONTROL:
                        _, payload, cmd_bytes = item
                        d = self._send_control(ser, cmd_bytes, payload)
                        self.result_q.put((payload, d))
                    continue
                except queue.Empty:
                    pass

                self._try_parse_heartbeat(ser)
                time.sleep(0.05)


def _safe_addnstr(win, row, col, text, max_cols, attr):
    import curses
    rows, cols = win.getmaxyx()
    if row < 0 or row >= rows - 1:
        return
    if col < 0 or col >= cols - 1:
        return
    avail = min(max_cols, cols - col - 1)
    if avail <= 0:
        return
    try:
        win.addnstr(row, col, text, avail, attr)
    except curses.error:
        pass


def _render_column(win, data, payload_label, col_x, col_w, max_rows):
    import curses
    row = 1
    header = " {} ".format(payload_label.upper())
    _safe_addnstr(win, row, col_x, header.center(col_w), col_w,
                  curses.color_pair(2) | curses.A_BOLD)
    row += 1
    if not data:
        _safe_addnstr(win, row, col_x, "  --- no data ---".ljust(col_w), col_w,
                      curses.color_pair(3))
        return
    last_updated = data.get("_ts")
    if last_updated:
        age = int(time.time() - last_updated)
        ts_str = "  updated {}s ago".format(age)
    else:
        ts_str = ""
    _safe_addnstr(win, row, col_x, ts_str.ljust(col_w), col_w, curses.color_pair(4))
    row += 1
    grouped = {g: [] for g in GROUP_ORDER}
    for key, label, unit, group in FIELDS:
        grouped[group].append((key, label, unit))
    for group in GROUP_ORDER:
        if row >= max_rows:
            break
        _safe_addnstr(win, row, col_x, "  {}".format(group).ljust(col_w), col_w,
                      curses.color_pair(2) | curses.A_UNDERLINE)
        row += 1
        for key, label, unit in grouped[group]:
            if row >= max_rows:
                break
            val = data.get(key)
            val_str, is_fill = _fmt_value(key, val)
            if key in ("pump_front_state", "pump_back_state") and val:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif key == "valve_state" and val:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif is_fill:
                attr = curses.color_pair(3)
            else:
                attr = curses.color_pair(1)
            line = "    {:<18} {:>8} {}".format(label, val_str, unit)
            _safe_addnstr(win, row, col_x, line.ljust(col_w), col_w, attr)
            row += 1


def _run_monitor(payloads, qnh, log_file):
    import curses

    worker = PollWorker(payloads, qnh, log_file)
    worker.start()

    state  = {p: {} for p in payloads}
    events = []

    def add_event(msg):
        events.append((time.time(), msg))
        if len(events) > MAX_EVENTS:
            events.pop(0)

    def _toggle_pump(payload, location):
        cur = state[payload].get(
            "pump_front_state" if location == "front" else "pump_back_state", 0
        )
        new_state = "off" if cur else "on"
        cmd = "{} pump {} {}\n".format(payload, location, new_state).encode()
        worker.cmd_q.put((_CMD_CONTROL, payload, cmd))
        add_event("{} pump {} -> {}".format(payload, location, new_state.upper()))

    def _toggle_valve(payload):
        cur = state[payload].get("valve_state", 0)
        new_state = "off" if cur else "on"
        cmd = "{} valve {}\n".format(payload, new_state).encode()
        worker.cmd_q.put((_CMD_CONTROL, payload, cmd))
        add_event("{} valve -> {}".format(payload, new_state.upper()))

    def draw(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE,  -1)
        curses.init_pair(2, curses.COLOR_CYAN,   -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_GREEN,  -1)
        stdscr.timeout(100)

        add_event("Monitor started. Listening for heartbeats...")

        while True:
            try:
                while True:
                    payload, d = worker.result_q.get_nowait()
                    if d is None:
                        if payload:
                            add_event("{} - no response".format(payload))
                    elif "_error" in d:
                        add_event("Serial error: {}".format(d["_error"]))
                    else:
                        target = payload
                        if target is None:
                            pid = d.get("payload_id", "").strip()
                            for p in payloads:
                                if str(p) == pid:
                                    target = p
                                    break
                        if target and target in payloads:
                            state[target] = d
                            p_val = d.get("pressure_sensor_pressure")
                            p_str = ("{:.2f}".format(p_val)
                                     if isinstance(p_val, float) and abs(p_val - FILL_FLOAT) > 1
                                     else "N/A")
                            add_event("{} \u2014 P={} hPa".format(target, p_str))
                        elif target is None:
                            add_event("heartbeat: unknown payload_id={!r}".format(
                                d.get("payload_id", "?")))
            except queue.Empty:
                pass

            stdscr.erase()
            rows, cols = stdscr.getmaxyx()
            ts_now = datetime.datetime.now(tz=_UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            header = " Vertical Sampler Monitor  {}  QNH={} hPa ".format(ts_now, qnh)
            _safe_addnstr(stdscr, 0, 0, header.ljust(cols - 1), cols - 1,
                          curses.color_pair(2) | curses.A_REVERSE)

            n = len(payloads)
            col_w = max(1, cols // max(n, 1))
            events_start = max(3, rows - MAX_EVENTS - 2)
            data_rows = events_start - 1

            for i, p in enumerate(payloads):
                _render_column(stdscr, state[p], str(p), i * col_w, col_w, data_rows)

            div_row = events_start - 1
            if 0 < div_row < rows - 1:
                _safe_addnstr(stdscr, div_row, 0,
                              " Events ".center(cols - 1, "\u2500"), cols - 1,
                              curses.color_pair(4))

            for j, (evt_ts, evt_msg) in enumerate(events[-(MAX_EVENTS):]):
                er = events_start + j
                if er >= rows - 1:
                    break
                t_str = datetime.datetime.fromtimestamp(evt_ts, tz=_UTC).strftime("%H:%M:%S")
                _safe_addnstr(stdscr, er, 0,
                              "  {} {}".format(t_str, evt_msg).ljust(cols - 1),
                              cols - 1, curses.color_pair(1))

            footer = ("  F/f=pump front  B/b=pump back  V/v=valve  "
                      "(UPPER=matorova lower=kenttarova)  "
                      "r=poll all  1/2=poll  q=quit")
            try:
                stdscr.addnstr(rows - 1, 0, footer.ljust(cols - 1), cols - 1,
                               curses.color_pair(2) | curses.A_REVERSE)
            except curses.error:
                pass

            stdscr.noutrefresh()
            curses.doupdate()

            ch = stdscr.getch()
            if ch == ord('q'):
                worker.cmd_q.put(_CMD_QUIT)
                break
            elif ch == ord('r'):
                worker.cmd_q.put(_CMD_POLL_ALL)
                add_event("Manual poll all...")
            elif ch == ord('1') and len(payloads) >= 1:
                worker.cmd_q.put((_CMD_POLL_ONE, payloads[0]))
                add_event("Polling {}...".format(payloads[0]))
            elif ch == ord('2') and len(payloads) >= 2:
                worker.cmd_q.put((_CMD_POLL_ONE, payloads[1]))
                add_event("Polling {}...".format(payloads[1]))
            elif ch in _CTRL_KEYS:
                p_idx, actuator, location = _CTRL_KEYS[ch]
                if p_idx < len(payloads):
                    p = payloads[p_idx]
                    if actuator == "pump":
                        _toggle_pump(p, location)
                    elif actuator == "valve":
                        _toggle_valve(p)

    try:
        curses.wrapper(draw)
    except Exception as exc:
        worker.cmd_q.put(_CMD_QUIT)
        print("[monitor] curses error ({}}), falling back to plain poll.".format(exc))
        worker2 = PollWorker(payloads, qnh, log_file)
        worker2.start()
        worker2.cmd_q.put(_CMD_POLL_ALL)
        received = set()
        deadline = time.time() + 40
        state2 = {p: {} for p in payloads}
        while len(received) < len(payloads) and time.time() < deadline:
            try:
                payload, d = worker2.result_q.get(timeout=1)
                if payload and d and "_error" not in d:
                    state2[payload] = d
                    received.add(payload)
            except queue.Empty:
                pass
        worker2.cmd_q.put(_CMD_QUIT)
        for p in payloads:
            if state2[p]:
                pretty_print(state2[p], payload_id=str(p))


def parse_args():
    parser = argparse.ArgumentParser(description="Vertical Sampler ground control CLI")
    subparsers = parser.add_subparsers(title="subcommands", dest="subcommand", required=True)

    for sub_name in ("pump", "valve", "data"):
        p = subparsers.add_parser(sub_name)
        p.add_argument("payload", type=Payload, choices=list(Payload))
        if sub_name == "pump":
            p.add_argument("pump_location", type=str, choices=["front", "back", "both"])
            p.add_argument("state", type=State, choices=list(State))
        elif sub_name == "valve":
            p.add_argument("state", type=State, choices=list(State))

    mon = subparsers.add_parser("monitor", help="Live TUI dashboard")
    mon.add_argument(
        "--payloads", nargs="+", type=Payload,
        choices=list(Payload), default=_PAYLOADS_ALL,
        metavar="PAYLOAD",
    )
    mon.add_argument("--qnh", type=float, default=1013.25)
    mon.add_argument("--log-file", dest="log_file", default=None, metavar="FILE")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.subcommand == "monitor":
        _run_monitor(
            payloads=args.payloads,
            qnh=args.qnh,
            log_file=args.log_file,
        )
    else:
        relay_cmd(args)
