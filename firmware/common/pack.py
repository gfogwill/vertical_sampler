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

# Legacy KEYS: same as KEYS but without the leading msg_type field.
# Used for backward compat when the peer has an older pack.py.
_KEYS_LEGACY = KEYS[1:]

FORMAT        = "<" + "".join([t for _, t in KEYS])
_FORMAT_LEGACY = "<" + "".join([t for _, t in _KEYS_LEGACY])

# Pre-computed sizes so callers can sanity-check incoming bytes.
WIRE_SIZE   = struct.calcsize(FORMAT)
LEGACY_SIZE = struct.calcsize(_FORMAT_LEGACY)

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
    """Serialize a data dict to wire bytes using the current FORMAT."""
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
            tup.append(int(val))
        else:
            tup.append(val)
    return struct.pack(FORMAT, *tup)


def bytes2dict(b) -> dict:
    """Deserialize wire bytes to a dict.

    Accepts both the current FORMAT (with msg_type, WIRE_SIZE bytes) and
    the legacy FORMAT (without msg_type, LEGACY_SIZE bytes).  Any other
    length raises ValueError with a descriptive message so callers can
    log it instead of swallowing it silently.
    """
    n = len(b)
    if n == WIRE_SIZE:
        keys_used = KEYS
        fmt_used  = FORMAT
        legacy    = False
    elif n == LEGACY_SIZE:
        keys_used = _KEYS_LEGACY
        fmt_used  = _FORMAT_LEGACY
        legacy    = True
    else:
        raise ValueError(
            "pack: bad packet length {} (expected {} or {})".format(
                n, WIRE_SIZE, LEGACY_SIZE
            )
        )

    tup = struct.unpack(fmt_used, b)
    result = {}
    for (k, fmt), v in zip(keys_used, tup):
        if fmt.endswith("s"):
            try:
                result[k] = v.rstrip(b"\x00").decode()
            except Exception:
                result[k] = ""
        else:
            result[k] = v

    if legacy:
        # Tag as telemetry so downstream code can use msg_type safely
        result["msg_type"] = MSG_TELEMETRY

    return result
