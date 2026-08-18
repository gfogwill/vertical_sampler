import supervisor
supervisor.runtime.autoreload = False
import address
import board
import busio
import config
import logging
from lora import LoRa
from payload import main_loop
from sdcard import SDCard

PAYLOAD_ID = "kenttarova"
spi = busio.SPI(config.SPI_SCK, MOSI=config.SPI_MOSI, MISO=config.SPI_MISO)

def main():
    lora = LoRa(spi, address.kenttarova_rfm_address, address.ground_rfm_address)
    sd_card = SDCard(spi, PAYLOAD_ID)
    logger = logging.getLogger("{}-main".format(PAYLOAD_ID), sd_card)
    main_loop(lora=lora, payload_id=PAYLOAD_ID, logger=logger)

if __name__ == "__main__":
    main()
