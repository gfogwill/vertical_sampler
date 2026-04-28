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
    sd_card = SDCard(spi, fname="log.txt")
    logger = logging.getLogger("matorova-main", sd_card)

    logger.info("Starting matorova payload")

    try:
        lora = LoRa(
            spi=spi,
            node=address.matorova_rfm_address,
            destination=address.ground_rfm_address
        )
        logger.debug(
            "LoRa initialized with node: {}, destination: {}".format(
                address.matorova_rfm_address,
                address.ground_rfm_address
            )
        )
        main_loop(
            lora=lora,
            payload_id=PAYLOAD_ID,
            logger=logger
        )
    except Exception as e:
        logger.error("An error occurred: {}".format(e))


if __name__ == "__main__":
    main()
