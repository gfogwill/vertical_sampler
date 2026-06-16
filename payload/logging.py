import time
import json


class Logger:
    LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "DATA": 3, "ERROR": 4}

    def __init__(self, name, sd_card, level="DEBUG"):
        self.name = name
        self.sd_card = sd_card
        self.level = self.LEVELS.get(level, 0)
        if sd_card.available:
            self._log("INFO", "Data file: {}".format(sd_card.data_fname))
        else:
            self._log("WARNING", "SD not available — logging to console only")

    def _log(self, level, message):
        if self.LEVELS.get(level, 0) < self.level:
            return
        t = time.localtime()
        timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            t.tm_year, t.tm_mon, t.tm_mday,
            t.tm_hour, t.tm_min, t.tm_sec
        )
        line = "{} - {} - {} - {}\n".format(timestamp, level, self.name, message)
        self.sd_card.write_log(line)

    def debug(self, message):
        self._log("DEBUG", message)

    def info(self, message):
        self._log("INFO", message)

    def warning(self, message):
        self._log("WARNING", message)

    def error(self, message):
        self._log("ERROR", message)

    def data(self, d):
        self._log("DATA", "t={} -> {}".format(d.get("gps_time", "?"), self.sd_card.data_fname))
        self.sd_card.write_data(d)


def getLogger(name, sd_card, level="DEBUG"):
    return Logger(name, sd_card, level)
