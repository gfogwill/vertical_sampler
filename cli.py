import argparse
import json
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

# Max time a payload cycle takes (GPS read + sensors + listen window).
# If no response arrives within this time, we retry.
PAYLOAD_CYCLE_S  = 35   # seconds — slightly above worst-case cycle
RETRY_INTERVAL_S = 5    # how long to wait between retries
MAX_RETRIES      = int(PAYLOAD_CYCLE_S / RETRY_INTERVAL_S) + 1

FIELDS = [
    # (key, label, unit, group)
    ("gps_latitude",               "Latitude",           "°",     "GPS"),
    ("gps_longitude",              "Longitude",          "°",     "GPS"),
    ("gps_altitude",               "Altitude",           "m",     "GPS"),
    ("gps_time",                   "Time (GPS)",         "UTC",   "GPS"),
    ("rh_sensor_humidity",         "Humidity",           "%RH",   "Atmosphere"),
    ("rh_sensor_temperature",      "Temperature (RH)",   "°C",    "Atmosphere"),
    ("pressure_sensor_pressure",   "Pressure",           "hPa",   "Atmosphere"),
    ("pressure_sensor_temperature", "Temperature (P)",   "°C",    "Atmosphere"),
    ("flow",                       "Flow",               "L/min", "Sampler"),
    ("pump_front_state",           "Pump front",         "",      "Sampler"),
    ("pump_back_state",            "Pump back",          "",      "Sampler"),
    ("valve_state",                "Valve",              "",      "Sampler"),
    ("battery_voltage",            "Battery",            "V",     "System"),
    ("rssi",                       "RSSI",               "dBm",   "System"),
]

GROUP_ORDER = ["GPS", "Atmosphere", "Sampler", "System"]


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


def relay_cmd(args):
    cmd = _build_cmd(args)

    with find_serial() as ser:
        for attempt in range(1, MAX_RETRIES + 1):
            print("Sending command (attempt {}/{})...".format(attempt, MAX_RETRIES), end="", flush=True)
            ser.reset_input_buffer()
            ser.write(cmd)
            ser.flush()

            # Wait up to RETRY_INTERVAL_S for a response
            deadline = time.time() + RETRY_INTERVAL_S
            line = b""
            while time.time() < deadline:
                if ser.in_waiting:
                    line = ser.readline()
                    if line.strip():
                        break
                time.sleep(0.1)

            if line.strip():
                print(" OK")
                try:
                    data = json.loads(line.decode())
                    if args.subcommand == "data":
                        pretty_print(data, payload_id=str(args.payload))
                    else:
                        print(json.dumps(data, indent=2))
                except json.JSONDecodeError:
                    print(line.decode().strip())
                return

            print(" no response, retrying in {}s...".format(RETRY_INTERVAL_S))

        print("ERROR: no response from {} after {} attempts (~{}s).".format(
            args.payload, MAX_RETRIES, MAX_RETRIES * RETRY_INTERVAL_S
        ))


def parse_args():
    parser = argparse.ArgumentParser(description="Vertical Sampler ground control CLI")
    parser.add_argument("payload", type=Payload, choices=list(Payload))
    subparsers = parser.add_subparsers(title="subcommands", dest="subcommand", required=True)

    pump = subparsers.add_parser("pump", help="Control pump")
    pump.add_argument("pump_location", type=str, choices=["front", "back", "both"])
    pump.add_argument("state", type=State, choices=list(State))

    valve = subparsers.add_parser("valve", help="Control electrovalve")
    valve.add_argument("state", type=State, choices=list(State))

    subparsers.add_parser("data", help="Request telemetry data")

    return parser.parse_args()


if __name__ == "__main__":
    relay_cmd(parse_args())
