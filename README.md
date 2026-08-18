# Vertical Sampler

Firmware, hardware and host software for a balloon-borne vertical air sampler for Ice Nucleating Particle (INP) collection.

Payload and ground-station Raspberry Pi Pico W boards run **CircuitPython** and communicate over **LoRa at 868 MHz**. The system supports two payload identities: `kenttarova` and `matorova`.

## System Overview

```text
┌─────────────────────────┐        LoRa 868 MHz         ┌─────────────────────────┐
│        PAYLOAD          │◄───────────────────────────►│    GROUND STATION       │
│  Raspberry Pi Pico W    │                             │  Raspberry Pi Pico W    │
│                         │                             │                         │
│  - GPS (+ RTC sync)     │                             │  - Receives telemetry   │
│  - SHT85 RH/temp sensor │                             │  - Relays to PC via USB │
│  - LPS25H pressure      │                             │  - Forwards commands    │
│  - Pumps + electrovalve │                             └─────────────────────────┘
│  - SD logging           │                                         │
│  - Battery + flow meter │                                    USB Serial
│  - Watchdog             │                                         │
└─────────────────────────┘                             ┌─────────────────────────┐
                                                        │       Host computer     │
                                                        │ host/cli.py             │
                                                        │ host/quickview.py       │
                                                        └─────────────────────────┘
```

## Repository Structure

```text
vertical_sampler/
├── firmware/                    # CircuitPython code deployed to Pico boards
│   ├── common/                  # Copied flat to every CIRCUITPY drive
│   │   ├── config.py            # PCB v1 GPIOs, calibration, limits, LoRa addresses
│   │   ├── payload.py           # Active payload drivers and mission loop
│   │   ├── lora.py              # RFM9x wrapper
│   │   ├── pressure_sensor.py   # LPS25H driver
│   │   ├── sdcard.py            # SD mount, JSONL data and log writes
│   │   ├── logging.py
│   │   ├── pack.py              # Binary LoRa telemetry format
│   │   ├── led.py
│   │   ├── adafruit_gps.py      # Vendored CircuitPython dependency
│   │   └── adafruit_rfm9x.py    # Vendored CircuitPython dependency
│   ├── kenttarova_main.py       # kenttarova payload entry point
│   ├── matorova_main.py         # matorova payload entry point
│   └── ground_main.py           # Ground-station entry point
├── host/                        # Python 3 programs running on the control PC
│   ├── cli.py
│   └── quickview.py
├── pcb/                         # KiCad PCB v1 project
├── docs/
│   └── TROUBLESHOOTING.md
├── Makefile
└── README.md
```

`firmware/common/` is copied as flat modules to the root of `CIRCUITPY`. CircuitPython imports therefore remain simple:

```python
import config
import lora
import pack
```

## Payload Identities

| Unit | LoRa node address | Entry point |
|---|---:|---|
| Ground station | `0x47` | `firmware/ground_main.py` |
| kenttarova | `0x71` | `firmware/kenttarova_main.py` |
| matorova | `0x93` | `firmware/matorova_main.py` |

Addresses, GPIO assignments, calibration constants and safety limits are defined centrally in `firmware/common/config.py`.

## Quick Start

### 1. Flash CircuitPython

```bash
make download-circuitpython-image
```

Copy the resulting `.uf2` to the Pico W while it is in BOOTSEL mode.

### 2. Install dependencies

The repository vendors `adafruit_gps.py` and `adafruit_rfm9x.py`. Install the remaining required CircuitPython libraries in the Pico `lib/` directory, including the dependencies required by the RFM9x driver such as `adafruit_bus_device`.

### 3. Deploy firmware

Set the CircuitPython mount point if needed:

```bash
export CIRCUITPY_PATH=/media/$USER/CIRCUITPY
```

Deploy the required device image:

```bash
make update-kenttarova
make update-matorova
make update-ground
```

Each target:

1. Removes stale `code.py`.
2. Copies all `firmware/common/*.py` modules to the Pico.
3. Copies the selected entry point as `main.py`.

### 4. Control a payload

Run commands from the host computer:

```bash
python host/cli.py kenttarova data
python host/cli.py kenttarova pump front on
python host/cli.py kenttarova pump back off
python host/cli.py kenttarova valve on
python host/cli.py matorova data
```

