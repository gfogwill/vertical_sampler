import time
import board
import digitalio
import math

LED = digitalio.DigitalInOut(board.LED)
LED.direction = digitalio.Direction.OUTPUT


def toggle_led():
    LED.value = not LED.value


def blink_sin():
    init_val = LED.value
    steps = 100
    c = 0.1
    for i in range(steps):
        tsleep = c * math.fabs(math.sin(i * 2 * math.pi / steps))
        LED.value = True
        time.sleep(tsleep)
        LED.value = False
        time.sleep(tsleep)
    LED.value = init_val


def blink(ntimes=1, tsleep=0.2, bsleep=0.1, esleep=0.1):
    init_val = LED.value
    LED.value = False
    time.sleep(bsleep)
    for _ in range(ntimes):
        LED.value = True
        time.sleep(tsleep)
        toggle_led()
        time.sleep(tsleep)
    time.sleep(esleep)
    LED.value = init_val
