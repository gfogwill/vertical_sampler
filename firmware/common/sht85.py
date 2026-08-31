import time


class Sht85Sensor:
    def __init__(self, logger, i2c_bus):
        self.logger = logger
        time.sleep(0.1)
        self.sensor = i2c_bus
        while not self.sensor.try_lock():
            pass
        time.sleep(0.1)
        self.sensor.unlock()
        self.logger.info("Sht85Sensor initialized")

    def humidity_and_temperature(self):
        data = bytearray(6)
        while not self.sensor.try_lock():
            pass
        humidity = None
        temperature = None
        try:
            self.sensor.writeto(0x44, bytes([0x24, 0x00]))
            time.sleep(0.015)
            self.sensor.readfrom_into(0x44, data)
            temperature_raw = (data[0] << 8) | data[1]
            humidity_raw = (data[3] << 8) | data[4]
            temperature = -45 + (175 * temperature_raw / 65535.0)
            humidity = 100 * humidity_raw / 65535.0
        finally:
            self.sensor.unlock()
        return humidity, temperature
