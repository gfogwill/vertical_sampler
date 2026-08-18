import os
import sdcardio
import storage
import json
import config

class SDCard:
    def __init__(self, spi, payload_id):
        self._available = False
        self.log_fname = "{}_log.txt".format(payload_id)
        self.data_fname = "{}_001.jsonl".format(payload_id)
        try:
            cs = config.SD_CS
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
        try: existing = os.listdir("/sd")
        except Exception: existing = []
        n = 1
        while True:
            fname = "{}_{:03d}.jsonl".format(payload_id, n)
            if fname not in existing: return fname
            n += 1
    @property
    def available(self): return self._available
    def write_log(self, s):
        print(s, end="")
        if not self._available: return
        try:
            with open(self.log_fname, "a") as f: f.write(s)
        except Exception as e:
            print("SD write_log failed: {}".format(e)); self._available = False
    def write_data(self, d):
        if not self._available: return
        try:
            with open(self.data_fname, "a") as f: f.write(json.dumps(d) + "\n")
        except Exception as e:
            print("SD write_data failed: {}".format(e)); self._available = False
