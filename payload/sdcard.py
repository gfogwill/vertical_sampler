import board
import os
import sdcardio
import storage
import json


class SDCard:
    def __init__(self, spi, payload_id: str):
        cs = board.GP18
        sdcard = sdcardio.SDCard(spi, cs)
        vfs = storage.VfsFat(sdcard)
        storage.mount(vfs, "/sd")

        self.log_fname = "/sd/{}_log.txt".format(payload_id)
        self.data_fname = self._next_data_fname(payload_id)

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
                return "/sd/{}".format(fname)
            n += 1

    def write_log(self, s: str):
        """Write a log line to the log file and print to console."""
        print(s, end="")
        try:
            with open(self.log_fname, "a") as f:
                f.write(s)
        except Exception as e:
            print("Failed to write log:", e)

    def write_data(self, d: dict):
        """Append a data dict as a JSON line to the data file."""
        try:
            json_str = json.dumps(d)
            with open(self.data_fname, "a") as f:
                f.write(json_str + "\n")
        except Exception as e:
            print("Failed to write data:", e)
