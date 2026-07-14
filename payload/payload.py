import json
import time
import rtc
import microcontroller
import watchdog
import adafruit_gps
import analogio
import board
import busio
import digitalio
import led
import pack
from lora import LoRa
from pressure_sensor import PressureSensor

BOARD_GP_RH_SDA = board.GP8
BOARD_GP_RH_SCL = board.GP9
BOARD_GP_GPS_UART_TX = board.GP0
BOARD_GP_GPS_UART_RX = board.GP1
BOARD_GP_ELECTROVALVE = board.GP19
BOARD_GP_PUMP_FRONT = board.GP20
BOARD_GP_PUMP_BACK = board.GP21
BOARD_GP_BATTERY_MONITOR = board.GP27
BOARD_GP_FLOWMETER = board.GP28

_FLOW_DIVIDER_RATIO = 32.6 / (10.0 + 32.6)
_FLOW_FULL_SCALE_V = 4.0
_FLOW_FULL_SCALE_LMIN = 20.0
_FLOW_OFFSET_LMIN = 0.25

_BAT_CAL_FACTOR = 10.15
_BAT_WARN_V = 19.8
_BAT_CUTOFF_V = 18.6

_TEMP_WARN_C = 45.0
_TEMP_CRITICAL_C = 55.0

_WATCHDOG_TIMEOUT_S = 30

HEARTBEAT_INTERVAL_S = 60
HEARTBEAT_OFFSETS = {
    "matorova":   0,
    "kenttarova": 30,
}

i2c_bus = busio.I2C(scl=BOARD_GP_RH_SCL, sda=BOARD_GP_RH_SDA)
_rtc = rtc.RTC()
_rtc_synced = False
_wdt = None


def _init_watchdog():
    global _wdt
    try:
        _wdt = microcontroller.watchdog
        _wdt.timeout = _WATCHDOG_TIMEOUT_S
        _wdt.mode = watchdog.WatchDogMode.RESET
        _wdt.feed()
    except Exception as e:
        print("Watchdog init failed: {}".format(e))
        _wdt = None


def _feed_watchdog():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass


def _format_rtc_time():
    t = _rtc.datetime
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}".format(
        t.tm_year, t.tm_mon, t.tm_mday,
        t.tm_hour, t.tm_min, t.tm_sec
    )


class Pump:
    def __init__(self, logger):
        time.sleep(0.2)
        self.pump_front = digitalio.DigitalInOut(BOARD_GP_PUMP_FRONT)
        self.pump_front.switch_to_output()
        time.sleep(0.2)
        self.pump_back = digitalio.DigitalInOut(BOARD_GP_PUMP_BACK)
        self.pump_back.switch_to_output()
        logger.info("Pump initialized")

    def set_state(self, pump_location, state):
        state_value = state == "on"
        if pump_location == "front":
            self.pump_front.value = state_value
        elif pump_location == "back":
            self.pump_back.value = state_value
        elif pump_location == "both":
            self.pump_front.value = state_value
            self.pump_back.value = state_value

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
        self.valve = digitalio.DigitalInOut(BOARD_GP_ELECTROVALVE)
        self.valve.switch_to_output()
        logger.info("Valve initialized")

    def set_state(self, state):
        self.valve.value = state == "on"

    def get_state(self):
        return int(self.valve.value)


class FlowMeter:
    def __init__(self, logger):
        time.sleep(0.2)
        self.flow_meter = analogio.AnalogIn(BOARD_GP_FLOWMETER)
        logger.info("FlowMeter initialized")

    def flow(self):
        v_adc = self.flow_meter.value * 3.3 / 65535
        v_sensor = v_adc / _FLOW_DIVIDER_RATIO
        return max(0.0, (v_sensor / _FLOW_FULL_SCALE_V) * _FLOW_FULL_SCALE_LMIN - _FLOW_OFFSET_LMIN)


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
        humidity = None
        temperature = None
        try:
            self.sensor.writeto(0x44, bytes([0x24, 0x00]))
            time.sleep(0.015)
            self.sensor.readfrom_into(0x44, data)
            temperature_raw = data[0] << 8 | data[1]
            humidity_raw = data[3] << 8 | data[4]
            temperature = -45 + (175 * temperature_raw / 65535.0)
            humidity = 100 * humidity_raw / 65535.0
        finally:
            self.sensor.unlock()
        return humidity, temperature


class Battery:
    def __init__(self, logger):
        self.v = analogio.AnalogIn(BOARD_GP_BATTERY_MONITOR)
        logger.info("Battery initialized")

    def voltage(self):
        raw = self.v.value * 3.3 / 65535
        return _BAT_CAL_FACTOR * raw


