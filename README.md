# Vertical Sampler

Firmware and ground station software for a balloon-borne vertical air sampler. The system uses two **Raspberry Pi Pico W** boards running **CircuitPython**, communicating over **LoRa (868 MHz)**.

## System Overview

```
┌─────────────────────────┐        LoRa 868 MHz        ┌─────────────────────────┐
│        PAYLOAD          │◄───────────────────────────►│    GROUND STATION       │
│  Raspberry Pi Pico W    │                             │  Raspberry Pi Pico W    │
│                         │                             │                         │
│  - GPS                  │                             │  - Receives telemetry   │
│  - Pressure sensor      │                             │  - Relays to PC via USB │
│  - RH/Temp sensor       │                             │  - Forwards commands    │
│  - 2x Pumps             │                             └─────────────────────────┘
│  - Electrovalve         │                                         │
│  - SD card logging      │                                    USB Serial
│  - Battery monitor      │                                         │
└─────────────────────────┘                             ┌─────────────────────────┐
                                                        │    Ground PC (cli.py)   │
                                                        │  Python 3 + pyserial    │
                                                        └─────────────────────────┘
```

## Repository Structure

```
vertical_sampler/
├── common/                   # Shared CircuitPython code (deployed to all payloads)
│   ├── address.py            # LoRa node addresses
│   ├── led.py                # LED blink helpers
│   ├── logging.py            # Logger class (writes to SD card)
│   ├── lora.py               # LoRa wrapper (adafruit_rfm9x)
│   ├── pack.py               # Binary packing for LoRa telemetry
│   ├── payload.py            # Main loop, sensors, actuators
│   ├── pressure_sensor.py    # LPS25H I2C driver
│   └── sdcard.py             # SD card mount + append logging
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

There are two payload units. Each has its own `main.py` with a unique `PAYLOAD_ID` and LoRa address. All other code (`common/`) is shared.

| Payload ID   | LoRa node address            |
|--------------|------------------------------|
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
#   adafruit-circuitpython-ntp
```

### 3. Deploy a payload

```bash
make update-kenttarova   # deploys common/ + payloads/kenttarova/main.py
make update-matorova    # deploys common/ + payloads/matorova/main.py
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

Each telemetry packet contains:

| Field | Type | Description |
|---|---|---|
| `gps_time` | uint32 | Elapsed seconds since boot |
| `gps_latitude` | float | Degrees |
| `gps_longitude` | float | Degrees |
| `gps_altitude` | float | Meters |
| `rh_sensor_humidity` | float | % RH |
| `rh_sensor_temperature` | float | °C |
| `pressure_sensor_pressure` | float | mbar |
| `pressure_sensor_temperature` | float | °C |
| `flow` | float | Flow meter value |
| `rssi` | int | LoRa RSSI |
| `battery_voltage` | float | Volts |
| `pump_front_state` | int | 0/1 |
| `pump_back_state` | int | 0/1 |
| `valve_state` | int | 0/1 |

Packets are transmitted as binary structs (see `common/pack.py`) over LoRa, and also logged as text to `/sd/log.txt` on the payload SD card.

## SD Card Logging

- Logs are written to `/sd/log.txt` in **append mode** — the file grows across boots.
- Each line: `YYYY-MM-DD HH:MM:SS - LEVEL - module - message`
- The SD card uses SPI (GP2/GP3/GP4) with CS on GP18.
- To verify SD is mounted after boot, open a REPL and run:
  ```python
  import os
  print(os.listdir("/sd"))
  ```

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
| Battery monitor | GP26 |
| Flow meter | GP28 |
