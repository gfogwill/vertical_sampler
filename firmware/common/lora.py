import adafruit_rfm9x
import digitalio
import time
import config

class LoRa:
    def __init__(self, spi, node, destination, shared_spi=None):
        self.shared_spi = shared_spi
        self.cs = digitalio.DigitalInOut(config.LORA_CS)
        self.reset = digitalio.DigitalInOut(config.LORA_RESET_DUMMY)
        self._before_lora()
        self.rfm9x = adafruit_rfm9x.RFM9x(spi=spi, cs=self.cs, reset=self.reset, frequency=868)
        self.rfm9x.node = node
        self.rfm9x.destination = destination
    def _before_lora(self):
        if self.shared_spi is not None:
            self.shared_spi.before_lora()
    def send(self, msg):
        self._before_lora()
        return self.rfm9x.send(msg)
    def receive(self, timeout=1):
        self._before_lora()
        return self.rfm9x.receive(timeout=timeout)
    def rssi(self): return self.rfm9x.last_rssi
    def set_destination(self, destination): self.rfm9x.destination = destination
    def reset_radio(self):
        # PCB v1 does not route RFM9x RST; recover logically only.
        try:
            self._before_lora()
            self.rfm9x.idle()
            time.sleep(0.01)
        except Exception:
            pass