class GPS:
    def __init__(self, logger):
        self.logger = logger
        self.sensor = adafruit_gps.GPS(
            busio.UART(BOARD_GP_GPS_UART_TX, BOARD_GP_GPS_UART_RX, baudrate=9600)
        )
        self.sensor.send_command(b"PMTK314,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0")
        self.sensor.send_command(b"PMTK220,1000")
        self._last_lat = None
        self._last_lon = None
        self._last_alt = None
        self._last_time = None
        logger.info("GPS initialized")

    def lat_lon_alt_time(self, max_attempts=5, timeout=10):
        """Full GPS poll: waits up to timeout/max_attempts for a fix."""
        global _rtc_synced
        gps = self.sensor
        gps.update()
        start_time = time.time()
        attempts = 0
        while not gps.has_fix and (time.time() - start_time < timeout) and attempts < max_attempts:
            self.logger.debug("Waiting for GPS fix...")
            gps.update()
            _feed_watchdog()
            time.sleep(1)
            attempts += 1
        if not gps.has_fix:
            self.logger.error("GPS fix not acquired after timeout")
            return None, None, None, None
        self.logger.info("GPS fix acquired")
        while gps.has_fix:
            gps.update()
            lat = gps.latitude
            lon = gps.longitude
            time_ = gps.timestamp_utc
            alt = gps.altitude_m
            try:
                if all(x is not None for x in (lat, lon, alt, time_)):
                    self._last_lat = lat
                    self._last_lon = lon
                    self._last_alt = alt
                    self._last_time = time_
                    if not _rtc_synced:
                        try:
                            _rtc.datetime = time_
                            _rtc_synced = True
                            self.logger.info("RTC synced from GPS")
                        except Exception as e:
                            self.logger.warning("RTC sync failed: {}".format(e))
                    return lat, lon, alt, time_
            except Exception as e:
                self.logger.error("Error processing GPS data: {}".format(e))
                return None, None, None, None
        self.logger.error("Lost GPS fix")
        return None, None, None, None

    def lat_lon_alt_time_fast(self):
        """Non-blocking GPS poll: pumps NMEA buffer once, returns cached
        fix if still valid, None tuple if no fix.  Use on the command
        path where blocking for 5 s would make the ACK arrive too late."""
        gps = self.sensor
        gps.update()  # drain NMEA buffer, ~0 ms
        if gps.has_fix:
            lat = gps.latitude
            lon = gps.longitude
            alt = gps.altitude_m
            time_ = gps.timestamp_utc
            if all(x is not None for x in (lat, lon, alt, time_)):
                self._last_lat = lat
                self._last_lon = lon
                self._last_alt = alt
                self._last_time = time_
                return lat, lon, alt, time_
        # Return last known fix if available
        if self._last_lat is not None:
            return self._last_lat, self._last_lon, self._last_alt, self._last_time
        return None, None, None, None


def _check_safety(pump, valve, bat_v, logger):
    cpu_temp = microcontroller.cpu.temperature
    if cpu_temp >= _TEMP_CRITICAL_C:
        logger.error("CPU temp critical: {:.1f}C - cutting pump & valve".format(cpu_temp))
        pump.emergency_off()
        valve.set_state("off")
    elif cpu_temp >= _TEMP_WARN_C:
        logger.warning("CPU temp warning: {:.1f}C".format(cpu_temp))
    if bat_v > 1.0:
        if bat_v <= _BAT_CUTOFF_V:
            logger.error("Battery critical: {:.2f}V - cutting pump & valve".format(bat_v))
            pump.emergency_off()
            valve.set_state("off")
        elif bat_v <= _BAT_WARN_V:
            logger.warning("Battery low: {:.2f}V".format(bat_v))


def _failed_sensors_loop(lora, logger):
    logger.error("Failed to initialize sensors, entering failed sensors loop")
    while True:
        _feed_watchdog()
        led.blink(13, tsleep=0.2, bsleep=0.1, esleep=0.1)
        lora.send(b"Failed to init sensors\n")
        time.sleep(2)


def _failed_reading_data(lora, logger):
    logger.error("Failed to read data")
    for _ in range(10):
        _feed_watchdog()
        led.blink(7, tsleep=0.2, bsleep=0.1, esleep=0.1)
        lora.send(b"Failed to read data\n")
        time.sleep(2)


def _collect_data(payload_id, gps, rh_sensor, pressure_sensor, bat,
                  flow_meter, pump, valve, lora, start_time, fast_gps=False):
    elapsed_time = time.time() - start_time
    if fast_gps:
        lat, lon, alt, time_ = gps.lat_lon_alt_time_fast()
    else:
        lat, lon, alt, time_ = gps.lat_lon_alt_time()
    rh_humidity, rh_temperature = rh_sensor.humidity_and_temperature()
    bat_v = bat.voltage()
    cpu_temp = microcontroller.cpu.temperature
    return {
        "payload_id": payload_id,
        "rtc_time": _format_rtc_time(),
        "gps_time": elapsed_time,
        "gps_latitude": lat,
        "gps_longitude": lon,
        "gps_altitude": alt,
        "rh_sensor_humidity": rh_humidity,
        "rh_sensor_temperature": rh_temperature,
        "pressure_sensor_pressure": pressure_sensor.pressure(),
        "pressure_sensor_temperature": pressure_sensor.temperature(),
        "battery_voltage": bat_v,
        "cpu_temperature": cpu_temp,
        "flow": flow_meter.flow(),
        "rssi": lora.rssi(),
        "pump_front_state": pump.get_front_state(),
        "pump_back_state": pump.get_back_state(),
        "valve_state": valve.get_state(),
    }


