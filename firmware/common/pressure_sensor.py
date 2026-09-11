import time


class PressureSensor:
    ADDRESS = 0x5D
    CTRL_REG1 = 0x20
    STATUS_REG = 0x27
    PRESS_OUT_XL = 0x28
    TEMP_L = 0x2B

    def __init__(self, logger, i2c_bus):
        self._logger = logger
        self._sensor = i2c_bus
        while not self._sensor.try_lock():
            pass
        try:
            # PD=1 (activo), ODR=001 (1 Hz), BDU=1 (lectura atomica de la muestra)
            self._sensor.writeto(self.ADDRESS, bytes((self.CTRL_REG1, 0x94)))
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

    def _read_block(self, start_register, length):
        data = bytearray(length)
        while not self._sensor.try_lock():
            pass
        try:
            # bit 7 en 1 = auto-incremento de direccion (lectura atomica multi-byte)
            self._sensor.writeto(self.ADDRESS, bytes((start_register | 0x80,)))
            self._sensor.readfrom_into(self.ADDRESS, data)
        finally:
            self._sensor.unlock()
        return data

    def _data_ready(self):
        status = self._read_register(self.STATUS_REG)
        p_da = bool(status & 0x02)
        t_da = bool(status & 0x01)
        return p_da, t_da

    def pressure(self):
        p_bytes = self._read_block(self.PRESS_OUT_XL, 3)
        raw = (p_bytes[2] << 16) | (p_bytes[1] << 8) | p_bytes[0]
        return raw / 4096.0

    def temperature(self):
        t_bytes = self._read_block(self.TEMP_L, 2)
        raw = (t_bytes[1] << 8) | t_bytes[0]
        if raw & 0x8000:
            raw -= 1 << 16
        return 42.5 + raw / 480.0

    def read(self):
        """Lee presion y temperatura juntas, solo si hay muestra nueva disponible."""
        p_da, t_da = self._data_ready()
        if not (p_da and t_da):
            return None
        return self.pressure(), self.temperature()