`host/quickview.py` is available for local data inspection and visualization.

## Telemetry Format

Each payload sample is logged as JSONL when an SD card is available and sent over LoRa as a packed binary packet defined in `firmware/common/pack.py`.

| Field | Type | Description |
|---|---|---|
| `msg_type` | str | `telemetry`, `cmd_ack` or `cmd_err` |
| `payload_id` | str | `kenttarova` or `matorova` |
| `rtc_time` | str | RTC timestamp in ISO 8601 format |
| `gps_time` | uint32/null | GPS UTC Unix epoch |
| `gps_latitude` | float/null | Degrees |
| `gps_longitude` | float/null | Degrees |
| `gps_altitude` | float/null | Metres |
| `rh_sensor_humidity` | float | Relative humidity, % |
| `rh_sensor_temperature` | float | Temperature, °C |
| `pressure_sensor_pressure` | float | Pressure, mbar |
| `pressure_sensor_temperature` | float | Temperature, °C |
| `battery_voltage` | float | Calibrated 6S battery voltage |
| `cpu_temperature` | float | Pico internal temperature, °C |
| `flow` | float | Standard L/min |
| `rssi` | int | Last received LoRa RSSI |
| `pump_front_state` | int | 0 or 1 |
| `pump_back_state` | int | 0 or 1 |
| `valve_state` | int | 0 or 1 |

> **RTC synchronization:** on the first valid GPS fix, the payload sets the onboard RTC to GPS UTC. Subsequent `rtc_time` values remain valid even if the GPS temporarily loses its fix.

## SD Logging

The SD-card handler writes:

| File | Content |
|---|---|
| `/sd/<payload_id>_log.txt` | Human-readable events and diagnostics |
| `/sd/<payload_id>_NNN.jsonl` | One JSON object per sample cycle |

If the SD card is unavailable or fails while operating, the payload continues running and transmitting telemetry over LoRa. Logging degrades to the serial console instead of stopping the mission.

## Safety Features

| Condition | Threshold | Action |
|---|---:|---|
| Battery warning | ≤ 19.8 V | Warning logged |
| Battery critical | ≤ 18.6 V | Pumps off, valve off, error logged |
| CPU temperature warning | ≥ 45 °C | Warning logged |
| CPU temperature critical | ≥ 55 °C | Pumps off, valve off, error logged |
| Main-loop stall | 30 s watchdog timeout | Pico reset |

The watchdog is fed in the main loop, GPS wait loop, LoRa receive loop and failure-report loops.

## PCB v1 Pin Map

| Function | Pico GPIO |
|---|---:|
| GPS UART TX/RX | GP0 / GP1 |
| I2C SDA/SCL | GP2 / GP3 |
| Pump front | GP6 |
| Electrovalve | GP7 |
| SD chip select | GP9 |
| SPI SCK/MOSI/MISO | GP10 / GP11 / GP12 |
| LoRa chip select | GP16 |
| OPC chip select (reserved) | GP17 |
| Pump back | GP21 |
| Battery monitor ADC | GP27 |
| Flowmeter ADC | GP28 |

The RFM9x reset line is not routed on PCB v1. `config.LORA_RESET_DUMMY` remains because the CircuitPython RFM9x driver requires a reset-pin argument.

## Calibration

### Flowmeter

The TSI 4121 flowmeter is configured as:

- Signal range: 0–4 V corresponding to 0–20 Std L/min.
- ADC divider: 10 kΩ series resistor and 32.6 kΩ to ground.
- Divider ratio: \(32.6 / (10.0 + 32.6)\).
- Zero offset: `FLOW_OFFSET_LMIN` in `firmware/common/config.py`.

### Battery monitor

`BATTERY_CAL_FACTOR` in `firmware/common/config.py` converts the Pico ADC voltage to the measured 6S battery voltage. Recalibrate against a multimeter if the divider or analog front-end changes.

## Development Notes

- `firmware/common/payload.py` contains the active, hardware-validated payload drivers and mission loop.
- `firmware/common/config.py` is the single source of truth for PCB pins, calibration, operating limits and LoRa addresses.
- The old breadboard firmware is preserved in the `old_hardware` branch.
- See `docs/TROUBLESHOOTING.md` for deployment and communication diagnostics.
