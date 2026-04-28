import supervisor
supervisor.runtime.autoreload = False

import time
import busio
import board
import usb_cdc
from lora import LoRa
import address
import pack

BOARD_GP_LORA_SCK = board.GP2
BOARD_GP_LORA_TX = board.GP3
BOARD_GP_LORA_RX = board.GP4

spi = busio.SPI(BOARD_GP_LORA_SCK, MOSI=BOARD_GP_LORA_TX, MISO=BOARD_GP_LORA_RX)

lora = LoRa(
    spi=spi,
    node=address.ground_rfm_address,
    destination=address.kenttarova_rfm_address
)

serial = usb_cdc.data

while True:
    # Check for incoming LoRa messages from payload
    msg = lora.receive(timeout=1)
    if msg is not None:
        try:
            data = pack.bytes2dict(msg)
            import json
            serial.write((json.dumps(data) + "\n").encode())
        except Exception:
            serial.write(msg + b"\n")

    # Check for commands from PC over USB serial
    if serial.in_waiting > 0:
        line = serial.readline()
        if line:
            cmd = line.decode().strip()
            parts = cmd.split()
            if len(parts) >= 2:
                payload_id = parts[0]
                # Route to the right destination
                if payload_id == "kenttarova":
                    lora.set_destination(address.kenttarova_rfm_address)
                elif payload_id == "matorova":
                    lora.set_destination(address.matorova_rfm_address)
                # Forward the rest of the command
                lora.send(" ".join(parts[1:]).encode())