def _send_with_type(lora, data, msg_type):
    """Copy data dict, set msg_type, and send."""
    pkt = data.copy()
    pkt["msg_type"] = msg_type
    lora.send(pack.dict2bytes(pkt))


def _handle_command(msg, data, pump, valve, lora, payload_id, logger):
    try:
        msg_in = msg.decode().strip()
        cmd = msg_in.split()
        if not cmd:
            return
        main_cmd = cmd[0]
        sub_cmd = cmd[1:]

        if not data:
            # data dict not yet populated (first cycle still running)
            logger.warning("Command arrived before first data collection; sending fill-value ack")

        if main_cmd == "pump":
            if len(sub_cmd) < 2:
                raise ValueError("pump requires <location> <state>")
            pump.set_state(sub_cmd[0], sub_cmd[1])
            data["pump_front_state"] = pump.get_front_state()
            data["pump_back_state"] = pump.get_back_state()
            logger.info("Processed pump command: {}".format(msg_in))
        elif main_cmd == "valve":
            if len(sub_cmd) < 1:
                raise ValueError("valve requires <state>")
            valve.set_state(sub_cmd[0])
            data["valve_state"] = valve.get_state()
            logger.info("Processed valve command: {}".format(msg_in))
        elif main_cmd == "data":
            logger.info("Data command received")
        else:
            logger.warning("Unexpected command: {}".format(msg_in))
            return

        _send_with_type(lora, data, pack.MSG_COMMAND_ACK)
        logger.info("cmd_ack sent for: {}".format(main_cmd))

    except Exception as err:
        logger.error("Error processing command: {}".format(err))
        try:
            _send_with_type(lora, data, pack.MSG_COMMAND_ERROR)
            logger.info("cmd_err sent")
        except Exception as send_err:
            logger.error("cmd_err send also failed: {}".format(send_err))


def main_loop(lora, payload_id, logger):
    logger.info("Starting main loop")
    _init_watchdog()
    logger.info("Watchdog armed ({} s timeout)".format(_WATCHDOG_TIMEOUT_S))

    try:
        rh_sensor = Sht85Sensor(logger, i2c_bus)
        valve = Valve(logger)
        pump = Pump(logger)
        pressure_sensor = PressureSensor(i2c_bus)
        bat = Battery(logger)
        flow_meter = FlowMeter(logger)
        gps = GPS(logger)
        logger.info("Sensors initialized successfully")
    except Exception as err:
        logger.error("Error initializing sensors: {}".format(err))
        _failed_sensors_loop(lora, logger)

    start_time = time.time()
    data = {}
    _fast_next = False  # if True, use fast GPS on the next _collect_data

    _hb_offset = HEARTBEAT_OFFSETS.get(payload_id, 0)
    _next_heartbeat = time.monotonic() + _hb_offset

    while True:
        _feed_watchdog()
        led.blink(1)

        try:
            data = _collect_data(
                payload_id, gps, rh_sensor, pressure_sensor, bat,
                flow_meter, pump, valve, lora, start_time,
                fast_gps=_fast_next,
            )
            _fast_next = False
            _check_safety(pump, valve, data["battery_voltage"], logger)
            led.blink(2)
            logger.data(data)
            logger.info("Sensor data collected")
        except Exception as err:
            logger.error("Error reading data: {}".format(err))
            _fast_next = False
            _failed_reading_data(lora, logger)
            continue

        now_mono = time.monotonic()
        if now_mono >= _next_heartbeat:
            try:
                _send_with_type(lora, data, pack.MSG_TELEMETRY)
                logger.info("Heartbeat sent")
            except Exception as err:
                logger.error("Heartbeat send failed: {}".format(err))
            _next_heartbeat = now_mono + HEARTBEAT_INTERVAL_S

        _feed_watchdog()
        led.blink(3)
        deadline = time.time() + 12
        got_cmd = False
        while time.time() < deadline:
            _feed_watchdog()
            msg = lora.receive(timeout=1)
            if msg is not None:
                _handle_command(msg, data, pump, valve, lora, payload_id, logger)
                got_cmd = True
                # Use fast GPS next cycle so ACK window stays within ground's retry budget
                _fast_next = True
                deadline = time.time() + 3
            elif got_cmd:
                break
        time.sleep(0.2)
