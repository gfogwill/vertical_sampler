import supervisor
supervisor.runtime.autoreload = False

import time
import busio
import config
import logging
from lora import LoRa
from payload import main_loop
from sdcard import SDCard
from shared_spi import SharedSPI

PAYLOAD_ID = "alma"
spi = busio.SPI(config.SPI_SCK, MOSI=config.SPI_MOSI, MISO=config.SPI_MISO)


def main():
    time.sleep(config.STARTUP_DELAY_S)
    shared_spi = SharedSPI(spi, config.OPC_CS, config.LORA_CS)
    lora = LoRa(spi, config.ALMA_RFM_ADDRESS, config.GROUND_RFM_ADDRESS, shared_spi=shared_spi)
    sd_card = SDCard(spi, PAYLOAD_ID, shared_spi=shared_spi)
    logger = logging.getLogger("{}-main".format(PAYLOAD_ID), sd_card)
    main_loop(lora=lora, payload_id=PAYLOAD_ID, logger=logger, spi=spi, shared_spi=shared_spi)


if __name__ == "__main__":
    main()
