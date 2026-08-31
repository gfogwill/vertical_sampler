import time
import busio
import config
import pack
from actuators import Pump, Valve
from power import PowerMonitor
from safety import SafetyInterlock
from sht85 import Sht85Sensor
from pressure_sensor import PressureSensor

SHT85_INTERVAL_S = 10.0
PRESSURE_INTERVAL_S = 10.0

def _snapshot(payload_id, pump, valve, power, logger):
    data = {"payload_id": payload_id, "pump_front_state": pump.front_state(), "pump_back_state": pump.back_state(), "valve_state": valve.state()}
    try: data["battery_voltage"] = power.battery_voltage()
    except Exception as e: data["battery_voltage"] = None; logger.warning("Battery read failed: {}".format(e))
    try: data["cpu_temperature"] = power.cpu_temperature()
    except Exception as e: data["cpu_temperature"] = None; logger.warning("CPU temperature read failed: {}".format(e))
    return data

def _send(lora, data, typ):
    p = data.copy(); p["msg_type"] = typ; lora.send(pack.dict2bytes(p))

def _update_sht85(data, sensor, logger):
    try:
        humidity_percent, temperature_c = sensor.humidity_and_temperature()
        data["rh_sensor_temperature"] = temperature_c
        data["rh_sensor_humidity"] = humidity_percent
        logger.info("SHT85: {:.2f} C, {:.2f} %RH".format(temperature_c, humidity_percent))
    except Exception as e: logger.warning("SHT85 read failed: {}".format(e))

def _update_pressure(data, sensor, logger):
    if sensor is None:
        return
    try:
        pressure_mbar = sensor.pressure()
        temperature_c = sensor.temperature()
        data["pressure_sensor_pressure"] = pressure_mbar
        data["pressure_sensor_temperature"] = temperature_c
        logger.info("Pressure: {:.2f} mbar, {:.2f} C".format(pressure_mbar, temperature_c))
    except Exception as e: logger.warning("Pressure read failed: {}".format(e))

def _handle_command(msg, data, pump, valve, power, safety, lora, payload_id, logger):
    try:
        parts = msg.decode().strip().lower().split()
        if not parts: return
        command, args = parts[0], parts[1:]
        on = (command == "pump" and len(args) == 2 and args[1] == "on") or (command == "valve" and len(args) == 1 and args[0] == "on")
        if safety.locked and on: raise ValueError("safety interlock active")
        if command == "pump":
            if len(args) != 2: raise ValueError("pump requires: pump <front|back|both> <on|off>")
            pump.set_state(args[0], args[1])
        elif command == "valve":
            if len(args) != 1: raise ValueError("valve requires: valve <on|off>")
            valve.set_state(args[0])
        elif command != "data": raise ValueError("unknown command: " + command)
        data.update(_snapshot(payload_id,pump,valve,power,logger)); _send(lora,data,pack.MSG_COMMAND_ACK); logger.info("cmd_ack: " + " ".join(parts))
    except Exception as e:
        logger.error("Command error: {}".format(e)); data.update(_snapshot(payload_id,pump,valve,power,logger))
        try: _send(lora,data,pack.MSG_COMMAND_ERROR)
        except Exception as x: logger.error("cmd_err send failed: {}".format(x))

def main_loop(lora,payload_id,logger,spi=None):
    del spi
    pump=Pump(logger); valve=Valve(logger); power=PowerMonitor(logger); safety=SafetyInterlock(logger)
    i2c = busio.I2C(scl=config.I2C_SCL, sda=config.I2C_SDA)
    sht85 = Sht85Sensor(logger, i2c)
    try:
        pressure_sensor = PressureSensor(logger, i2c)
    except Exception as e:
        pressure_sensor = None
        logger.warning("Pressure sensor unavailable: {}".format(e))
    data=_snapshot(payload_id,pump,valve,power,logger); now=time.monotonic(); next_heartbeat=now+config.HEARTBEAT_OFFSETS.get(payload_id,0); next_safety=now; next_sht85=now; next_pressure=now
    logger.info("LoRa actuator, power, safety, SHT85, and pressure payload ready")
    while True:
        try:
            msg=lora.receive(timeout=0.2)
            if msg is not None: _handle_command(msg,data,pump,valve,power,safety,lora,payload_id,logger)
            now=time.monotonic()
            if now>=next_safety: safety.update(power,pump,valve); next_safety=now+1.0
            if now>=next_sht85: _update_sht85(data,sht85,logger); next_sht85=now+SHT85_INTERVAL_S
            if now>=next_pressure: _update_pressure(data,pressure_sensor,logger); next_pressure=now+PRESSURE_INTERVAL_S
            if now>=next_heartbeat:
                data.update(_snapshot(payload_id,pump,valve,power,logger)); _send(lora,data,pack.MSG_TELEMETRY); logger.info("Heartbeat sent"); next_heartbeat=now+config.HEARTBEAT_INTERVAL_S
        except Exception as e: logger.error("LoRa loop error: {}".format(e)); time.sleep(0.5)
