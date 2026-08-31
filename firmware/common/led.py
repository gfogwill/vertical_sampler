import time
import board
import digitalio


LED = digitalio.DigitalInOut(board.LED)
LED.direction = digitalio.Direction.OUTPUT
LED.value = False


def toggle_led():
    LED.value = not LED.value


def blink(ntimes=1, tsleep=0.2, bsleep=0.1, esleep=0.1):
    initial = LED.value
    LED.value = False
    time.sleep(bsleep)
    for _ in range(ntimes):
        LED.value = True
        time.sleep(tsleep)
        LED.value = False
        time.sleep(tsleep)
    time.sleep(esleep)
    LED.value = initial


class StatusLed:
    ALIVE_INTERVAL_S = 2.0
    ALIVE_ON_S = 0.05
    PULSE_ON_S = 0.08
    PULSE_OFF_S = 0.08

    def __init__(self, logger=None):
        self._logger = logger
        self._queue = []
        self._mode = "idle"
        self._remaining = 0
        self._next_at = time.monotonic()
        self._next_alive = self._next_at + self.ALIVE_INTERVAL_S

    def event(self, pulses):
        self._queue.append(pulses)

    def rx(self):
        self.event(1)

    def tx(self):
        self.event(2)

    def sensors_updated(self):
        self.event(3)

    def error(self):
        self.event(5)

    def tick(self, now=None):
        if now is None:
            now = time.monotonic()
        if now < self._next_at:
            return
        if self._mode == "alive":
            LED.value = False
            self._mode = "idle"
            return
        if self._mode == "pulse":
            if LED.value:
                LED.value = False
                self._remaining -= 1
                self._next_at = now + self.PULSE_OFF_S
                if self._remaining == 0:
                    self._mode = "idle"
                    self._next_alive = now + self.ALIVE_INTERVAL_S
            else:
                LED.value = True
                self._next_at = now + self.PULSE_ON_S
            return
        if self._queue:
            self._remaining = self._queue.pop(0)
            self._mode = "pulse"
            LED.value = True
            self._next_at = now + self.PULSE_ON_S
            return
        if now >= self._next_alive:
            LED.value = True
            self._mode = "alive"
            self._next_at = now + self.ALIVE_ON_S
            self._next_alive = now + self.ALIVE_INTERVAL_S
