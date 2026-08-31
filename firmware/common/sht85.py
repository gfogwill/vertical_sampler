import time
import busio
import config


class Sht85:
    ADDRESS = 0x44

    def __init__(self, logger):
        self._logger = logger
        self._i2c = None
        try:
            self._i2c = busio.I2C(scl=config.I2C_SCL, sda=config.I2C_SDA, frequency=10_000)
            self._logger.info("SHT85 I2C initialized")
        except Exception as error:
            self._logger.warning("SHT85 unavailable: {}".format(error))

    def read(self):
        if self._i2c is None:
            raise RuntimeError("SHT85 I2C unavailable")
        data = bytearray(6)
        while not self._i2c.try_lock():
            pass
        try:
            self._i2c.writeto(self.ADDRESS, bytes((0x24, 0x00)))
            time.sleep(0.015)
            self._i2c.readfrom_into(self.ADDRESS, data)
        finally:
            self._i2c.unlock()
        t = (data[0] << 8) | data[1]
        rh = (data[3] << 8) | data[4]
        return -45.0 + 175.0 * t / 65535.0, 100.0 * rh / 65535.0
