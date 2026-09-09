import time
import busio
import digitalio
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
OPC_HISTOGRAM_INTERVAL_S=10.0

def _snapshot(payload_id,pump,valve,power,logger):
    data={"payload_id":payload_id,"pump_front_state":pump.front_state(),"pump_back_state":pump.back_state(),"valve_state":valve.state() if valve is not None else None}
    try: data["battery_voltage"]=power.battery_voltage()
    except Exception as e: data["battery_voltage"]=None; logger.warning("Battery read failed: {}".format(e))
    try: data["cpu_temperature"]=power.cpu_temperature()
    except Exception as e: data["cpu_temperature"]=None; logger.warning("CPU temperature read failed: {}".format(e))
    return data

def _send(lora,data,typ,status_led):
    packet=data.copy(); packet["msg_type"]=typ
    if not lora.send(pack.dict2bytes(packet)):
        raise RuntimeError("LoRa transmit timed out")
    status_led.tx()

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

def _update_opc_histogram(data,opc,logger,status_led):
    if opc is None: return
    try:
        raw=opc.histogram(raw=True)
        for i in range(24):
            data["opc_bin_{}".format(i)]=raw["bin_{}".format(i)]
        data["opc_temperature"]=opc._convert_temperature(raw["temperature_raw"])
        data["opc_humidity"]=opc._convert_relative_humidity(raw["relative_humidity_raw"])
        data["opc_sample_flow"]=raw["sfr_raw"]/100.0
        data["opc_laser_status"]=raw["laser_status"]
        total=sum(raw["bin_{}".format(i)] for i in range(24))
        logger.info("OPC histogram read: raw_total={} laser={}".format(total,raw["laser_status"]))
        status_led.sensors_updated()
    except Exception as e: logger.warning("OPC histogram read failed: {}".format(e))

def _update_rssi(data,lora,logger):
    try: data["rssi"]=int(lora.rssi()); logger.info("LoRa RX RSSI: {} dBm".format(data["rssi"]))
    except Exception as e: logger.warning("LoRa RSSI read failed: {}".format(e))

def _handle_command(msg,data,pump,valve,power,safety,lora,payload_id,logger,status_led):
    try:
        parts=msg.decode().strip().lower().split()
        if not parts: return
        logger.info("Command received: "+" ".join(parts))
        command,args=parts[0],parts[1:]
        if command=="pump":
            if len(args)!=2: raise ValueError("pump requires: pump <front|back|both> <on|off>")
            if args[0] not in ("front","back","both"): raise ValueError("pump location: front, back, or both")
            if args[1] not in ("on","off"): raise ValueError("pump state: on or off")
            if not pump.supports(args[0]): raise ValueError("pump {} is not installed".format(args[0]))
        elif command=="valve":
            if valve is None: raise ValueError("valve is not installed")
            if len(args)!=1: raise ValueError("valve requires: valve <on|off>")
            if args[0] not in ("on","off"): raise ValueError("valve state: on or off")
        elif command!="data": raise ValueError("unknown command: "+command)
        on=(command=="pump" and args[1]=="on") or (command=="valve" and args[0]=="on")
        if safety.locked and on: raise ValueError("safety interlock active")
        if command=="pump":
            pump.set_state(args[0],args[1])
        elif command=="valve":
            valve.set_state(args[0])
        data.update(_snapshot(payload_id,pump,valve,power,logger)); _send(lora,data,pack.MSG_COMMAND_ACK,status_led); logger.info("cmd_ack: "+" ".join(parts))
    except Exception as e:
        logger.error("Command error: {}".format(e)); data.update(_snapshot(payload_id,pump,valve,power,logger))
        try: _send(lora,data,pack.MSG_COMMAND_ERROR,status_led)
        except Exception as x: logger.error("cmd_err send failed: {}".format(x))

def main_loop(lora,payload_id,logger,spi=None,shared_spi=None,pump_locations=("front","back"),has_valve=True,has_opc=True):
    pump=Pump(logger,locations=pump_locations)
    valve=Valve(logger) if has_valve else None
    disabled_valve_output=None
    if not has_valve:
        disabled_valve_output=digitalio.DigitalInOut(config.ELECTROVALVE)
        disabled_valve_output.switch_to_output(value=False)
        logger.info("Electro-valve output held off: valve not installed")
    power=PowerMonitor(logger); safety=SafetyInterlock(logger); status_led=led.StatusLed(logger)
    i2c=busio.I2C(scl=config.I2C_SCL,sda=config.I2C_SDA); sht85=Sht85Sensor(logger,i2c)
    try: pressure_sensor=PressureSensor(logger,i2c)
    except Exception as e: pressure_sensor=None; logger.warning("Pressure sensor unavailable: {}".format(e))
    try: flow_meter=FlowMeter(logger)
    except Exception as e: flow_meter=None; logger.warning("Flow meter unavailable: {}".format(e))
    try: gps=Gps(logger)
    except Exception as e: gps=None; logger.warning("GPS unavailable: {}".format(e))
    opc=None
    if has_opc and spi is not None:
        try:
            from opc_n3 import OPCN3
            opc=OPCN3(spi,logger,shared_spi=shared_spi)
            opc.on(warmup=True)
        except Exception as e: opc=None; logger.warning("OPC-N3 unavailable: {}".format(e))
    elif has_opc:
        logger.warning("OPC-N3 disabled: no shared SPI bus provided")
    data=_snapshot(payload_id,pump,valve,power,logger); data["flow"]=None; data["rssi"]=None
    for i in range(24): data["opc_bin_{}".format(i)]=None
    data["opc_temperature"]=None; data["opc_humidity"]=None; data["opc_sample_flow"]=None; data["opc_laser_status"]=None
    if gps is not None: data.update(gps.fields())
    now=time.monotonic(); next_heartbeat=now+config.HEARTBEAT_OFFSETS.get(payload_id,0); next_safety=now
    next_sht85=now; next_pressure=now+2.0; next_flow=now+4.0
    next_opc_histogram=now+6.0 if opc is not None else None
    capabilities=["pump {}".format("/".join(pump_locations))]
    if has_valve: capabilities.append("valve")
    if has_opc: capabilities.append("OPC-N3")
    logger.info("Payload ready: "+", ".join(capabilities)+", power, GPS, safety, SHT85, pressure, flow, and LED")
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
            if next_opc_histogram is not None and now>=next_opc_histogram: _update_opc_histogram(data,opc,logger,status_led); next_opc_histogram=now+OPC_HISTOGRAM_INTERVAL_S; sampled=True
            if sampled:
                data.update(_snapshot(payload_id,pump,valve,power,logger))
                if gps is not None: data.update(gps.fields())
                logger.data(data)
            if now>=next_heartbeat:
                next_heartbeat=now+config.HEARTBEAT_INTERVAL_S
                data.update(_snapshot(payload_id,pump,valve,power,logger))
                if gps is not None: data.update(gps.fields())
                try:
                    _send(lora,data,pack.MSG_TELEMETRY,status_led); logger.info("Heartbeat sent")
                except Exception as e:
                    logger.error("Heartbeat send failed: {}".format(e))
            status_led.tick(time.monotonic())
        except Exception as e:
            logger.error("LoRa loop error: {}".format(e)); status_led.error(); time.sleep(0.5)
