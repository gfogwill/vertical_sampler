import analogio
import microcontroller
import config


class PowerMonitor:
    def __init__(self, logger):
        self._battery = analogio.AnalogIn(config.BATTERY_MONITOR)
        logger.info("Power monitor initialized")

    def battery_voltage(self):
        adc_voltage = self._battery.value * 3.3 / 65535
        return adc_voltage * config.BATTERY_CAL_FACTOR

    def cpu_temperature(self):
        return microcontroller.cpu.temperature
