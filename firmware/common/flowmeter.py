import time
import analogio
import config


class FlowMeter:
    def __init__(self, logger, oversample_n=16, sample_delay_s=0.001):
        self._logger = logger
        self._input = analogio.AnalogIn(config.FLOWMETER)
        self._oversample_n = oversample_n
        self._sample_delay_s = sample_delay_s
        self._logger.info("Flow meter initialized")

    def flow_l_min(self):
        total = 0
        for _ in range(self._oversample_n):
            total += self._input.value
            if self._sample_delay_s:
                time.sleep(self._sample_delay_s)
        raw_average = total / self._oversample_n
        voltage_adc = raw_average * 3.3 / 65535.0
        voltage_sensor = voltage_adc / config.FLOW_DIVIDER_RATIO
        flow_l_min = (voltage_sensor / config.FLOW_FULL_SCALE_V * config.FLOW_FULL_SCALE_LMIN - config.FLOW_OFFSET_LMIN)
        return max(0.0, flow_l_min)
