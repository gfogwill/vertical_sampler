import argparse
import json
from enum import Enum
from pprint import pprint

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


def find_serial(baudrate=9600, timeout=2):
    """Find the first available USB serial port (ground station Pico)."""
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        if "Pico" in (port.description or "") or "CircuitPython" in (port.description or ""):
            return serial.Serial(port.device, baudrate=baudrate, timeout=timeout)
    # Fallback: first available port
    if ports:
        return serial.Serial(ports[0].device, baudrate=baudrate, timeout=timeout)
    raise RuntimeError("No serial port found. Is the ground station Pico connected?")


def relay_cmd(args):
    if args.subcommand == "pump":
        cmd = "{} {} {} {}\n".format(args.payload, args.subcommand, args.pump_location, args.state).encode()
    elif args.subcommand == "valve":
        cmd = "{} {} {}\n".format(args.payload, args.subcommand, args.state).encode()
    elif args.subcommand == "data":
        cmd = "{} {}\n".format(args.payload, args.subcommand).encode()
    else:
        raise ValueError("Unknown subcommand: {}".format(args.subcommand))

    with find_serial() as ser:
        ser.write(cmd)
        ser.flush()
        line = ser.readline()
        try:
            data = json.loads(line.decode())
            pprint(data)
        except json.JSONDecodeError:
            print(line.decode().strip())


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
