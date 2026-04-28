# Vertical Sampler

Firmware and ground station software for a balloon-borne vertical air sampler. The system uses two **Raspberry Pi Pico W** boards running **CircuitPython**, communicating over **LoRa (868 MHz)**.

## System Overview

```
┌─────────────────────────┐        LoRa 868 MHz        ┌─────────────────────────┐
│        PAYLOAD          │◄──────────────────────────►│    GROUND STATION       │
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
├── common/                   # CircuitPython modules shared by all payloads
│   ├── address.py            # LoRa node addresses
│   ├── led.py                # LED blink helpers
│   ├── logging.py            # Logger (writes to SD card)
│   ├── lora.py               # LoRa wrapper (adafruit_rfm9x)
│   ├── pack.py               # Binary struct packing for LoRa telemetry
│   ├── payload.py            # Main loop, sensor classes, actuator classes
│   ├── pressure_sensor.py    # LPS25H driver (I2C)
│   └── sdcard.py             # SD card mount + file write
├── payloads/
│   ├── kenttarova/
│   │   └── main.py           # Entry point for kenttarova payload
│   └── matorova/
│       └── main.py           # Entry point for matorova payload
├── ground/
│   └── main.py               # Ground station firmware (LoRa ↔ USB relay)
├── cli.py                    # Python 3 CLI for sending commands from PC
├── Makefile                  # Deploy helpers
└── docs/
    └── TROUBLESHOOTING.md
```

## Payloads

Each payload has its own `main.py` with a unique `PAYLOAD_ID` and LoRa address. All other code is shared from `common/`.

| Payload ID   | LoRa address (`address.py`) |
|--------------|-----------------------------|
| `kenttarova` | `0x02`                      |
| `matorova`   | `0x03`                      |
| ground       | `0x01`                      |

## Quick Start

### 1. Flash CircuitPython

```bash
make download-circuitpython-image
# Copy the .uf2 to the Pico W while in BOOTSEL mode
```

### 2. Install CircuitPython dependencies

```bash
make install-lora-deps
# Packages needed (via Thonny or circup):
#   adafruit-circuitpython-rfm9x
#   adafruit-circuitpython-gps
#   adafruit-circuitpython-ntp
```

### 3. Deploy payload firmware

```bash
make update-kenttarova   # or: make update-matorova
```

This copies all files from `common/` + `payloads/kenttarova/main.py` to `CIRCUITPY`.

### 4. Deploy ground station firmware

```bash
make update-ground
```

### 5. Send commands from PC

```bash
# Control pumps
python cli.py kenttarova pump front on
python cli.py kenttarova pump back off
python cli.py kenttarova pump both on

# Control electrovalve
python cli.py kenttarova valve on

# Request telemetry data
python cli.py kenttarova data
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
- Each line format: `YYYY-MM-DD HH:MM:SS - LEVEL - module - message`
- The SD card uses SPI (GP2/GP3/GP4) with CS on GP18.
- To verify the SD is mounted after boot, open a REPL and run:
  ```python
  import os
  print(os.listdir("/sd"))
  ```

## Pin Map (Payload Pico W)

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
