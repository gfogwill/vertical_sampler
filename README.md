# Vertical Sampler

Firmware and ground station software for a balloon-borne vertical air sampler for Ice Nucleating Particle (INP) collection. The system uses two **Raspberry Pi Pico W** boards running **CircuitPython**, communicating over **LoRa (868 MHz)**.

## System Overview

```
┌─────────────────────────┐        LoRa 868 MHz         ┌─────────────────────────┐
│        PAYLOAD          │◄───────────────────────────►│    GROUND STATION       │
│  Raspberry Pi Pico W    │                             │  Raspberry Pi Pico W    │
│                         │                             │                         │
│  - GPS (+ RTC sync)     │                             │  - Receives telemetry   │
│  - Pressure sensor      │                             │  - Relays to PC via USB │
│  - RH/Temp sensor       │                             │  - Forwards commands    │
│  - 2x Pumps             │                             └─────────────────────────┘
│  - Electrovalve         │                                         │
│  - SD card logging      │                                    USB Serial
│  - Battery monitor      │                                         │
│  - Flow meter           │                             ┌─────────────────────────┐
│  - Watchdog timer       │                             │    Ground PC (cli.py)   │
└─────────────────────────┘                             │  Python 3 + pyserial    │
                                                        └─────────────────────────┘
```

## Repository Structure

```
vertical_sampler/
├── payload/                  # CircuitPython code deployed to payload units
│   ├── address.py            # LoRa node addresses
│   ├── led.py                # LED blink helpers
│   ├── logging.py            # Logger class (writes to SD card + console)
│   ├── lora.py               # LoRa wrapper (adafruit_rfm9x)
│   ├── pack.py               # Binary packing for LoRa telemetry
│   ├── payload.py            # Main loop, sensors, actuators, safety checks
│   ├── pressure_sensor.py    # LPS25H I2C driver
│   └── sdcard.py             # SD card mount + JSONL data + log file
├── payloads/
│   ├── kenttarova/
│   │   └── main.py           # Entry point for kenttarova unit
│   └── matorova/
│       └── main.py           # Entry point for matorova unit
├── ground/
│   └── main.py               # Ground station firmware
├── cli.py                    # Python 3 CLI for sending commands from PC
├── Makefile                  # Deploy helpers
└── docs/
    └── TROUBLESHOOTING.md
```

## Payloads

There are two payload units. Each has its own `main.py` with a unique `PAYLOAD_ID` and LoRa address. All other code (`payload/`) is shared.

| Payload ID   | LoRa node address               |
|--------------|---------------------------------|
| `kenttarova` | `kenttarova_rfm_address` (0x02) |
| `matorova`   | `matorova_rfm_address` (0x03)   |

## Quick Start

### 1. Flash CircuitPython

```bash
make download-circuitpython-image
# Then copy the .uf2 to the Pico W while in BOOTSEL mode
```

### 2. Install dependencies

```bash
make install-lora-deps
# Packages needed (install via Thonny or circup):
#   adafruit-circuitpython-rfm9x
#   adafruit-circuitpython-gps
```

### 3. Deploy a payload

```bash
make update-kenttarova   # deploys payload/ + payloads/kenttarova/main.py
make update-matorova    # deploys payload/ + payloads/matorova/main.py
```

### 4. Deploy ground station

```bash
make update-ground
```

### 5. Send commands from PC

```bash
python cli.py kenttarova pump front on
python cli.py kenttarova pump back off
python cli.py kenttarova valve on
python cli.py kenttarova data
python cli.py matorova pump front on
```

## Data Format

Each sample is stored as a JSON line in `/sd/<payload_id>_001.jsonl` (auto-incremented per boot). Fields:

