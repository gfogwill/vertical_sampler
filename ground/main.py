import supervisor
supervisor.runtime.autoreload = False

import time
import busio
import board
import usb_cdc
from lora import LoRa
import address
import pack
import json

BOARD_GP_LORA_SCK = board.GP2
BOARD_GP_LORA_TX  = board.GP3
BOARD_GP_LORA_RX  = board.GP4

spi = busio.SPI(BOARD_GP_LORA_SCK, MOSI=BOARD_GP_LORA_TX, MISO=BOARD_GP_LORA_RX)

lora = LoRa(
    spi=spi,
    node=address.ground_rfm_address,
    destination=address.kenttarova_rfm_address
)

serial = usb_cdc.data

# Disable CircuitPython stdout on the data CDC port to avoid
# boot messages and echoes polluting the JSON stream.
supervisor.runtime.usb_cdc = False  # noqa: suppress REPL on data port

while True:
    # --- Incoming LoRa packet → forward as JSON to PC ---
    msg = lora.receive(timeout=1)
    if msg is not None:
        try:
            data = pack.bytes2dict(msg)
            serial.write((json.dumps(data) + "\n").encode())
        except Exception:
            # Forward raw bytes as hex so the PC always gets valid text
            serial.write('{"raw": "' + msg.hex() + '"}\n').encode())

    # --- Incoming command from PC → relay via LoRa ---
    if serial.in_waiting > 0:
        line = serial.readline()
        if line:
            cmd = line.decode(errors="ignore").strip()
            parts = cmd.split()
            if len(parts) >= 2:
                payload_id = parts[0]
                if payload_id == "kenttarova":
                    lora.set_destination(address.kenttarova_rfm_address)
                elif payload_id == "matorova":
                    lora.set_destination(address.matorova_rfm_address)
                lora.send(" ".join(parts[1:]).encode())
