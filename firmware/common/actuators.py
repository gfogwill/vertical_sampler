import digitalio
import config


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
            self._front.value = value
        if location in ("back", "both"):
            self._back.value = value

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
        self._output.value = state == "on"

    def state(self):
        return int(self._output.value)
