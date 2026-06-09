import supervisor
supervisor.runtime.autoreload = False

import json
import select
import sys
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

lora = LoRa(
    spi=spi,
    node=address.ground_rfm_address,
    destination=None,
)

POLL = select.poll()
POLL.register(sys.stdin, 1)


class UnexpectedCommand(Exception):
    pass


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

    lora.send(" ".join(cmd).encode())

    msg = None
    for _ in range(10):
        msg = lora.receive(timeout=2)
        if msg is not None:
            break

    if isinstance(msg, (bytes, bytearray)):
        led.blink(ntimes=6, bsleep=0.4, tsleep=0.2, esleep=0.4)
        try:
            d = pack.bytes2dict(msg)
            print(json.dumps(d))
        except Exception:
            print(msg.decode(errors="ignore").strip())
    elif msg is None:
        print('{"error": "no response"}')


while True:
    led.blink(ntimes=3, bsleep=0.1, tsleep=0.1, esleep=0.1)
    if POLL.poll(0):
        cmd_str = sys.stdin.readline().strip().lower()
        # Skip empty lines and JSON echoes
        if not cmd_str or cmd_str.startswith("{"):
            continue
        try:
            _process_command(cmd_str)
        except UnexpectedCommand as e:
            print('{"error": "unexpected command: ' + str(e) + '"}')
        except Exception as e:
            err = str(e).replace('"', "'").replace("\n", " ")
            print('{"error": "' + err + '"}')
