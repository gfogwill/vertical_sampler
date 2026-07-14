import supervisor
supervisor.runtime.autoreload = False

import json
import select
import sys
import time
import busio
import board
import led
import pack
import address
from lora import LoRa

BOARD_GP_LORA_SCK = board.GP2
BOARD_GP_LORA_TX  = board.GP3
BOARD_GP_LORA_RX  = board.GP4

spi = busio.SPI(BOARD_GP_LORA_SCK, MOSI=BOARD_GP_LORA_TX, MISO=BOARD_GP_LORA_RX)

# Default destination = ground itself so the LoRa object is always valid.
# set_destination() overrides this before every send.
lora = LoRa(
    spi=spi,
    node=address.ground_rfm_address,
    destination=address.ground_rfm_address,
)

POLL = select.poll()
POLL.register(sys.stdin, 1)


class UnexpectedCommand(Exception):
    pass


def _drain_lora(timeout=0.2, max_reads=8):
    drained = 0
    for _ in range(max_reads):
        msg = lora.receive(timeout=timeout)
        if msg is None:
            break
        drained += 1
    return drained


def _parse_packet(msg):
    if not isinstance(msg, (bytes, bytearray)):
        return None
    try:
        return pack.bytes2dict(msg)
    except Exception:
        return None


def _print_json(obj):
    print(json.dumps(obj, separators=(",", ":")))


def _process_command(cmd_str):
    parts = cmd_str.split()
    if len(parts) < 2:
        raise UnexpectedCommand("too few parts")

    payload_id, *cmd = parts
    if payload_id == "kenttarova":
        lora.set_destination(address.kenttarova_rfm_address)
    elif payload_id == "matorova":
        lora.set_destination(address.matorova_rfm_address)
    else:
        raise UnexpectedCommand("unknown payload: " + payload_id)

    # Drain old/stale LoRa frames before issuing a new command.
    _drain_lora()

    lora.send(" ".join(cmd).encode())

    ack = None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        msg = lora.receive(timeout=1.5)
        if msg is None:
            continue

        d = _parse_packet(msg)
        if d is None:
            # Ignore stray/corrupt/non-pack frames
            continue

        msg_type = d.get("msg_type", "")

        # Use pack constants so these strings stay in sync with the wire format.
        if msg_type == pack.MSG_COMMAND_ACK:
            ack = d
            break
        elif msg_type == pack.MSG_COMMAND_ERROR:
            ack = d
            break
        elif msg_type == pack.MSG_TELEMETRY:
            # Heartbeat arrived while waiting for ACK; ignore.
            continue
        elif not msg_type:
            # Backward compat: old firmware without msg_type field.
            # Accept as ACK only if the struct parsed cleanly.
            ack = d
            break
        # Unknown msg_type: skip

    if ack is not None:
        led.blink(ntimes=6, bsleep=0.4, tsleep=0.2, esleep=0.4)
        _print_json(ack)
    else:
        _print_json({"error": "no command ack"})


while True:
    led.blink(ntimes=3, bsleep=0.1, tsleep=0.1, esleep=0.1)
    if POLL.poll(0):
        cmd_str = sys.stdin.readline()
        cmd_str = cmd_str.replace("\x00", "").strip().lower()
        if not cmd_str or cmd_str.startswith("{"):
            continue
        try:
            _process_command(cmd_str)
        except UnexpectedCommand as e:
            _print_json({"error": "unexpected command: " + str(e)})
        except Exception as e:
            err = str(e).replace('"', "'").replace("\n", " ")
            _print_json({"error": err})
    else:
        # No serial command waiting — listen passively for heartbeats.
        msg = lora.receive(timeout=1)
        if msg is not None and isinstance(msg, (bytes, bytearray)):
            led.blink(ntimes=2, bsleep=0.1, tsleep=0.1, esleep=0.1)
            d = _parse_packet(msg)
            if d is not None:
                # Annotate msg_type for old firmware that predates the field
                if not d.get("msg_type"):
                    d["msg_type"] = pack.MSG_TELEMETRY
                _print_json(d)