| Field | Type | Description |
|---|---|---|
| `payload_id` | str | `"kenttarova"` or `"matorova"` |
| `rtc_time` | str | ISO 8601 timestamp from onboard RTC (`2020-01-01T...` until GPS syncs) |
| `gps_time` | float | Elapsed seconds since boot |
| `gps_latitude` | float\|null | Degrees (null if no fix) |
| `gps_longitude` | float\|null | Degrees (null if no fix) |
| `gps_altitude` | float\|null | Meters (null if no fix) |
| `rh_sensor_humidity` | float | % RH |
| `rh_sensor_temperature` | float | °C |
| `pressure_sensor_pressure` | float | mbar |
| `pressure_sensor_temperature` | float | °C |
| `battery_voltage` | float | Volts (6S Li-ion, calibrated) |
| `cpu_temperature` | float | Pico W internal temperature (°C) |
| `flow` | float | Flow meter (Std L/min) |
| `rssi` | int | LoRa RSSI |
| `pump_front_state` | int | 0/1 |
| `pump_back_state` | int | 0/1 |
| `valve_state` | int | 0/1 |

> **RTC sync:** The first time the GPS acquires a fix, the onboard RTC is synced to GPS UTC time. All subsequent `rtc_time` values will be real UTC timestamps even if the GPS later loses fix.

## SD Card Logging

Two files are created per session (auto-incremented, never overwritten):

| File | Content |
|---|---|
| `/sd/<id>_log.txt` | Human-readable log: `YYYY-MM-DD HH:MM:SS - LEVEL - module - message` |
| `/sd/<id>_001.jsonl` | One JSON object per line, one per sample cycle |

- The SD card uses SPI (GP2/GP3/GP4) with CS on GP18.
- **Graceful degradation:** if the SD card is absent or fails mid-flight, the system continues running and sending data over LoRa — it does not crash.
- To inspect SD contents from the Thonny REPL:

```python
import os, storage, board, busio, sdcardio
spi = busio.SPI(board.GP2, MOSI=board.GP3, MISO=board.GP4)
sdcard = sdcardio.SDCard(spi, board.GP18)
vfs = storage.VfsFat(sdcard)
storage.mount(vfs, "/sd")
print(os.listdir("/sd"))
with open("/sd/matorova_001.jsonl") as f:
    print(f.read())
```

## Safety Features

### Watchdog Timer
Armed at startup with a **30-second timeout**. If the main loop hangs (GPS block, I2C lockup, SD stall), the Pico W resets automatically. The watchdog is fed at the start of each cycle, inside the GPS wait loop, and inside the LoRa receive loop.

### Automatic Pump Cutoff
The pump is cut automatically under two conditions:

| Condition | Threshold | Action |
|---|---|---|
| Battery warning | ≤ 19.8 V (3.3 V × 6) | `WARNING` logged |
| Battery critical | ≤ 18.6 V (3.1 V × 6) | `ERROR` logged + pump off |
| CPU temp warning | ≥ 45 °C | `WARNING` logged |
| CPU temp critical | ≥ 55 °C | `ERROR` logged + pump off |

## Calibration

### Flow Meter (TSI 4121)
- Output: 0–4 V → 0–20 Std L/min
- Voltage divider on ADC input: 10 kΩ series + 32.6 kΩ to GND (ratio = 0.7652)
- Zero offset (`_FLOW_OFFSET_LMIN`): measure `flow` with pump off and set that value in `payload.py`

### Battery Monitor (6S Li-ion, 22.2 V nominal)
- Calibration factor `_BAT_CAL_FACTOR = 10.15` (measured: multimeter 24.82 V, raw ADC × 10 = 24.44 V)
- To recalibrate: connect battery, read `battery_voltage` from JSON, measure with multimeter, update factor = `multimeter_V / reported_V * current_factor`

## Pin Map (Payload)

| Function | GPIO |
|---|---|
| SPI SCK (LoRa + SD) | GP2 |
| SPI MOSI | GP3 |
| SPI MISO | GP4 |
| LoRa CS | GP5 |
| LoRa RESET | GP14 |
| SD CS | GP18 |
| GPS UART TX | GP0 |
| GPS UART RX | GP1 |
| I2C SDA (RH + pressure) | GP8 |
| I2C SCL (RH + pressure) | GP9 |
| Electrovalve | GP19 |
| Pump front | GP20 |
| Pump back | GP21 |
| Battery monitor | GP27 |
| Flow meter | GP28 |
