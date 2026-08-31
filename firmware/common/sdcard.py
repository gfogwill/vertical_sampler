import json
import os
import sdcardio
import storage
import config


class SDCard:
    def __init__(self, spi, payload_id):
        self._available = False
        self._failure_reported = False
        self._sample_index = 0
        self.payload_id = payload_id
        self.session = None
        self.log_fname = None
        self.data_fname = None
        try:
            sdcard = sdcardio.SDCard(spi, config.SD_CS)
            storage.mount(storage.VfsFat(sdcard), "/sd")
            self.session = self._next_session(payload_id)
            stem = "{}_{:03d}".format(payload_id, self.session)
            self.log_fname = "/sd/{}.log".format(stem)
            self.data_fname = "/sd/{}.jsonl".format(stem)
            self._available = True
            self.write_data({"record_type": "session_start", "payload_id": payload_id, "session": self.session})
            print("SD card mounted OK. Data: {}".format(self.data_fname))
        except Exception as error:
            self._disable("mount", error)

    def _next_session(self, payload_id):
        existing = os.listdir("/sd")
        session = 1
        while True:
            stem = "{}_{:03d}".format(payload_id, session)
            if "{}.jsonl".format(stem) not in existing and "{}.log".format(stem) not in existing:
                return session
            session += 1

    @property
    def available(self):
        return self._available

    def _disable(self, operation, error):
        self._available = False
        if not self._failure_reported:
            self._failure_reported = True
            print("SD unavailable after {}: {} — logging to console only".format(operation, error))

    def write_log(self, line):
        print(line, end="")
        if not self._available:
            return
        try:
            with open(self.log_fname, "a") as handle:
                handle.write(line)
        except Exception as error:
            self._disable("log write", error)

    def write_data(self, data):
        if not self._available:
            return
        try:
            record = dict(data)
            record.setdefault("record_type", "telemetry")
            if record["record_type"] == "telemetry":
                self._sample_index += 1
                record["sample_index"] = self._sample_index
            with open(self.data_fname, "a") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception as error:
            self._disable("data write", error)
