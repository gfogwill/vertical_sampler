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

# Expected wire sizes — used for a fast pre-check before struct.unpack.
_WIRE_SIZE   = getattr(pack, "WIRE_SIZE",   None)
_LEGACY_SIZE = getattr(pack, "LEGACY_SIZE", None)


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
    """Try to deserialize a LoRa payload.  Returns dict or None.

    Prints a one-line diagnostic to stdout (visible in Thonny) if the
    packet fails to parse, so FORMAT mismatches are no longer silent.
    """
    if not isinstance(msg, (bytes, bytearray)):
        return None
    n = len(msg)
    # Fast pre-check: if we know the expected sizes, reject obviously wrong lengths.
    if _WIRE_SIZE is not None and _LEGACY_SIZE is not None:
        if n not in (_WIRE_SIZE, _LEGACY_SIZE):
            print("WARN pack: bad len={} (want {} or {})".format(n, _WIRE_SIZE, _LEGACY_SIZE))
            return None
    try:
        return pack.bytes2dict(msg)
    except Exception as e:
        print("WARN pack: parse failed len={} err={}".format(n, e))
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
            # parse error already printed above — keep waiting
            continue

        msg_type = d.get("msg_type", "")

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
                if not d.get("msg_type"):
                    d["msg_type"] = pack.MSG_TELEMETRY
                _print_json(d)
