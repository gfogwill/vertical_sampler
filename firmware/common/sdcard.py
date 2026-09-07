import json
import os
import sdcardio
import storage
import config


class SDCard:
    def __init__(self, spi, payload_id, shared_spi=None):
        self._available = False
        self._mount_failure_reported = False
        self._write_failures = 0
        self._sample_index = 0
        self.payload_id = payload_id
        self.session = None
        self.shared_spi = shared_spi
        self.data_fname = "{}_001.jsonl".format(payload_id)
        try:
            if self.shared_spi is not None:
                self.shared_spi.before_sd()
            sdcard = sdcardio.SDCard(spi, config.SD_CS)
            storage.mount(storage.VfsFat(sdcard), "/sd")
            self.session = self._next_session(payload_id)
            self.data_fname = "/sd/{}_{:03d}.jsonl".format(payload_id, self.session)
            self._available = True
            self.write_record({"record_type": "session_start", "payload_id": payload_id, "session": self.session})
            print("SD card mounted OK. Data: {}".format(self.data_fname))
        except Exception as error:
            self._disable_mount(error)

    def _next_session(self, payload_id):
        try:
            existing = os.listdir("/sd")
        except Exception:
            existing = []
        session = 1
        while True:
            filename = "{}_{:03d}.jsonl".format(payload_id, session)
            if filename not in existing:
                return session
            session += 1

    @property
    def available(self):
        return self._available

    def _disable_mount(self, error):
        # Permanent: the card never mounted, so no write can ever succeed.
        self._available = False
        if not self._mount_failure_reported:
            self._mount_failure_reported = True
            print("SD unavailable after mount: {} — logging to console only".format(error))

    def write_record(self, record):
        if not self._available:
            return
        line = json.dumps(record) + "\n"
        try:
            if self.shared_spi is not None:
                self.shared_spi.before_sd()
            with open(self.data_fname, "a") as handle:
                handle.write(line)
                handle.flush()
        except Exception as error:
            # Transient: drop this one record but keep the card enabled so
            # the next sample can still be written. A single glitch must
            # not silence logging for the rest of the mission.
            self._write_failures += 1
            print("SD write failed ({}): {} — record dropped, retrying next sample".format(self._write_failures, error))

    def write_log(self, record):
        self.write_record(record)

    def write_data(self, data):
        record = dict(data)
        record.setdefault("record_type", "telemetry")
        if record["record_type"] == "telemetry":
            self._sample_index += 1
            record["sample_index"] = self._sample_index
        self.write_record(record)
