import adafruit_rfm9x
import board
import busio
import digitalio
import time

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
        # Use the value cached by adafruit_rfm9x at receive() time.
        # Avoids a live SPI register read between packets, which is stale
        # anyway and adds unnecessary bus contention with the shared SD SPI.
        return self.rfm9x.last_rssi

    def set_destination(self, destination):
        self.rfm9x.destination = destination

    def reset_radio(self):
        """Hardware-reset the RFM9x via the RESET pin.

        Call this after an SPI error or unexpected silence to bring the
        module back to a known-good state.  Follows the SX1276 datasheet
        POR sequence: assert reset low for >= 100 us, then release and
        wait >= 5 ms for the internal oscillator to settle.

        Safe to call at any time; does NOT re-initialise the SPI bus or
        re-create the RFM9x object, so the existing spi/cs references
        remain valid.
        """
        try:
            self.reset.switch_to_output(value=False)  # assert RESET
            time.sleep(0.0002)                         # 200 us (> 100 us min)
            self.reset.value = True                    # release RESET
            time.sleep(0.010)                          # 10 ms settling (> 5 ms min)
        except Exception:
            pass  # best-effort; caller will detect failure on next send/receive
