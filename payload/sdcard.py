import board
import os
import sdcardio
import storage
import json


class SDCard:
    """SD card handler. If the card fails to mount, operates in degraded mode
    (no file writes) so the rest of the system keeps running."""

    def __init__(self, spi, payload_id: str):
        self._available = False
        self.log_fname = "{}_log.txt".format(payload_id)
        self.data_fname = "{}_001.jsonl".format(payload_id)
        try:
            cs = board.GP18
            sdcard = sdcardio.SDCard(spi, cs)
            vfs = storage.VfsFat(sdcard)
            storage.mount(vfs, "/sd")
            self.log_fname = "/sd/{}_log.txt".format(payload_id)
            self.data_fname = "/sd/" + self._next_data_fname(payload_id)
            self._available = True
            print("SD card mounted OK. Data: {}".format(self.data_fname))
        except Exception as e:
            print("SD card unavailable: {} — running without SD".format(e))

    def _next_data_fname(self, payload_id):
        """Find next available matorova_001.jsonl, matorova_002.jsonl, etc."""
        try:
            existing = os.listdir("/sd")
        except Exception:
            existing = []
        n = 1
        while True:
            fname = "{}_{:03d}.jsonl".format(payload_id, n)
            if fname not in existing:
                return fname
            n += 1

    @property
    def available(self):
        return self._available

    def write_log(self, s: str):
        """Write a log line to the log file and print to console."""
        print(s, end="")
        if not self._available:
            return
        try:
            with open(self.log_fname, "a") as f:
                f.write(s)
        except Exception as e:
            print("SD write_log failed: {}".format(e))
            self._available = False

    def write_data(self, d: dict):
        """Append a data dict as a JSON line to the data file."""
        if not self._available:
            return
        try:
            with open(self.data_fname, "a") as f:
                f.write(json.dumps(d) + "\n")
        except Exception as e:
            print("SD write_data failed: {}".format(e))
            self._available = False
