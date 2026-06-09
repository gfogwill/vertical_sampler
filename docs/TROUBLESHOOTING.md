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

---

## `cp: error writing ... No space left on device` after flashing UF2

**Symptom:** `make update-*` fails with:
```
cp: error writing '/media/.../CIRCUITPY/adafruit_gps.py': No space left on device
```
This happens even immediately after flashing a fresh UF2.

**Cause:** CircuitPython's `CIRCUITPY` drive has a fixed filesystem size baked into the UF2. By default it is **~1 MB**, which fills up quickly with libraries. Flashing the UF2 again does **not** wipe or resize the filesystem — it only replaces the firmware, leaving the old filesystem intact.

**Fix — wipe and resize the filesystem:**

1. **Enter bootloader mode:** hold BOOTSEL while plugging in USB (or double-tap RESET). The drive `RPI-RP2` appears.

2. **Flash the CircuitPython UF2** (if not already done): drag the `.uf2` onto `RPI-RP2`.

3. **Wipe the filesystem from the REPL.** Connect via serial (Thonny or `screen /dev/ttyACM0 115200`) and run:
   ```python
   import storage
   storage.erase_filesystem()
   ```
   The board will reboot and format a **fresh, empty** `CIRCUITPY` filesystem.

4. **Run `make update-*` again.** The drive is now empty and should have enough space.

**Prevention:** If the drive keeps filling up, remove unused `.py` or `.mpy` files. Pre-compiled `.mpy` files (from the Adafruit CircuitPython Bundle) are significantly smaller than `.py` sources — use them for large libraries like `adafruit_gps`.

**Check available space from REPL:**
```python
import os
s = os.statvfs("/")
free_kb = s[0] * s[3] / 1024
print("Free:", free_kb, "KB")
```
