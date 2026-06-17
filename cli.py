import argparse
import json
import math
import queue
import threading
import time
from enum import Enum
import datetime

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

POLL_INTERVAL_S  = 30  # monitor: seconds between automatic data polls

FIELDS = [
    ("gps_latitude",               "Latitude",           "°",     "GPS"),
    ("gps_longitude",              "Longitude",          "°",     "GPS"),
    ("gps_altitude",               "Altitude (GPS)",     "m",     "GPS"),
    ("gps_time",                   "Time (GPS)",         "UTC",   "GPS"),
    ("rh_sensor_humidity",         "Humidity",           "%RH",   "Atmosphere"),
    ("rh_sensor_temperature",      "Temperature (RH)",   "°C",    "Atmosphere"),
    ("pressure_sensor_pressure",   "Pressure",           "hPa",   "Atmosphere"),
    ("pressure_sensor_temperature", "Temperature (P)",   "°C",    "Atmosphere"),
    ("_pressure_altitude",         "Altitude (P)",       "m",     "Atmosphere"),
    ("flow",                       "Flow",               "L/min", "Sampler"),
    ("pump_front_state",           "Pump front",         "",      "Sampler"),
    ("pump_back_state",            "Pump back",          "",      "Sampler"),
    ("valve_state",                "Valve",              "",      "Sampler"),
    ("battery_voltage",            "Battery",            "V",     "System"),
    ("cpu_temperature",            "CPU temp",           "°C",    "System"),
    ("rssi",                       "RSSI",               "dBm",   "System"),
]

GROUP_ORDER = ["GPS", "Atmosphere", "Sampler", "System"]


def _pressure_altitude(pressure_hpa, qnh_hpa):
    """ISA pressure altitude relative to QNH (metres)."""
    try:
        return 44330.0 * (1.0 - math.pow(pressure_hpa / qnh_hpa, 0.1903))
    except Exception:
        return None


def _fmt_value(key, val):
    """Format a raw value for display. Returns (display_str, is_fill)."""
    if val is None:
        return "N/A", True
    if isinstance(val, float) and abs(val - FILL_FLOAT) < 1:
        return "N/A", True
    if isinstance(val, int) and val in (FILL_INT, FILL_UINT):
        return "N/A", True

    if key == "gps_time":
        try:
            ts = datetime.datetime.utcfromtimestamp(val)
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
    lines.append("╔" + "═" * W + "╗")
    lines.append("║" + title.center(W) + "║")
    lines.append("╠" + "═" * (COL_LABEL + 2) + "╦" + "═" * (COL_VALUE + 2) + "╦" + "═" * (COL_UNIT + 2) + "╣")

    grouped = {g: [] for g in GROUP_ORDER}
    for key, label, unit, group in FIELDS:
        if key.startswith("_"):
            continue
        grouped[group].append((key, label, unit))

    for g_idx, group in enumerate(GROUP_ORDER):
        group_title = "  " + group
        lines.append("║ " + group_title.ljust(COL_LABEL) + " ║ " + " " * COL_VALUE + " ║ " + " " * COL_UNIT + " ║")

        for key, label, unit in grouped[group]:
            val = data.get(key)
            val_str, is_fill = _fmt_value(key, val)
            label_col = "    " + label
            if is_fill:
                val_str = "N/A"
                unit = ""
            lines.append(
                "║ " + label_col.ljust(COL_LABEL) +
                " ║ " + val_str.rjust(COL_VALUE) +
                " ║ " + unit.ljust(COL_UNIT) + " ║"
            )

        if g_idx < len(GROUP_ORDER) - 1:
            lines.append("╠" + "═" * (COL_LABEL + 2) + "╬" + "═" * (COL_VALUE + 2) + "╬" + "═" * (COL_UNIT + 2) + "╣")

    lines.append("╚" + "═" * (COL_LABEL + 2) + "╩" + "═" * (COL_VALUE + 2) + "╩" + "═" * (COL_UNIT + 2) + "╝")
    print("\n" + "\n".join(lines) + "\n")


def find_serial(baudrate=9600, timeout=2):
    """Find the first available USB serial port (ground station Pico)."""
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
    """Read lines from ser until a valid JSON line is found or timeout expires."""
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


def relay_cmd(args):
    cmd = _build_cmd(args)

    with find_serial() as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()

        for attempt in range(1, MAX_RETRIES + 1):
            print("Sending command (attempt {}/{})...".format(attempt, MAX_RETRIES), end="", flush=True)
            ser.write(cmd)
            ser.flush()

            data = _read_json_line(ser, timeout_s=RETRY_INTERVAL_S)

            if data is not None:
                print(" OK")
                if args.subcommand == "data":
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

_CMD_POLL_ALL = "poll_all"
_CMD_POLL_ONE = "poll_one"
_CMD_QUIT     = "quit"


