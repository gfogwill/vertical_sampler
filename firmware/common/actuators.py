import digitalio
import config


def _set_output(output, value):
    if output.value != value:
        output.value = value


class Pump:
    def __init__(self, logger):
        self._front = digitalio.DigitalInOut(config.PUMP_FRONT)
        self._front.switch_to_output(value=False)
        self._back = digitalio.DigitalInOut(config.PUMP_BACK)
        self._back.switch_to_output(value=False)
        logger.info("Pump initialized: off")

    def set_state(self, location, state):
        if location not in ("front", "back", "both"):
            raise ValueError("pump location: front, back, or both")
        if state not in ("on", "off"):
            raise ValueError("pump state: on or off")
        value = state == "on"
        if location in ("front", "both"):
            _set_output(self._front, value)
        if location in ("back", "both"):
            _set_output(self._back, value)

    def front_state(self):
        return int(self._front.value)

    def back_state(self):
        return int(self._back.value)


class Valve:
    def __init__(self, logger):
        self._output = digitalio.DigitalInOut(config.ELECTROVALVE)
        self._output.switch_to_output(value=False)
        logger.info("Valve initialized: off")

    def set_state(self, state):
        if state not in ("on", "off"):
            raise ValueError("valve state: on or off")
        _set_output(self._output, state == "on")

    def state(self):
        return int(self._output.value)
