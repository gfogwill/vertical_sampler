"""Hardware defaults for the vertical sampler.

All board-specific assignments live here. Keep these values aligned with
the deployed wiring; profile-specific overrides can be added later without
changing drivers or the mission loop.
"""

import board

# GPIO assignments
RH_SDA = board.GP8
RH_SCL = board.GP9
GPS_UART_TX = board.GP0
GPS_UART_RX = board.GP1
LORA_CS = board.GP5
LORA_RESET = board.GP14
SD_CS = board.GP18
ELECTROVALVE = board.GP19
PUMP_FRONT = board.GP20
PUMP_BACK = board.GP21
BATTERY_MONITOR = board.GP27
FLOWMETER = board.GP28

# Instrument calibration
FLOW_DIVIDER_RATIO = 32.6 / (10.0 + 32.6)
FLOW_FULL_SCALE_V = 4.0
FLOW_FULL_SCALE_LMIN = 20.0
FLOW_OFFSET_LMIN = 0.25
BATTERY_CAL_FACTOR = 10.15

# Operational limits
BATTERY_WARN_V = 19.8
BATTERY_CUTOFF_V = 18.6
CPU_TEMP_WARN_C = 45.0
CPU_TEMP_CRITICAL_C = 55.0
WATCHDOG_TIMEOUT_S = 30
HEARTBEAT_INTERVAL_S = 60

# Existing units retain their current timing. Add the third payload here when
# its identifier is assigned.
HEARTBEAT_OFFSETS = {
    "matorova": 0,
    "kenttarova": 30,
}
