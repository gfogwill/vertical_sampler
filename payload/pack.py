import struct

KEYS = [
    ("msg_type",                    "12s"),
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
_MSG_TYPE_LEN        = 12

# Integer struct format chars: fields with these formats must be int, not float.
_INT_FMTS = frozenset("iIlL")

# Valid msg_type values (max 12 chars incl. null padding)
MSG_TELEMETRY     = "telemetry"   # 9 chars
MSG_COMMAND_ACK   = "cmd_ack"     # 7 chars
MSG_COMMAND_ERROR = "cmd_err"     # 7 chars


def dict2bytes(d: dict) -> bytes:
    # Transitional: dicts without msg_type default to telemetry
    if "msg_type" not in d:
        d = dict(d)
        d["msg_type"] = MSG_TELEMETRY
    tup = []
    for key, fmt in KEYS:
        val = d.get(key)
        if fmt.endswith("s"):
            length = int(fmt[:-1])
            if val is None:
                tup.append(b"\x00" * length)
            else:
                b = val.encode() if isinstance(val, str) else bytes(val)
                b = b[:length]
                if len(b) < length:
                    b = b + b"\x00" * (length - len(b))
                tup.append(b)
        elif val is None:
            tup.append(
                UNSIGNED_INT_FILLVAL if fmt == "L" else
                INT_FILLVAL          if fmt == "i" else
                FLOAT_FILLVAL
            )
        elif fmt in _INT_FMTS:
            # CircuitPython struct.pack does not coerce float->int.
            # Cast explicitly so callers can pass floats for integer fields
            # (e.g. elapsed_time = time.time() - start_time for gps_time).
            tup.append(int(val))
        else:
            tup.append(val)
    return struct.pack(FORMAT, *tup)


def bytes2dict(b) -> dict:
    tup = struct.unpack(FORMAT, b)
    result = {}
    for (k, fmt), v in zip(KEYS, tup):
        if fmt.endswith("s"):
            try:
                result[k] = v.rstrip(b"\x00").decode()
            except Exception:
                result[k] = ""
        else:
            result[k] = v
    return result
