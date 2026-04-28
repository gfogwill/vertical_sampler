import struct

KEYS = [
    ("gps_time", "L"),
    ("gps_latitude", "f"),
    ("gps_longitude", "f"),
    ("gps_altitude", "f"),
    ("rh_sensor_humidity", "f"),
    ("rh_sensor_temperature", "f"),
    ("pressure_sensor_pressure", "f"),
    ("pressure_sensor_temperature", "f"),
    ("flow", "f"),
    ("rssi", "i"),
    ("battery_voltage", "f"),
    ("pump_front_state", "i"),
    ("pump_back_state", "i"),
    ("valve_state", "i"),
]

FORMAT = "<" + "".join([t for _, t in KEYS])

INT_FILLVAL = -999999
UNSIGNED_INT_FILLVAL = 4294967295
FLOAT_FILLVAL = -1e9


def dict2bytes(d: dict) -> bytes:
    tup = [
        d.get(key,
              UNSIGNED_INT_FILLVAL if fmt == "L" else
              INT_FILLVAL if fmt == "i" else
              FLOAT_FILLVAL)
        if d.get(key) is not None
        else (UNSIGNED_INT_FILLVAL if fmt == "L" else
              INT_FILLVAL if fmt == "i" else
              FLOAT_FILLVAL)
        for key, fmt in KEYS
    ]
    return struct.pack(FORMAT, *tup)


def bytes2dict(b) -> dict:
    tup = struct.unpack(FORMAT, b)
    return {k: v for (k, _), v in zip(KEYS, tup)}
