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

For bench testing, the Alma or Beni payload can also accept actuator commands
directly over its own USB connection. Deploy the updated payload firmware first,
then run the USB helper from the laptop:

```bash
python host/usb_payload.py --port /dev/ttyACM0 pump front on
python host/usb_payload.py --port /dev/ttyACM0 data
python host/usb_payload.py --port /dev/ttyACM0 pump front off
```

The `--port` option can be omitted when exactly one Pico serial device is
connected. The helper waits for a JSON acknowledgement and exits; the pump
state is not tied to the helper process or USB connection. Alma/Beni must
remain separately powered after the laptop is disconnected, and the normal
battery/temperature safety interlock still applies. The direct USB path
accepts `pump front|back|both on|off`, `valve on|off`, and `data`.

Scheduled commands can be sent from a text file using UTC ISO-8601
timestamps. The command after each timestamp uses the same syntax as the
one-shot CLI commands:

```text
# timestamp command payload [location] state
2026-09-26T12:00:00Z pump alma front on
2026-09-26T12:30:00Z valve beni off
2026-09-26T13:00:00+00:00 pump alma front off
```

An editable example is provided in
[`sampling_schedule.example.txt`](sampling_schedule.example.txt). Copy it
to a working schedule, then change and review the timestamps before running:

```bash
cp sampling_schedule.example.txt sampling_schedule.txt
python host/cli.py schedule sampling_schedule.txt --dry-run
```

Run the schedule with:

```bash
python host/cli.py schedule sampling_schedule.txt
```

To run the schedule together with the monitor and JSON telemetry logging,
use the monitor mode so both features share one serial connection:

```bash
python host/cli.py monitor \
  --schedule sampling_schedule.txt \
  --json-output telemetry.jsonl
```

`--json-output` is an alias for `--log-file`. Add
`--schedule-run-past-due` if entries from before startup should be executed
immediately.

Entries that are already past when the scheduler starts are skipped by
default. Use `--run-past-due` to execute them immediately, or
`--dry-run` to validate and print the schedule without sending commands.
For scheduled actuator commands, the host by default waits 5 seconds after
each send, requests fresh payload telemetry, and checks the requested pump or
valve state. If the state is not confirmed, the command is sent again, up to
three total attempts. A command that was applied but whose acknowledgement was
lost is not resent when the later state check confirms the requested state.

The standalone scheduler accepts `--verify-delay SECONDS` and
`--max-attempts COUNT`. Monitor schedules use the corresponding
`--schedule-verify-delay` and `--schedule-max-attempts` options. These options
apply only to pump and valve commands; `data` entries retain their existing
telemetry retry behavior.

`host/quickview.py` is available for local data inspection and visualization.
It displays separate Alma and Beni OPC-N3 heatmaps using the standard
0.35–40 µm diameter bins on a logarithmic diameter axis. Heatmap colors use
logarithmic normalization and are corrected to counts per logarithmic diameter
interval (`dN/dlog10(Dp)`). New telemetry includes the OPC histogram sample
period, so QuickView and the plotting scripts normalize distributions to
particles/cm³. Older logs without that field remain relative unless a known
period is passed explicitly, for example:

```bash
python host/quickview.py --log-file ground_dump.jsonl \
    --opc-sampling-period-s <seconds>
```
All shared time axes are explicitly formatted in UTC. QuickView prefers the
payload UTC/RTC timestamp, then the host log timestamp; when a payload has no
valid absolute clock, its `monotonic_s` intervals are retained instead of
assigning one artificial second per log line.
Carla is included in the common sensor plots but has no OPC or electro-valve
controls.

For daily low-cloud measurement planning at Matorova, install the weather
dashboard dependencies and launch:

```bash
python -m pip install -r host/requirements-weather.txt
python host/weather_dashboard.py
```

The dashboard refreshes every 10 minutes and combines:

- ECMWF IFS boundary-layer height, retrieved through Open-Meteo.
- Altitude-resolved cloud fraction from Cloudnet's preferred forecast model
  for Kenttärova (normally MEPS), with liquid, ice and precipitating
  hydrometeor contours.
- Near-real-time cloud-base and cloud-top observations from ACTRIS Cloudnet at
  Kenttärova, visualized as Cloudnet target classifications for liquid,
  drizzle/rain, ice, mixed-phase and melting particles.
- The latest Kenttärova HATPRO microwave-radiometer temperature and relative
  humidity profile, including potential temperature for visually identifying
  stable layers and likely boundary-layer tops.
- The latest available 00 or 12 UTC Sodankylä radiosonde profile from the
  University of Wyoming archive, including low-level horizontal wind-speed
  and wind-direction lines with meteorological wind barbs at 100 m intervals.
  Half barbs represent 2.5 m/s, full barbs 5 m/s, and flags 25 m/s.

Cloudnet NetCDF files are cached under
`~/.cache/vertical_sampler/weather`, and are downloaded again only when their
portal metadata indicates an update. The large HATPRO file is refreshed at
most once per hour, and cached files older than seven days are removed. The
plots focus on the lowest 2 km above ground. Use `--refresh-minutes 0` for a static view or
`--output matorova-weather.png` to generate a PNG without opening a window.

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
| `sd_card_available` | int/null | 1 when the payload mounted the SD card successfully; 0 when mounting was unavailable; older packets report unavailable |
| `flow` | float | Standard L/min |
| `uplink_rssi` | int/null | Last ground-to-payload command RSSI measured by the payload, dBm |
| `downlink_rssi` | int/null | Current payload-to-ground packet RSSI measured by the ground station, dBm |
| `pump_front_state` | int | 0 or 1 |
| `pump_back_state` | int/null | 0 or 1; unavailable on Carla |
| `valve_state` | int/null | 0 or 1; unavailable on Carla |
| `opc_bin_0` ... `opc_bin_23` | uint16/null | Raw OPC-N3 particle counts per histogram bin |
| `opc_temperature` | float/null | OPC temperature, °C |
| `opc_humidity` | float/null | OPC relative humidity, %RH |
| `opc_sample_flow` | float/null | OPC sample flow, mL/s |
| `opc_sampling_period_s` | float/null | OPC histogram sampling period, s |
| `opc_laser_status` | int/null | OPC laser status |

For a histogram with a valid flow and sampling period, the instrument-reported
number concentration for each bin is:

```text
particles/cm³ = raw_bin_count / (opc_sample_flow * opc_sampling_period_s)
```

The conversion uses the measured per-histogram sample volume; it is a number
concentration reported by the OPC, not a mass concentration or a correction
for optical efficiency, coincidence losses, inlet losses, humidity, or particle
refractive index. Historical logs that predate `opc_sampling_period_s` can be
plotted with `--opc-sampling-period-s <seconds>` only when that period is known.

The current binary telemetry format is shared by all payloads. The ground
station also accepts the immediately preceding packet format and legacy
pre-OPC packets; those formats decode `opc_sampling_period_s` and
`sd_card_available` as unavailable. In `cli.py monitor`, the System section
shows this field as `YES` or `NO`.
Carla sends fill values for the unavailable back pump, electro-valve and OPC-N3
fields.
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

The `sd_card_available` telemetry field reports whether the card mounted
successfully and was available to the logger. It is not a hot-plug sensor:
removing or inserting a card after boot is not reported until the payload is
restarted and mounts the card again.

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
