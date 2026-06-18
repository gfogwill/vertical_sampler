import struct

KEYS = [
    ("gps_time",                    "L"),
    ("gps_latitude",                "f"),
    ("gps_longitude",               "f"),
    ("gps_altitude",                "f"),
    ("rh_sensor_humidity",          "f"),
    ("rh_sensor_temperature",       "f"),
    ("pressure_sensor_pressure",    "f"),
    ("pressure_sensor_temperature", "f"),
    ("flow",                        "f"),
    ("rssi",                        "i"),
    ("battery_voltage",             "f"),
    ("pump_front_state",            "i"),
    ("pump_back_state",             "i"),
    ("valve_state",                 "i"),
    ("cpu_temperature",             "f"),
    ("payload_id",                  "10s"),
]

FORMAT = "<" + "".join([t for _, t in KEYS])

INT_FILLVAL          = -999999
UNSIGNED_INT_FILLVAL = 4294967295
FLOAT_FILLVAL        = -1e9
_STR_LEN             = 10


def dict2bytes(d: dict) -> bytes:
    tup = []
    for key, fmt in KEYS:
        val = d.get(key)
        if fmt == "10s":
            if val is None:
                tup.append(b"\x00" * _STR_LEN)
            else:
                b = val.encode() if isinstance(val, str) else bytes(val)
                b = b[:_STR_LEN]
                # CircuitPython has no bytes.ljust — pad manually
                if len(b) < _STR_LEN:
                    b = b + b"\x00" * (_STR_LEN - len(b))
                tup.append(b)
        elif val is None:
            tup.append(
                UNSIGNED_INT_FILLVAL if fmt == "L" else
                INT_FILLVAL          if fmt == "i" else
                FLOAT_FILLVAL
            )
        else:
            tup.append(val)
    return struct.pack(FORMAT, *tup)


def bytes2dict(b) -> dict:
    tup = struct.unpack(FORMAT, b)
    result = {}
    for (k, fmt), v in zip(KEYS, tup):
        if fmt == "10s":
            result[k] = v.rstrip(b"\x00").decode(errors="ignore").strip()
        else:
            result[k] = v
    return result
