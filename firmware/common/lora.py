import adafruit_rfm9x
import digitalio
import time
import config


class LoRa:
    def __init__(self, spi, node, destination, shared_spi=None, logger=None):
        self.shared_spi = shared_spi
        self._logger = logger
        if self.shared_spi is not None:
            self.cs = self.shared_spi.lora_cs
        else:
            self.cs = digitalio.DigitalInOut(config.LORA_CS)
        self.reset = digitalio.DigitalInOut(config.LORA_RESET_DUMMY)
        self._before_lora()
        self.rfm9x = self._init_rfm9x(spi, self.cs, self.reset, 868)
        self.rfm9x.node = node
        self.rfm9x.destination = destination

    def _init_rfm9x(self, spi, cs, reset, frequency, retries=5, delay=0.2):
        # PCB v1 does not route RFM9x RST, so version-register detection can
        # fail intermittently on an SPI glitch. Retry a few times before
        # giving up instead of failing on the first attempt.
        last_error = None
        for attempt in range(retries):
            try:
                self._before_lora()
                return adafruit_rfm9x.RFM9x(spi=spi, cs=cs, reset=reset, frequency=frequency)
            except RuntimeError as exc:
                last_error = exc
                if self._logger is not None:
                    self._logger.warning(
                        "RFM9x init attempt {}/{} failed: {}".format(attempt + 1, retries, exc)
                    )
                time.sleep(delay)
        raise last_error

    def _before_lora(self):
        if self.shared_spi is not None:
            self.shared_spi.before_lora()

    def send(self, msg):
        self._before_lora()
        return self.rfm9x.send(msg)

    def receive(self, timeout=1):
        self._before_lora()
        return self.rfm9x.receive(timeout=timeout)

    def receive_with_rssi(self, timeout=1):
        msg = self.receive(timeout=timeout)
        return msg, self.rssi()

    def rssi(self):
        return self.rfm9x.last_rssi

    def set_destination(self, destination):
        self.rfm9x.destination = destination

    def reset_radio(self):
        # PCB v1 does not route RFM9x RST; recover logically only.
        try:
            self._before_lora()
            self.rfm9x.idle()
            time.sleep(0.01)
        except Exception:
            pass
