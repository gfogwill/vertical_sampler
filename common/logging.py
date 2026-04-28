import time

LOG_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "DATA": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class Logger:
    def __init__(self, name, level="DEBUG", sd_card=None):
        self.name = name
        self.level = LOG_LEVELS[level]
        self.sd_card = sd_card
        if self.sd_card is None:
            raise ValueError("SDCard instance must be provided")

    def _log(self, level_name, message):
        if LOG_LEVELS[level_name] >= self.level:
            log_time = self._get_formatted_time()
            log_message = "{} - {} - {} - {}".format(log_time, level_name, self.name, message)
            try:
                self.sd_card.write(log_message + "\n")
            except Exception as e:
                print("Failed to write to log file:", e)

    def data(self, message):
        self._log("DATA", message)

    def debug(self, message):
        self._log("DEBUG", message)

    def info(self, message):
        self._log("INFO", message)

    def warning(self, message):
        self._log("WARNING", message)

    def error(self, message):
        self._log("ERROR", message)

    def critical(self, message):
        self._log("CRITICAL", message)

    def _get_formatted_time(self):
        year, month, day, hour, minute, second, *_ = time.localtime()
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            year, month, day, hour, minute, second
        )


def getLogger(name, sd_card):
    return Logger(name, sd_card=sd_card)
