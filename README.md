# Vertical Sampler

Firmware, hardware and host software for a balloon-borne vertical air sampler for Ice Nucleating Particle (INP) collection.

Payload and ground-station Raspberry Pi Pico W boards run **CircuitPython** and
communicate over **LoRa at 868 MHz**. The system supports the airborne payloads
`alma` and `beni`, plus the ground-operated payload `carla`.

## System Overview

```text
┌─────────────────────────┐        LoRa 868 MHz         ┌─────────────────────────┐
│        PAYLOAD          │◄───────────────────────────►│    GROUND STATION       │
│  Raspberry Pi Pico W    │                             │  Raspberry Pi Pico W    │
│                         │                             │                         │
│  - GPS (+ RTC sync)     │                             │  - Receives telemetry   │
│  - SHT85 RH/temp sensor │                             │  - Relays to PC via USB │
│  - LPS25H pressure      │                             │  - Forwards commands    │
│  - Pumps + optional     │                             └─────────────────────────┘
│    electrovalve / OPC   │
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
│   ├── alma_main.py             # Alma airborne payload entry point
│   ├── beni_main.py             # Beni airborne payload entry point
│   ├── carla_main.py            # Carla ground payload entry point
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

| Unit | LoRa node address | Hardware | Entry point |
|---|---:|---|---|
| Ground station | `0x47` | Serial/LoRa bridge | `firmware/ground_main.py` |
| Alma | `0x93` | Two pumps, electro-valve, OPC-N3 | `firmware/alma_main.py` |
| Beni | `0x71` | Two pumps, electro-valve, OPC-N3 | `firmware/beni_main.py` |
| Carla | `0x72` | Front pump only; no electro-valve or OPC-N3 | `firmware/carla_main.py` |

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
make update-alma
make update-beni
make update-carla
make update-ground
```

Each target:

1. Removes stale `code.py`.
2. Copies all `firmware/common/*.py` modules to the Pico.
3. Copies the selected entry point as `main.py`.

### 4. Control a payload

Run commands from the host computer:

```bash
python host/cli.py data alma
python host/cli.py pump alma front on
python host/cli.py pump beni back off
python host/cli.py valve beni on
python host/cli.py data carla
python host/cli.py pump carla front on
```

`host/quickview.py` is available for local data inspection and visualization.
It displays separate OPC-N3 histogram heatmaps for Alma and Beni. Carla is
included in the common sensor plots but has no OPC or electro-valve controls.

The host dashboards automatically use the latest sea-level pressure observation
from FMI station `Kittilä Matorova` (`fmisid=101985`) as QNH. The value is
refreshed every 10 minutes through the public
[FMI WFS API](https://opendata.fmi.fi/wfs). If FMI is unavailable, the last
fresh FMI value is retained and then the manual/default value of 1013.25 hPa
is used. Pass `--qnh VALUE` to select a different fallback, or
`--no-auto-qnh` to disable automatic updates. Logged monitor samples include
`_qnh_hpa`, `_qnh_source` and `_qnh_observation_time` so derived altitude
remains traceable.

## Telemetry Format

Each payload sample is logged as JSONL when an SD card is available and sent over LoRa as a packed binary packet defined in `firmware/common/pack.py`.

| Field | Type | Description |
|---|---|---|
| `msg_type` | str | `telemetry`, `cmd_ack` or `cmd_err` |
| `payload_id` | str | `alma`, `beni` or `carla` |
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
| `uplink_rssi` | int/null | Last ground-to-payload command RSSI measured by the payload, dBm |
| `downlink_rssi` | int/null | Current payload-to-ground packet RSSI measured by the ground station, dBm |
| `pump_front_state` | int | 0 or 1 |
| `pump_back_state` | int/null | 0 or 1; unavailable on Carla |
| `valve_state` | int/null | 0 or 1; unavailable on Carla |

The binary telemetry format remains identical for all payloads. Carla sends
fill values for the unavailable back pump, electro-valve and OPC-N3 fields.
Commands targeting those unavailable devices are rejected by both the host
CLI and Carla firmware.

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
