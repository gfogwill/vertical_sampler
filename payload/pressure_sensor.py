import board
import busio

I2C_ADDRESS = 0x5D

CTRL_REG1 = 0x20

PRESS_OUT_XL = 0x28
PRESS_OUT_L = 0x29
PRESS_OUT_H = 0x2A

DELTA_PRESS_XL = 0x3C
DELTA_PRESS_L = 0x3D
DELTA_PRESS_H = 0x3E

TEMP_L = 0x2B
TEMP_H = 0x2C


class PressureSensor:
    def __init__(self, i2c_bus):
        self.sensor = i2c_bus
        while not self.sensor.try_lock():
            pass
        try:
            # PD=1 (active), ODR=110 (12.5 Hz)
            self.sensor.writeto(I2C_ADDRESS, bytes([CTRL_REG1, 0xE0]))
        finally:
            self.sensor.unlock()

    def pressure(self):
        """Returns pressure in mbar."""
        pressure_xl = bytearray(1)
        pressure_h = bytearray(1)
        pressure_l = bytearray(1)
        delta_xl = bytearray(1)
        delta_h = bytearray(1)
        delta_l = bytearray(1)

        while not self.sensor.try_lock():
            pass
        try:
            self.sensor.writeto(I2C_ADDRESS, bytes([PRESS_OUT_XL]))
            self.sensor.readfrom_into(I2C_ADDRESS, pressure_xl)
            self.sensor.writeto(I2C_ADDRESS, bytes([PRESS_OUT_H]))
            self.sensor.readfrom_into(I2C_ADDRESS, pressure_h)
            self.sensor.writeto(I2C_ADDRESS, bytes([PRESS_OUT_L]))
            self.sensor.readfrom_into(I2C_ADDRESS, pressure_l)
            self.sensor.writeto(I2C_ADDRESS, bytes([DELTA_PRESS_XL]))
            self.sensor.readfrom_into(I2C_ADDRESS, delta_xl)
            self.sensor.writeto(I2C_ADDRESS, bytes([DELTA_PRESS_H]))
            self.sensor.readfrom_into(I2C_ADDRESS, delta_h)
            self.sensor.writeto(I2C_ADDRESS, bytes([DELTA_PRESS_L]))
            self.sensor.readfrom_into(I2C_ADDRESS, delta_l)
        finally:
            self.sensor.unlock()

        pressure = (pressure_h[0] << 16) | (pressure_l[0] << 8) | pressure_xl[0]
        return pressure / 4096.0

    def temperature(self):
        """Returns temperature in °C."""
        temp_lsb = bytearray(1)
        temp_msb = bytearray(1)

        while not self.sensor.try_lock():
            pass
        try:
            self.sensor.readfrom_into(I2C_ADDRESS, temp_lsb)
            self.sensor.writeto(I2C_ADDRESS, bytes([TEMP_H]))
            self.sensor.readfrom_into(I2C_ADDRESS, temp_msb)
        finally:
            self.sensor.unlock()

        count = (temp_msb[0] << 8) | temp_lsb[0]
        comp = count - (1 << 16)
        return 42.5 + (comp / 480.0)
