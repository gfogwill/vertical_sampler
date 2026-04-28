MP_URL     = https://micropython.org/resources/firmware/RPI_PICO_W-20230426-v1.20.0.uf2
MP_FNAME   = RPI_PICO_W-20230426-v1.20.0.uf2

CIRCUIT_URL   = https://downloads.circuitpython.org/bin/raspberry_pi_pico_w/en_GB/adafruit-circuitpython-raspberry_pi_pico_w-en_GB-8.2.6.uf2
CIRCUIT_FNAME = adafruit-circuitpython-raspberry_pi_pico_w-en_GB-8.2.6.uf2

NUKE_URL   = https://datasheets.raspberrypi.com/soft/flash_nuke.uf2
NUKE_FNAME = flash_nuke.uf2

PINOUT_URL = https://datasheets.raspberrypi.com/pico/Pico-R3-A4-Pinout.pdf

CIRCUITPY_PATH ?= /media/$(USER)/CIRCUITPY

# --- Firmware downloads ---

download-micropython-image:
	mkdir -p images
	curl -o images/$(MP_FNAME) $(MP_URL)

download-circuitpython-image:
	mkdir -p images
	curl -o images/$(CIRCUIT_FNAME) $(CIRCUIT_URL)

download-nuke:
	mkdir -p images
	curl -o images/$(NUKE_FNAME) $(NUKE_URL)

# --- Deploy targets ---

update-ground: update-common
	cp ground/main.py $(CIRCUITPY_PATH)/main.py

update-kenttarova: update-payload
	cp payload/main.py $(CIRCUITPY_PATH)/main.py

update-matorova: update-payload
	@echo "Copy payload/main.py and update PAYLOAD_ID=matorova manually, or add matorova/main.py"

update-payload: update-common
	cp payload/payload.py $(CIRCUITPY_PATH)/payload.py
	cp payload/pressure_sensor.py $(CIRCUITPY_PATH)/pressure_sensor.py

update-common:
	rm -f $(CIRCUITPY_PATH)/code.py
	cp payload/sdcard.py $(CIRCUITPY_PATH)/sdcard.py
	cp payload/logging.py $(CIRCUITPY_PATH)/logging.py
	cp payload/lora.py $(CIRCUITPY_PATH)/lora.py
	cp payload/address.py $(CIRCUITPY_PATH)/address.py
	cp payload/led.py $(CIRCUITPY_PATH)/led.py
	cp payload/pack.py $(CIRCUITPY_PATH)/pack.py

# --- Utilities ---

install-lora-deps:
	@echo "Install the following packages via Thonny or circup:"
	@echo "  adafruit-circuitpython-rfm9x"
	@echo "  adafruit-circuitpython-gps"
	@echo "  adafruit-circuitpython-ntp"

open-pico-pinout:
	@xdg-open $(PINOUT_URL) 2>/dev/null || open $(PINOUT_URL)

open-circuitpy:
	@xdg-open $(CIRCUITPY_PATH) 2>/dev/null || open $(CIRCUITPY_PATH)
