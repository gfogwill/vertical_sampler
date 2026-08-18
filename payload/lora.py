import adafruit_rfm9x
import digitalio
import time
import config

class LoRa:
    def __init__(self, spi, node, destination):
        self.cs = digitalio.DigitalInOut(config.LORA_CS)
        self.reset = digitalio.DigitalInOut(config.LORA_RESET_DUMMY)
        self.rfm9x = adafruit_rfm9x.RFM9x(spi=spi, cs=self.cs, reset=self.reset, frequency=868)
        self.rfm9x.node = node
        self.rfm9x.destination = destination
    def send(self, msg): return self.rfm9x.send(msg)
    def receive(self, timeout=1): return self.rfm9x.receive(timeout=timeout)
    def rssi(self): return self.rfm9x.last_rssi
    def set_destination(self, destination): self.rfm9x.destination = destination
    def reset_radio(self):
        # PCB v1 does not route RFM9x RST; recover logically only.
        try:
            self.rfm9x.idle()
            time.sleep(0.01)
        except Exception:
            pass
