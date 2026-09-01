import time
import rtc
import busio
import adafruit_gps
import config


class Gps:
    RESYNC_INTERVAL_S = 60.0

    def __init__(self, logger):
        self._logger = logger
        self._rtc = rtc.RTC()
        self._sensor = adafruit_gps.GPS(busio.UART(config.GPS_UART_TX, config.GPS_UART_RX, baudrate=9600, timeout=0.1))
        self._sensor.send_command(b"PMTK314,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0")
        self._sensor.send_command(b"PMTK220,1000")
        self._synced = False
        self._last_sync = 0.0
        self._last = {"gps_time": None, "gps_latitude": None, "gps_longitude": None, "gps_altitude": None, "gps_satellites": None}
        self._logger.info("GPS initialized")

    def update(self):
        for _ in range(4):
            if not self._sensor.update():
                break
        now = time.monotonic()
        stamp = self._sensor.timestamp_utc
        valid = self._sensor.has_fix and stamp is not None and stamp.tm_year >= 2020
        if valid:
            self._last = {"gps_time": int(time.mktime(stamp)), "gps_latitude": self._sensor.latitude, "gps_longitude": self._sensor.longitude, "gps_altitude": self._sensor.altitude_m, "gps_satellites": self._sensor.satellites}
            if not self._synced or now - self._last_sync >= self.RESYNC_INTERVAL_S:
                self._rtc.datetime = stamp
                self._synced = True
                self._last_sync = now
                self._logger.info("RTC synced from GPS")
                return {"record_type": "time_sync", "utc_time": self._utc_time(), "monotonic_s": now, "source": "gps", "gps_satellites": self._sensor.satellites}
        return None

    def _utc_time(self):
        if not self._synced:
            return None
        current = self._rtc.datetime
        return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(current.tm_year, current.tm_mon, current.tm_mday, current.tm_hour, current.tm_min, current.tm_sec)

    def fields(self):
        data = dict(self._last)
        data["utc_time"] = self._utc_time()
        data["monotonic_s"] = time.monotonic()
        data["time_source"] = "gps" if self._sensor.has_fix and self._synced else ("rtc_holdover" if self._synced else "unsynced")
        data["gps_fix"] = bool(self._sensor.has_fix)
        return data
