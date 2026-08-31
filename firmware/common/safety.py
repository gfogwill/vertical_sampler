import config


class SafetyInterlock:
    def __init__(self, logger):
        self._logger = logger
        self.locked = False
        self._warn_battery = False
        self._warn_cpu = False

    def update(self, power, pump, valve):
        battery = power.battery_voltage()
        cpu = power.cpu_temperature()
        critical = (
            battery <= config.BATTERY_CUTOFF_V
            or cpu >= config.CPU_TEMP_CRITICAL_C
        )

        if critical:
            pump.set_state("both", "off")
            valve.set_state("off")
            if not self.locked:
                self._logger.error(
                    "Safety interlock: battery={:.2f} V cpu={:.1f} C".format(
                        battery, cpu
                    )
                )
            self.locked = True
            return True

        if self.locked:
            self._logger.info("Safety interlock cleared")
            self.locked = False

        battery_warn = battery <= config.BATTERY_WARN_V
        cpu_warn = cpu >= config.CPU_TEMP_WARN_C

        if battery_warn and not self._warn_battery:
            self._logger.warning("Battery low: {:.2f} V".format(battery))
        if cpu_warn and not self._warn_cpu:
            self._logger.warning("CPU warm: {:.1f} C".format(cpu))

        self._warn_battery = battery_warn
        self._warn_cpu = cpu_warn
        return False
