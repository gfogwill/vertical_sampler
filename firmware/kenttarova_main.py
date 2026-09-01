import supervisor
supervisor.runtime.autoreload = False

import busio
import digitalio
import config
import logging
from lora import LoRa
from payload import main_loop
from sdcard import SDCard

PAYLOAD_ID = "kenttarova"
spi = busio.SPI(config.SPI_SCK, MOSI=config.SPI_MOSI, MISO=config.SPI_MISO)

def main():
    # Keep OPC-N3 deselected while SD and LoRa claim the shared SPI bus.
    opc_cs = digitalio.DigitalInOut(config.OPC_CS)
    opc_cs.switch_to_output(value=True)

    lora = LoRa(spi, config.KENTTAROVA_RFM_ADDRESS, config.GROUND_RFM_ADDRESS)
    sd_card = SDCard(spi, PAYLOAD_ID)
    logger = logging.getLogger("{}-main".format(PAYLOAD_ID), sd_card)

    # OPCN3 creates and owns its CS pin after the shared bus is ready.
    opc_cs.deinit()
    main_loop(
        lora=lora,
        payload_id=PAYLOAD_ID,
        logger=logger,
        spi=spi,
    )

if __name__ == "__main__":
    main()
