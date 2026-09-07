"""Shared SPI bus arbiter for SD, OPC-N3 and LoRa.

sdcardio manages its own CS pin internally and already deselects it
correctly after each transaction (CircuitPython sdcardio docs state
that every CS pin on a shared bus must be HIGH before any transaction
occurs). This module cannot own SD-CS directly because sdcardio takes
a raw pin, not a DigitalInOut.

Instead, SharedSPI owns OPC-CS and LoRa-CS (created here as
DigitalInOut, since OPCN3 and LoRa accept an externally created CS).
Before any SD, OPC or LoRa operation, the other two CS lines are
forced HIGH so only one peripheral can ever be selected at a time.
"""

import digitalio


class SharedSPI:
    def __init__(self, spi, opc_cs_pin, lora_cs_pin):
        self.spi = spi

        self.opc_cs = digitalio.DigitalInOut(opc_cs_pin)
        self.opc_cs.switch_to_output(value=True)

        self.lora_cs = digitalio.DigitalInOut(lora_cs_pin)
        self.lora_cs.switch_to_output(value=True)

    def idle(self):
        self.opc_cs.value = True
        self.lora_cs.value = True

    def before_sd(self):
        self.idle()

    def before_opc(self):
        self.lora_cs.value = True

    def before_lora(self):
        self.opc_cs.value = True
