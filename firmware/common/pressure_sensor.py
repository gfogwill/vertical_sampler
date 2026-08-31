import time


class PressureSensor:
    ADDRESS = 0x5D
    CTRL_REG1 = 0x20
    PRESS_OUT_XL = 0x28
    PRESS_OUT_L = 0x29
    PRESS_OUT_H = 0x2A
    TEMP_L = 0x2B
    TEMP_H = 0x2C

    def __init__(self, logger, i2c_bus):
        self._logger = logger
        self._sensor = i2c_bus
        while not self._sensor.try_lock():
            pass
        try:
            self._sensor.writeto(self.ADDRESS, bytes((self.CTRL_REG1, 0xE0)))
        finally:
            self._sensor.unlock()
        time.sleep(0.1)
        self._logger.info("Pressure sensor initialized")

    def _read_register(self, register):
        value = bytearray(1)
        while not self._sensor.try_lock():
            pass
        try:
            self._sensor.writeto(self.ADDRESS, bytes((register,)))
            self._sensor.readfrom_into(self.ADDRESS, value)
        finally:
            self._sensor.unlock()
        return value[0]

    def pressure(self):
        pressure_xl = self._read_register(self.PRESS_OUT_XL)
        pressure_l = self._read_register(self.PRESS_OUT_L)
        pressure_h = self._read_register(self.PRESS_OUT_H)
        raw = (pressure_h << 16) | (pressure_l << 8) | pressure_xl
        return raw / 4096.0

    def temperature(self):
        temp_l = self._read_register(self.TEMP_L)
        temp_h = self._read_register(self.TEMP_H)
        raw = (temp_h << 8) | temp_l
        if raw & 0x8000:
            raw -= 1 << 16
        return 42.5 + raw / 480.0
