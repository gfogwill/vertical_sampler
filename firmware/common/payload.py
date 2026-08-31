import time
import config
import pack
from actuators import Pump, Valve
from power import PowerMonitor


def _snapshot(payload_id, pump, valve, power, logger):
    data = {
        "payload_id": payload_id,
        "pump_front_state": pump.front_state(),
        "pump_back_state": pump.back_state(),
        "valve_state": valve.state(),
    }

    try:
        data["battery_voltage"] = power.battery_voltage()
    except Exception as error:
        data["battery_voltage"] = None
        logger.warning("Battery read failed: {}".format(error))

    try:
        data["cpu_temperature"] = power.cpu_temperature()
    except Exception as error:
        data["cpu_temperature"] = None
        logger.warning("CPU temperature read failed: {}".format(error))

    return data


def _send(lora, data, msg_type):
    packet = data.copy()
    packet["msg_type"] = msg_type
    lora.send(pack.dict2bytes(packet))


def _handle_command(msg, data, pump, valve, power, lora, payload_id, logger):
    try:
        text = msg.decode().strip().lower()
        parts = text.split()
        if not parts:
            return

        command = parts[0]
        args = parts[1:]

        if command == "pump":
            if len(args) != 2:
                raise ValueError("pump requires: pump <front|back|both> <on|off>")
            pump.set_state(args[0], args[1])
        elif command == "valve":
            if len(args) != 1:
                raise ValueError("valve requires: valve <on|off>")
            valve.set_state(args[0])
        elif command != "data":
            raise ValueError("unknown command: " + command)

        data.update(_snapshot(payload_id, pump, valve, power, logger))
        _send(lora, data, pack.MSG_COMMAND_ACK)
        logger.info("cmd_ack: " + text)

    except Exception as error:
        logger.error("Command error: {}".format(error))
        data.update(_snapshot(payload_id, pump, valve, power, logger))
        try:
            _send(lora, data, pack.MSG_COMMAND_ERROR)
        except Exception as send_error:
            logger.error("cmd_err send failed: {}".format(send_error))


def main_loop(lora, payload_id, logger, spi=None):
    del spi
    pump = Pump(logger)
    valve = Valve(logger)
    power = PowerMonitor(logger)
    data = _snapshot(payload_id, pump, valve, power, logger)
    next_heartbeat = time.monotonic() + config.HEARTBEAT_OFFSETS.get(payload_id, 0)

    logger.info("LoRa actuator and power payload ready")

    while True:
        try:
            msg = lora.receive(timeout=0.2)
            if msg is not None:
                _handle_command(
                    msg, data, pump, valve, power, lora, payload_id, logger
                )

            now = time.monotonic()
            if now >= next_heartbeat:
                data.update(_snapshot(payload_id, pump, valve, power, logger))
                _send(lora, data, pack.MSG_TELEMETRY)
                logger.info("Heartbeat sent")
                next_heartbeat = now + config.HEARTBEAT_INTERVAL_S

        except Exception as error:
            logger.error("LoRa loop error: {}".format(error))
            time.sleep(0.5)
