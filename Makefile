CIRCUIT_URL   = https://downloads.circuitpython.org/bin/raspberry_pi_pico_w/en_GB/adafruit-circuitpython-raspberry_pi_pico_w-en_GB-8.2.6.uf2
CIRCUIT_FNAME = adafruit-circuitpython-raspberry_pi_pico_w-en_GB-8.2.6.uf2

NUKE_URL   = https://datasheets.raspberrypi.com/soft/flash_nuke.uf2
NUKE_FNAME = flash_nuke.uf2

PINOUT_URL = https://datasheets.raspberrypi.com/pico/Pico-R3-A4-Pinout.pdf

CIRCUITPY_PATH ?= /media/$(USER)/CIRCUITPY

# --- Firmware downloads ---

download-circuitpython-image:
	mkdir -p images
	curl -o images/$(CIRCUIT_FNAME) $(CIRCUIT_URL)

download-nuke:
	mkdir -p images
	curl -o images/$(NUKE_FNAME) $(NUKE_URL)

# --- Deploy targets ---

update-kenttarova: update-payload
	cp payloads/kenttarova/main.py $(CIRCUITPY_PATH)/main.py
	@echo "Deployed kenttarova to $(CIRCUITPY_PATH)"

update-matorova: update-payload
	cp payloads/matorova/main.py $(CIRCUITPY_PATH)/main.py
	@echo "Deployed matorova to $(CIRCUITPY_PATH)"

update-ground: update-payload
	cp ground/main.py $(CIRCUITPY_PATH)/main.py
	@echo "Deployed ground station to $(CIRCUITPY_PATH)"

update-payload:
	rm -f $(CIRCUITPY_PATH)/code.py
	cp payload/*.py $(CIRCUITPY_PATH)/

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
