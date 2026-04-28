# Troubleshooting

## SD Card not mounting at boot

**Symptom:** `os.listdir("/sd")` raises `OSError: [Errno 2] No such file/directory` after reset, but works fine when `SDCard(spi, ...)` is called manually in the REPL.

**Cause:** `SDCard(...)` is not being called during the boot sequence — either `main()` is not running, or an exception is thrown before the SD mount.

**Fix:** Ensure `main.py` calls `SDCard(spi, fname="log.txt")` early inside `main()`, before creating the logger. Verify by checking:
```python
import os
print(os.listdir("/"))   # should include 'sd'
print(os.listdir("/sd")) # should include 'log.txt'
```

---

## Payload keeps resetting / autoreload loop

**Symptom:** The payload restarts every time a file is saved to `CIRCUITPY`, or seems to restart randomly during development.

**Cause:** CircuitPython's autoreload is enabled by default. Saving any file to the drive triggers a soft reset.

**Fix:** Add this at the very top of `main.py`, **before any other imports**:
```python
import supervisor
supervisor.runtime.autoreload = False
```
This must be the first two lines of the file. If placed after other imports, it may not take effect before a reload is triggered.

---

## LoRa not initializing / SPI conflict

**Symptom:** `LoRa(...)` raises an exception, or the payload hangs on LoRa init.

**Cause:** LoRa and the SD card share the same SPI bus (GP2/GP3/GP4). If the SD card is not properly initialized first, SPI bus contention can prevent LoRa from initializing.

**Fix:** Always initialize `SDCard` before `LoRa` in `main()`.

---

## GPS not acquiring fix

**Symptom:** Logger shows `Waiting for GPS fix...` repeatedly, eventually `GPS fix not acquired`.

**Cause:** Cold start, obstructed sky view, or insufficient timeout.

**Fix:**
- Allow more time outdoors with clear sky view (cold start can take 1–2 minutes).
- Increase `timeout` parameter in `GPS.lat_lon_alt_time(max_attempts=5, timeout=10)`.
- Check UART wiring (GP0/GP1) and baud rate (9600).

---

## `log.txt` grows indefinitely

**Behavior (expected):** `log.txt` is opened in append mode (`"a"`) every time a line is logged. It accumulates across all boots. This is intentional for flight data recovery.

**To clear the log:** Delete or rename `log.txt` from the SD card while the payload is off, or via REPL:
```python
import os
os.remove("/sd/log.txt")
```

---

## Payload boots but `/sd` does not appear in `os.listdir("/")`

**Verify the SD card is physically inserted** and that the CS pin is GP18.

**Test manually in REPL:**
```python
import board, busio
from sdcard import SDCard
spi = busio.SPI(board.GP2, MOSI=board.GP3, MISO=board.GP4)
sd = SDCard(spi, "log.txt")
import os
print(os.listdir("/sd"))
```
If this works but boot does not, the problem is in `main.py` initialization order.

---

## `cli.py` can't find the serial port

**Symptom:** `find_serial()` raises an error or returns None.

**Fix:** Make sure the ground station Pico W is connected via USB and recognized as a serial device. On Linux, it usually appears as `/dev/ttyACM0`. You may need to add your user to the `dialout` group:
```bash
sudo usermod -a -G dialout $USER
# then log out and back in
```
