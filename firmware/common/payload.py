import json
import time
import rtc
import microcontroller
import watchdog
import adafruit_gps, pack
import analogio
import board
import busio
import digitalio
import led

import config
from opc_n3 import OPCN3
from pressure_sensor import PressureSensor

_CMD_MIN_LEN = 4

i2c_bus = busio.I2C(scl=config.I2C_SCL, sda=config.I2C_SDA)
_rtc = rtc.RTC()
_rtc_synced = False
_wdt = None

def _init_watchdog():
    global _wdt
    try:
        _wdt = microcontroller.watchdog
        _wdt.timeout = config.WATCHDOG_TIMEOUT_S
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

def _gps_time_to_epoch(time_):
    if time_ is None:
        return None
    try:
        return int(time.mktime(time_))
    except Exception:
        return None

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
        self.v = analogio.AnalogIn(config.BATTERY_MONITOR)
        logger.info("Battery initialized")

    def voltage(self):
        raw = self.v.value * 3.3 / 65535
        return config.BATTERY_CAL_FACTOR * raw

class GPS:
    def __init__(self, logger):
        self.logger = logger
        self.sensor = adafruit_gps.GPS(
            busio.UART(config.GPS_UART_TX, config.GPS_UART_RX, baudrate=9600)
        )
        self.sensor.send_command(b"PMTK314,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0")
        self.sensor.send_command(b"PMTK220,1000")
        self._last_lat = None
        self._last_lon = None
        self._last_alt = None
        self._last_time = None
        logger.info("GPS initialized")

    def lat_lon_alt_time(self, max_attempts=5, timeout=10):
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
        gps = self.sensor
        gps.update()
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
        if self._last_lat is not None:
            return self._last_lat, self._last_lon, self._last_alt, self._last_time
        return None, None, None, None

def _check_safety(pump, valve, bat_v, logger):
    cpu_temp = microcontroller.cpu.temperature
    if cpu_temp >= config.CPU_TEMP_CRITICAL_C:
        logger.error("CPU temp critical: {:.1f}C - cutting pump & valve".format(cpu_temp))
        pump.emergency_off()
        valve.set_state("off")
    elif cpu_temp >= config.CPU_TEMP_WARN_C:
        logger.warning("CPU temp warning: {:.1f}C".format(cpu_temp))
    if bat_v > 1.0:
        if bat_v <= config.BATTERY_CUTOFF_V:
            logger.error("Battery critical: {:.2f}V - cutting pump & valve".format(bat_v))
            pump.emergency_off()
            valve.set_state("off")
        elif bat_v <= config.BATTERY_WARN_V:
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
                  flow_meter, pump, valve, lora, start_time,
                  opc_hist=None, opc_available=False, fast_gps=False):
    if fast_gps:
        lat, lon, alt, time_ = gps.lat_lon_alt_time_fast()
    else:
        lat, lon, alt, time_ = gps.lat_lon_alt_time()
    gps_epoch = _gps_time_to_epoch(time_)
    rh_humidity, rh_temperature = rh_sensor.humidity_and_temperature()
    bat_v = bat.voltage()
    cpu_temp = microcontroller.cpu.temperature
    opc_hist = opc_hist or {}

    data = {
        "payload_id": payload_id,
        "rtc_time": _format_rtc_time(),
        "gps_time": gps_epoch,
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

        # OPC-N3 summary. Initially these are SD/log-only fields;
        # they will be added to pack.py in Phase B.
        "opc_available": opc_available,
        "opc_pm1": opc_hist.get("opc_pm1"),
        "opc_pm25": opc_hist.get("opc_pm25"),
        "opc_pm10": opc_hist.get("opc_pm10"),
        "opc_temperature": opc_hist.get("temperature_c"),
        "opc_relative_humidity": opc_hist.get(
            "relative_humidity_percent"
        ),
        "opc_flow_ml_s": opc_hist.get("sample_flow_rate_ml_s"),
        "opc_laser_status": opc_hist.get("laser_status"),
        "opc_fan_rev_count": opc_hist.get("fan_rev_count"),
        "opc_sampling_period_s": opc_hist.get("sampling_period_s"),
    }

    # Preserve the complete 24-bin distribution in the SD JSONL record.
    # No bin is sent through LoRa yet.
    for index in range(24):
        data["opc_bin_{}".format(index)] = opc_hist.get(
            "bin_{}".format(index)
        )

    bin_values = [opc_hist.get("bin_{}".format(index)) for index in range(24)]
    data["opc_bin_total"] = sum(value for value in bin_values if value is not None)

    return data


