import time
import busio
import config
import led
import pack
from actuators import Pump, Valve
from flowmeter import FlowMeter
from gps import Gps
from power import PowerMonitor
from safety import SafetyInterlock
from sht85 import Sht85Sensor
from pressure_sensor import PressureSensor

SHT85_INTERVAL_S=10.0
PRESSURE_INTERVAL_S=10.0
FLOW_INTERVAL_S=10.0

def _snapshot(payload_id,pump,valve,power,logger):
    data={"payload_id":payload_id,"pump_front_state":pump.front_state(),"pump_back_state":pump.back_state(),"valve_state":valve.state()}
    try: data["battery_voltage"]=power.battery_voltage()
    except Exception as e: data["battery_voltage"]=None; logger.warning("Battery read failed: {}".format(e))
    try: data["cpu_temperature"]=power.cpu_temperature()
    except Exception as e: data["cpu_temperature"]=None; logger.warning("CPU temperature read failed: {}".format(e))
    return data

def _send(lora,data,typ,status_led):
    packet=data.copy(); packet["msg_type"]=typ; lora.send(pack.dict2bytes(packet)); status_led.tx()

def _update_sht85(data,sensor,logger,status_led):
    try:
        humidity,temperature=sensor.humidity_and_temperature(); data["rh_sensor_temperature"]=temperature; data["rh_sensor_humidity"]=humidity
        logger.info("SHT85: {:.2f} C, {:.2f} %RH".format(temperature,humidity)); status_led.sensors_updated()
    except Exception as e: logger.warning("SHT85 read failed: {}".format(e))

def _update_pressure(data,sensor,logger,status_led):
    if sensor is None: return
    try:
        pressure=sensor.pressure(); temperature=sensor.temperature(); data["pressure_sensor_pressure"]=pressure; data["pressure_sensor_temperature"]=temperature
        logger.info("Pressure: {:.2f} mbar, {:.2f} C".format(pressure,temperature)); status_led.sensors_updated()
    except Exception as e: logger.warning("Pressure read failed: {}".format(e))

def _update_flow(data,sensor,logger,status_led):
    if sensor is None: return
    try:
        flow=sensor.flow_l_min(); data["flow"]=flow; logger.info("Flow: {:.3f} L/min".format(flow)); status_led.sensors_updated()
    except Exception as e: logger.warning("Flow read failed: {}".format(e))

def _update_rssi(data,lora,logger):
    try: data["rssi"]=int(lora.rssi()); logger.info("LoRa RX RSSI: {} dBm".format(data["rssi"]))
    except Exception as e: logger.warning("LoRa RSSI read failed: {}".format(e))

def _handle_command(msg,data,pump,valve,power,safety,lora,payload_id,logger,status_led):
    try:
        parts=msg.decode().strip().lower().split()
        if not parts: return
        command,args=parts[0],parts[1:]; on=(command=="pump" and len(args)==2 and args[1]=="on") or (command=="valve" and len(args)==1 and args[0]=="on")
        if safety.locked and on: raise ValueError("safety interlock active")
        if command=="pump":
            if len(args)!=2: raise ValueError("pump requires: pump <front|back|both> <on|off>")
            pump.set_state(args[0],args[1])
        elif command=="valve":
            if len(args)!=1: raise ValueError("valve requires: valve <on|off>")
            valve.set_state(args[0])
        elif command!="data": raise ValueError("unknown command: "+command)
        data.update(_snapshot(payload_id,pump,valve,power,logger)); _send(lora,data,pack.MSG_COMMAND_ACK,status_led); logger.info("cmd_ack: "+" ".join(parts))
    except Exception as e:
        logger.error("Command error: {}".format(e)); data.update(_snapshot(payload_id,pump,valve,power,logger))
        try: _send(lora,data,pack.MSG_COMMAND_ERROR,status_led)
        except Exception as x: logger.error("cmd_err send failed: {}".format(x))

def main_loop(lora,payload_id,logger,spi=None):
    del spi
    pump=Pump(logger); valve=Valve(logger); power=PowerMonitor(logger); safety=SafetyInterlock(logger); status_led=led.StatusLed(logger)
    i2c=busio.I2C(scl=config.I2C_SCL,sda=config.I2C_SDA); sht85=Sht85Sensor(logger,i2c)
    try: pressure_sensor=PressureSensor(logger,i2c)
    except Exception as e: pressure_sensor=None; logger.warning("Pressure sensor unavailable: {}".format(e))
    try: flow_meter=FlowMeter(logger)
    except Exception as e: flow_meter=None; logger.warning("Flow meter unavailable: {}".format(e))
    try: gps=Gps(logger)
    except Exception as e: gps=None; logger.warning("GPS unavailable: {}".format(e))
    data=_snapshot(payload_id,pump,valve,power,logger); data["flow"]=None; data["rssi"]=None
    if gps is not None: data.update(gps.fields())
    now=time.monotonic(); next_heartbeat=now+config.HEARTBEAT_OFFSETS.get(payload_id,0); next_safety=now; next_sht85=now; next_pressure=now; next_flow=now
    logger.info("LoRa actuator, power, GPS, safety, SHT85, pressure, flow, and LED payload ready")
    while True:
        try:
            now=time.monotonic(); status_led.tick(now)
            if gps is not None:
                sync_event=gps.update()
                if sync_event is not None: logger.data(sync_event)
            msg=lora.receive(timeout=0.2)
            if msg is not None: status_led.rx(); _update_rssi(data,lora,logger); _handle_command(msg,data,pump,valve,power,safety,lora,payload_id,logger,status_led)
            now=time.monotonic(); sampled=False
            if now>=next_safety: safety.update(power,pump,valve); next_safety=now+1.0
            if now>=next_sht85: _update_sht85(data,sht85,logger,status_led); next_sht85=now+SHT85_INTERVAL_S; sampled=True
            if now>=next_pressure: _update_pressure(data,pressure_sensor,logger,status_led); next_pressure=now+PRESSURE_INTERVAL_S; sampled=True
            if now>=next_flow: _update_flow(data,flow_meter,logger,status_led); next_flow=now+FLOW_INTERVAL_S; sampled=True
            if sampled:
                data.update(_snapshot(payload_id,pump,valve,power,logger))
                if gps is not None: data.update(gps.fields())
                logger.data(data)
            if now>=next_heartbeat:
                data.update(_snapshot(payload_id,pump,valve,power,logger))
                if gps is not None: data.update(gps.fields())
                _send(lora,data,pack.MSG_TELEMETRY,status_led); logger.info("Heartbeat sent"); next_heartbeat=now+config.HEARTBEAT_INTERVAL_S
            status_led.tick(time.monotonic())
        except Exception as e:
            logger.error("LoRa loop error: {}".format(e)); status_led.error(); time.sleep(0.5)
