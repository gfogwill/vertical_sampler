import supervisor
supervisor.runtime.autoreload = False

import address
from lora import LoRa
from payload import main_loop
from sdcard import SDCard
import logging
import board
import busio

PAYLOAD_ID = "matorova"

BOARD_GP_LORA_SCK = board.GP2
BOARD_GP_LORA_TX = board.GP3
BOARD_GP_LORA_RX = board.GP4

spi = busio.SPI(BOARD_GP_LORA_SCK, MOSI=BOARD_GP_LORA_TX, MISO=BOARD_GP_LORA_RX)


def main():
    # LoRa MUST be initialized first: its __init__ does a hardware reset and
    # reads the VERSION register.  If the SD card touches the shared SPI bus
    # first and leaves it in a bad state (e.g. failed mount), the RFM9x will
    # not see VERSION==18 and raise RuntimeError.  SD runs in degraded mode
    # (no writes) if it fails, so it is safe to init it second.
    try:
        lora = LoRa(
            spi=spi,
            node=address.matorova_rfm_address,
            destination=address.ground_rfm_address,
        )
    except Exception as e:
        # Without LoRa there is nothing we can do — print and let the watchdog
        # reset the board rather than hanging silently.
        print("FATAL: LoRa init failed: {}".format(e))
        raise

    # SDCard accepts (spi, payload_id).  It runs in degraded mode on failure.
    sd_card = SDCard(spi, PAYLOAD_ID)
    logger = logging.getLogger("{}-main".format(PAYLOAD_ID), sd_card)

    logger.info("Starting {} payload".format(PAYLOAD_ID))
    logger.debug(
        "LoRa initialized with node: {}, destination: {}".format(
            address.matorova_rfm_address,
            address.ground_rfm_address,
        )
    )

    try:
        main_loop(
            lora=lora,
            payload_id=PAYLOAD_ID,
            logger=logger,
        )
    except Exception as e:
        logger.error("An error occurred: {}".format(e))


if __name__ == "__main__":
    main()
