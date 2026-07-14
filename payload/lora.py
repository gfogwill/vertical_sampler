import adafruit_rfm9x
import board
import busio
import digitalio

BOARD_GP_LORA_CS = board.GP5
BOARD_GP_LORA_RESET = board.GP14


class LoRa:
    def __init__(self, spi, node, destination):
        self.cs = digitalio.DigitalInOut(BOARD_GP_LORA_CS)
        self.reset = digitalio.DigitalInOut(BOARD_GP_LORA_RESET)
        self.rfm9x = adafruit_rfm9x.RFM9x(
            spi=spi,
            cs=self.cs,
            reset=self.reset,
            frequency=868,
        )
        self.rfm9x.node = node
        self.rfm9x.destination = destination

    def send(self, msg: bytes) -> bool:
        return self.rfm9x.send(msg)

    def receive(self, timeout=1):
        return self.rfm9x.receive(timeout=timeout)

    def rssi(self):
        return self.rfm9x.rssi

    def set_destination(self, destination):
        self.rfm9x.destination = destination
