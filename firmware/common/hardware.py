"""Reusable CircuitPython hardware drivers for the vertical sampler.

This module is introduced without changing the active mission loop. The next
refactor step will import these classes from payload.py after on-device tests.
"""

import time

from firmware.common import adafruit_gps
import analogio
import busio
import digitalio

import config


class Pump:
    def __init__(self, logger):
        time.sleep(0.2)
        self.pump_front = digitalio.DigitalInOut(config.PUMP_FRONT)
        self.pump_front.switch_to_output()
        time.sleep(0.2)
        self.pump_back = digitalio.DigitalInOut(config.PUMP_BACK)
        self.pump_back.switch_to_output()
        logger.info("Pump initialized")

    def set_state(self, pump_location, state):
        value = state == "on"
        if pump_location == "front":
            self.pump_front.value = value
        elif pump_location == "back":
            self.pump_back.value = value
        elif pump_location == "both":
            self.pump_front.value = value
            self.pump_back.value = value

    def get_front_state(self):
        return int(self.pump_front.value)

    def get_back_state(self):
        return int(self.pump_back.value)

    def emergency_off(self):
        self.pump_front.value = False
        self.pump_back.value = False


class Valve:
    def __init__(self, logger):
        time.sleep(0.2)
        self.valve = digitalio.DigitalInOut(config.ELECTROVALVE)
        self.valve.switch_to_output()
        logger.info("Valve initialized")

    def set_state(self, state):
        self.valve.value = state == "on"

    def get_state(self):
        return int(self.valve.value)


class FlowMeter:
    def __init__(self, logger, oversample_n=16, sample_delay_s=0.001):
        time.sleep(0.2)
        self.flow_meter = analogio.AnalogIn(config.FLOWMETER)
        self._n = oversample_n
        self._delay = sample_delay_s
        logger.info("FlowMeter initialized")

    def flow(self):
        total = 0
        for _ in range(self._n):
            total += self.flow_meter.value
            if self._delay:
                time.sleep(self._delay)
        raw_avg = total / self._n
        v_adc = raw_avg * 3.3 / 65535
        v_sensor = v_adc / config.FLOW_DIVIDER_RATIO
        return max(0.0, (v_sensor / config.FLOW_FULL_SCALE_V) * config.FLOW_FULL_SCALE_LMIN - config.FLOW_OFFSET_LMIN)


class Sht85Sensor:
    def __init__(self, logger, i2c_bus):
        time.sleep(0.1)
        self.sensor = i2c_bus
        while not self.sensor.try_lock():
            pass
        time.sleep(0.1)
        self.sensor.unlock()
        logger.info("Sht85Sensor initialized")

    def humidity_and_temperature(self):
        data = bytearray(6)
        while not self.sensor.try_lock():
            pass
        try:
            self.sensor.writeto(0x44, bytes([0x24, 0x00]))
            time.sleep(0.015)
            self.sensor.readfrom_into(0x44, data)
            temperature_raw = data[0] << 8 | data[1]
            humidity_raw = data[3] << 8 | data[4]
            return (100 * humidity_raw / 65535.0,
                    -45 + (175 * temperature_raw / 65535.0))
        finally:
            self.sensor.unlock()


class Battery:
    def __init__(self, logger):
        self.v = analogio.AnalogIn(config.BATTERY_MONITOR)
        logger.info("Battery initialized")

    def voltage(self):
        return config.BATTERY_CAL_FACTOR * self.v.value * 3.3 / 65535


class GPS:
    def __init__(self, logger, feed_watchdog=None, on_time=None):
        self.logger = logger
        self._feed_watchdog = feed_watchdog
        self._on_time = on_time
        self.sensor = adafruit_gps.GPS(busio.UART(
            config.GPS_UART_TX, config.GPS_UART_RX, baudrate=9600))
        self.sensor.send_command(b"PMTK314,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0")
        self.sensor.send_command(b"PMTK220,1000")
        self._last_lat = None
        self._last_lon = None
        self._last_alt = None
        self._last_time = None
        logger.info("GPS initialized")

    def lat_lon_alt_time(self, max_attempts=5, timeout=10):
        gps = self.sensor
        gps.update()
        start_time = time.time()
        attempts = 0
        while not gps.has_fix and time.time() - start_time < timeout and attempts < max_attempts:
            self.logger.debug("Waiting for GPS fix...")
            gps.update()
            if self._feed_watchdog:
                self._feed_watchdog()
            time.sleep(1)
            attempts += 1
        if not gps.has_fix:
            self.logger.error("GPS fix not acquired after timeout")
            return None, None, None, None
        lat, lon, alt, time_ = gps.latitude, gps.longitude, gps.altitude_m, gps.timestamp_utc
        if not all(x is not None for x in (lat, lon, alt, time_)):
            return None, None, None, None
        self._last_lat, self._last_lon = lat, lon
        self._last_alt, self._last_time = alt, time_
        if self._on_time:
            self._on_time(time_)
        return lat, lon, alt, time_

    def lat_lon_alt_time_fast(self):
        self.sensor.update()
        if self.sensor.has_fix:
            lat, lon = self.sensor.latitude, self.sensor.longitude
            alt, time_ = self.sensor.altitude_m, self.sensor.timestamp_utc
            if all(x is not None for x in (lat, lon, alt, time_)):
                self._last_lat, self._last_lon = lat, lon
                self._last_alt, self._last_time = alt, time_
                return lat, lon, alt, time_
        return self._last_lat, self._last_lon, self._last_alt, self._last_time