def _send_with_type(lora, data, msg_type):
    pkt = data.copy()
    pkt["msg_type"] = msg_type
    lora.send(pack.dict2bytes(pkt))

def _is_printable_ascii(b):
    for byte in b:
        if not (0x20 <= byte <= 0x7E or byte in (0x09, 0x0A, 0x0D)):
            return False
    return True

def _handle_command(msg, data, pump, valve, lora, payload_id, logger):
    if len(msg) < _CMD_MIN_LEN or not _is_printable_ascii(msg):
        logger.debug("Discarding garbage frame ({} bytes)".format(len(msg)))
        return
    try:
        msg_in = msg.decode().strip()
        cmd = msg_in.split()
        if not cmd:
            return
        main_cmd = cmd[0]
        sub_cmd = cmd[1:]

        if not data:
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

def main_loop(lora, payload_id, logger, spi):
    logger.info("Starting main loop")
    _init_watchdog()
    logger.info("Watchdog armed ({} s timeout)".format(config.WATCHDOG_TIMEOUT_S))

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

    opc = None
    opc_available = False

    try:
        opc = OPCN3(
            spi,
            logger=logger,
            warmup_s=config.OPC_WARMUP_S,
        )

        if not opc.ping():
            raise RuntimeError("OPC-N3 did not respond")

        logger.info("OPC-N3 info: {}".format(opc.info()))
        logger.info("OPC-N3 serial: {}".format(opc.serial()))
        logger.info("OPC-N3 firmware: {}".format(
            opc.firmware_version()
        ))

        opc.on(warmup=True)
        opc_available = True
        logger.info("OPC-N3 initialized and sampling")

    except Exception as err:
        # No matar la misión si el OPC falla: GPS, SD, LoRa y bombas
        # deben seguir operando.
        opc = None
        opc_available = False
        logger.warning("OPC-N3 unavailable: {}".format(err))

    start_time = time.time()
    data = {}
    _fast_next = False

    opc_hist = None
    next_opc_read = time.monotonic()

    _hb_offset = config.HEARTBEAT_OFFSETS.get(payload_id, 0)
    _next_heartbeat = time.monotonic() + _hb_offset

    while True:
        _feed_watchdog()
        led.blink(1)

        try:
            now_mono = time.monotonic()

            if opc is not None and now_mono >= next_opc_read:
                try:
                    opc_hist = opc.histogram()

                    logger.info(
                        "OPC PM: {:.3f}, {:.3f}, {:.3f} ug/m3".format(
                            opc_hist["opc_pm1"],
                            opc_hist["opc_pm25"],
                            opc_hist["opc_pm10"],
                        )
                    )
                except Exception as err:
                    opc_available = False
                    logger.warning("OPC-N3 read failed: {}".format(err))

                next_opc_read = now_mono + config.OPC_INTERVAL_S

            data = _collect_data(
                payload_id, gps, rh_sensor, pressure_sensor, bat,
                flow_meter, pump, valve, lora, start_time, opc_hist=opc_hist,
                opc_available=opc_available, fast_gps=_fast_next,
            )
            _fast_next = False
            _check_safety(pump, valve, data["battery_voltage"], logger)
            led.blink(2)
            logger.data(data)
            logger.info("Sensor data collected")
        except Exception as err:
            logger.error("Error reading data: {}".format(err))
            _fast_next = False
            try:
                lora.reset_radio()
                logger.info("LoRa reset after data error")
            except Exception as reset_err:
                logger.error("LoRa reset failed: {}".format(reset_err))
            _failed_reading_data(lora, logger)
            continue

        now_mono = time.monotonic()
        if now_mono >= _next_heartbeat:
            try:
                _send_with_type(lora, data, pack.MSG_TELEMETRY)
                logger.info("Heartbeat sent")
            except Exception as err:
                logger.error("Heartbeat send failed: {}".format(err))
            _next_heartbeat = now_mono + config.HEARTBEAT_INTERVAL_S

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
                _fast_next = True
                deadline = time.time() + 3
            elif got_cmd:
                break
            time.sleep(0.2)
