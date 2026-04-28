# Troubleshooting

## SD Card not mounting at boot

**Symptom:** `os.listdir("/sd")` raises `OSError: [Errno 2] No such file/directory` after reset, but works fine when `SDCard(spi, ...)` is called manually in the REPL.

**Cause:** `SDCard(...)` is not being called during the boot sequence — either `main()` is not running, or an exception is thrown before the SD mount.

**Fix:** Ensure `main.py` calls `SDCard(spi, fname="log.txt")` at the start of `main()`, before creating the logger. Verify:
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

---

## LoRa not initializing / SPI conflict

**Symptom:** `LoRa(...)` raises an exception, or the payload hangs on LoRa init.

**Cause:** LoRa and the SD card share the same SPI bus (GP2/GP3/GP4). SPI bus contention can occur if initialization order is wrong.

**Fix:** Always initialize `SDCard` before `LoRa` in `main()`.

---

## GPS not acquiring fix

**Symptom:** Logger shows `Waiting for GPS fix...` repeatedly.

**Fix:**
- Allow more time outdoors with a clear sky view (cold start can take 1–2 minutes).
- Increase `max_attempts` and `timeout` in `GPS.lat_lon_alt_time()`.
- Check UART wiring (GP0/GP1) and baud rate (9600).

---

## `log.txt` grows indefinitely

**Behavior (expected):** `log.txt` uses append mode and accumulates across all boots. This is intentional for flight data recovery.

**To clear the log:**
```python
import os
os.remove("/sd/log.txt")
```

---

## Payload boots but `/sd` does not appear

**Verify** the SD card is physically inserted and CS pin is GP18.

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

**Fix:** Make sure the ground station Pico W is connected via USB. On Linux:
```bash
sudo usermod -a -G dialout $USER
# then log out and back in
```
