"""Pin assignment and operating constants for PCB v1.

This is the single source of truth for firmware GPIO assignments.
"""

import board

GPS_UART_TX = board.GP0
GPS_UART_RX = board.GP1
I2C_SDA = board.GP2
I2C_SCL = board.GP3
PUMP_FRONT = board.GP6
ELECTROVALVE = board.GP7
SD_CS = board.GP9
SPI_SCK = board.GP10
SPI_MOSI = board.GP11
SPI_MISO = board.GP12
LORA_CS = board.GP16
OPC_CS = board.GP17
PUMP_BACK = board.GP21
BATTERY_MONITOR = board.GP27
FLOWMETER = board.GP28

# RFM9x RST is not routed on PCB v1. This unconnected pin is retained only
# because the CircuitPython driver requires a DigitalInOut reset argument.
LORA_RESET_DUMMY = board.GP14

# LoRa node addresses
GROUND_RFM_ADDRESS = 0x47
KENTTAROVA_RFM_ADDRESS = 0x71
MATOROVA_RFM_ADDRESS = 0x93

FLOW_DIVIDER_RATIO = 32.6 / (10.0 + 32.6)
FLOW_FULL_SCALE_V = 4.0
FLOW_FULL_SCALE_LMIN = 20.0
FLOW_OFFSET_LMIN = 0.25
BATTERY_CAL_FACTOR = 10.15
BATTERY_WARN_V = 19.8
BATTERY_CUTOFF_V = 18.6
CPU_TEMP_WARN_C = 45.0
CPU_TEMP_CRITICAL_C = 55.0
WATCHDOG_TIMEOUT_S = 30
HEARTBEAT_INTERVAL_S = 60
HEARTBEAT_OFFSETS = {
    "matorova": 0,
    "kenttarova": 30,
}

# Log levels persisted to SD. INFO/DEBUG are printed to console only, to
# minimize SD write activity during normal operation. DATA always persists
# via Logger.data(), independent of this list.
SD_LOG_LEVELS = ("WARNING", "ERROR")

# Delay at the very start of main(), before any peripheral is touched.
# The OPC-N3 has its own internal MCU that needs time to complete its own
# boot sequence after power-up; probing it over SPI too early can leave it
# in a state that only a physical reset recovers from.
STARTUP_DELAY_S = 2.0