class PollWorker(threading.Thread):
    """Background thread that owns the serial port and does all blocking I/O.

    Main thread sends commands via cmd_q; results arrive via result_q as
    (payload, data_dict_or_None) tuples.
    """

    def __init__(self, payloads, qnh, log_file):
        super().__init__(daemon=True)
        self.payloads = payloads
        self.qnh = qnh
        self.log_file = log_file
        self.cmd_q = queue.Queue()
        self.result_q = queue.Queue()

    def _poll_one(self, ser, payload):
        cmd = "{} data\n".format(payload).encode()
        ser.reset_input_buffer()
        ser.write(cmd)
        ser.flush()
        d = _read_json_line(ser, timeout_s=6)
        if d is not None:
            d["_ts"] = time.time()
            p = d.get("pressure_sensor_pressure")
            if p is not None and isinstance(p, (int, float)) and abs(p - FILL_FLOAT) > 1:
                d["_pressure_altitude"] = _pressure_altitude(p, self.qnh)
            else:
                d["_pressure_altitude"] = None
            if self.log_file:
                try:
                    with open(self.log_file, "a") as f:
                        f.write(json.dumps(d) + "\n")
                except Exception:
                    pass
        self.result_q.put((payload, d))

    def run(self):
        try:
            ser = find_serial(baudrate=9600, timeout=0.5)
        except Exception as e:
            self.result_q.put((None, {"_error": str(e)}))
            return

        with ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            # Initial poll on startup
            for p in self.payloads:
                self._poll_one(ser, p)

            while True:
                try:
                    item = self.cmd_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item == _CMD_QUIT:
                    break
                elif item == _CMD_POLL_ALL:
                    for p in self.payloads:
                        self._poll_one(ser, p)
                elif isinstance(item, tuple) and item[0] == _CMD_POLL_ONE:
                    self._poll_one(ser, item[1])


def _safe_addnstr(win, row, col, text, max_cols, attr):
    """addnstr that never raises curses.error due to boundary conditions."""
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
    """Draw one payload column inside window win."""
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
            line = "    {:<18} {:>8} {}".format(label, val_str, unit)
            attr = curses.color_pair(3) if is_fill else curses.color_pair(1)
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

    def draw(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE,  -1)
        curses.init_pair(2, curses.COLOR_CYAN,   -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_GREEN,  -1)
        stdscr.timeout(100)  # getch blocks at most 100 ms — keyboard always responsive

        next_auto_poll = time.time() + POLL_INTERVAL_S
        add_event("Monitor started — initial poll in progress...")

        while True:
            # --- drain result queue (non-blocking) ---
            try:
                while True:
                    payload, d = worker.result_q.get_nowait()
                    if payload is None:
                        add_event("Serial error: {}".format(d.get("_error", "?")))
                    elif d is None:
                        add_event("{} — no response".format(payload))
                    else:
                        state[payload] = d
                        p_val = d.get("pressure_sensor_pressure")
                        p_str = ("{:.2f}".format(p_val)
                                 if isinstance(p_val, float) and abs(p_val - FILL_FLOAT) > 1
                                 else "N/A")
                        add_event("{} OK — P={} hPa".format(payload, p_str))
            except queue.Empty:
                pass

            # --- auto poll trigger ---
            now = time.time()
            if now >= next_auto_poll:
                worker.cmd_q.put(_CMD_POLL_ALL)
                add_event("Auto-polling all payloads...")
                next_auto_poll = now + POLL_INTERVAL_S

            # --- draw ---
            stdscr.erase()
            rows, cols = stdscr.getmaxyx()

            ts_now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
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
                              " Events ".center(cols - 1, "─"), cols - 1,
                              curses.color_pair(4))

            for j, (evt_ts, evt_msg) in enumerate(events[-(MAX_EVENTS):]):
                er = events_start + j
                if er >= rows - 1:
                    break
                t_str = datetime.datetime.utcfromtimestamp(evt_ts).strftime("%H:%M:%S")
                _safe_addnstr(stdscr, er, 0,
                              "  {} {}".format(t_str, evt_msg).ljust(cols - 1),
                              cols - 1, curses.color_pair(1))

            footer = "  r=refresh  1=matorova  2=kenttarova  q=quit  (auto {}s)".format(POLL_INTERVAL_S)
            try:
                stdscr.addnstr(rows - 1, 0, footer.ljust(cols - 1), cols - 1,
                               curses.color_pair(2) | curses.A_REVERSE)
            except curses.error:
                pass

            stdscr.noutrefresh()
            curses.doupdate()

            # --- key input — never blocks > 100 ms ---
            ch = stdscr.getch()
            if ch == ord('q'):
                worker.cmd_q.put(_CMD_QUIT)
                break
            elif ch == ord('r'):
                worker.cmd_q.put(_CMD_POLL_ALL)
                add_event("Manual refresh requested...")
                next_auto_poll = time.time() + POLL_INTERVAL_S
            elif ch == ord('1') and len(payloads) >= 1:
                worker.cmd_q.put((_CMD_POLL_ONE, payloads[0]))
                add_event("Polling {}...".format(payloads[0]))
            elif ch == ord('2') and len(payloads) >= 2:
                worker.cmd_q.put((_CMD_POLL_ONE, payloads[1]))
                add_event("Polling {}...".format(payloads[1]))

    try:
        curses.wrapper(draw)
    except Exception as exc:
        worker.cmd_q.put(_CMD_QUIT)
        print("[monitor] curses error ({}), falling back to plain poll.".format(exc))
        received = set()
        deadline = time.time() + 30
        while len(received) < len(payloads) and time.time() < deadline:
            try:
                payload, d = worker.result_q.get(timeout=1)
                if payload and d:
                    state[payload] = d
                    received.add(payload)
            except queue.Empty:
                pass
        for p in payloads:
            if state[p]:
                pretty_print(state[p], payload_id=str(p))


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
        help="Payloads to monitor (default: both)"
    )
    mon.add_argument(
        "--qnh", type=float, default=1013.25,
        help="QNH altimeter setting in hPa (default: 1013.25 ISA)"
    )
    mon.add_argument(
        "--log-file", dest="log_file", default=None,
        metavar="FILE",
        help="Append received data records as JSONL to FILE"
    )

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
