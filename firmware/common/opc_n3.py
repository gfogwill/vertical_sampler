"""
CircuitPython driver for Alphasense OPC-N3.

SPI configuration:
- Mode 1: polarity=0, phase=1
- 500 kHz recommended initial baudrate
- OPC-N3 CS: config.OPC_CS

This driver supports:
- Device info, serial number, firmware version and ping
- Fan/laser power state and control
- Reset
- PM1 / PM2.5 / PM10
- Full 24-bin histogram
- OPC-N3 configuration read
"""

import struct
import time

import digitalio
from adafruit_bus_device.spi_device import SPIDevice

import config


OPC_READY = 0xF3
OPC_BUSY = 0x31

CMD_WRITE_POWER_STATE = 0x03
CMD_READ_POWER_STATE = 0x13
CMD_READ_INFO_STRING = 0x3F
CMD_READ_SERIAL_STRING = 0x10
CMD_READ_FW_VERSION = 0x12
CMD_READ_HISTOGRAM = 0x30
CMD_READ_PM = 0x32
CMD_READ_CONFIG = 0x3C
CMD_CHECK_STATUS = 0xCF
CMD_RESET = 0x06

POPT_FAN = 1
POPT_LASER_DAC = 2
POPT_LASER_SWITCH = 3
POPT_GAIN_TOGGLE = 4


class OPCError(OSError):
    pass


class DataModel:
    """Binary OPC data model based on a list of (name, struct-format)."""

    def __init__(self, fields):
        self.fields = fields
        self.names = [field[0] for field in fields]
        self.fmt = "<" + "".join(field[1] for field in fields)
        self.size = struct.calcsize(self.fmt)

    def unpack(self, raw):
        if len(raw) != self.size:
            raise ValueError(
                "OPC data length {} != expected {}".format(
                    len(raw), self.size
                )
            )
        return dict(zip(self.names, struct.unpack(self.fmt, raw)))


POWER_STATE_MODEL = DataModel([
    ("fan_on", "B"),
    ("laser_on", "B"),
    ("fan_dac", "B"),
    ("laser_dac", "B"),
    ("laser_switch", "B"),
    ("gain_toggle", "B"),
])

PM_MODEL = DataModel([
    ("pm1", "f"),
    ("pm25", "f"),
    ("pm10", "f"),
    ("checksum", "H"),
])

HISTOGRAM_FIELDS = []

for index in range(24):
    HISTOGRAM_FIELDS.append(("bin_{}".format(index), "H"))

HISTOGRAM_FIELDS.extend([
    ("bin1_mtof_raw", "B"),
    ("bin3_mtof_raw", "B"),
    ("bin5_mtof_raw", "B"),
    ("bin7_mtof_raw", "B"),
    ("sampling_period_raw", "H"),
    ("sfr_raw", "H"),
    ("temperature_raw", "H"),
    ("relative_humidity_raw", "H"),
    ("pm1", "f"),
    ("pm25", "f"),
    ("pm10", "f"),
    ("reject_glitch", "H"),
    ("reject_long_tof", "H"),
    ("reject_ratio", "H"),
    ("reject_out_of_range", "H"),
    ("fan_rev_count", "H"),
    ("laser_status", "H"),
    ("checksum", "H"),
])

HISTOGRAM_MODEL = DataModel(HISTOGRAM_FIELDS)

CONFIG_FIELDS = []

for index in range(25):
    CONFIG_FIELDS.append(("bin_boundary_{}".format(index), "H"))

for index in range(25):
    CONFIG_FIELDS.append(("bin_boundary_d_{}".format(index), "H"))

for index in range(24):
    CONFIG_FIELDS.append(("bin_weight_{}".format(index), "H"))

CONFIG_FIELDS.extend([
    ("m_a", "H"),
    ("m_b", "H"),
    ("m_c", "H"),
    ("max_tof", "H"),
    ("am_sampling_interval_count", "H"),
    ("am_idle_interval_count", "H"),
    ("am_max_data_arrays_in_file", "H"),
    ("am_only_save_pm_data", "B"),
    ("am_fan_on_idle", "B"),
    ("am_laser_on_idle", "B"),
    ("tof_to_sfr_factor", "B"),
    ("pvp", "B"),
    ("bin_weighting_index", "B"),
])

CONFIG_MODEL = DataModel(CONFIG_FIELDS)


class OPCN3:
    """
    Alphasense OPC-N3 SPI driver for CircuitPython.

    The caller creates the shared SPI bus. This allows OPC, SD card and
    RFM9x LoRa to share SCK/MOSI/MISO safely, each with its own CS pin.
    """

    def __init__(
        self,
        spi,
        logger=None,
        baudrate=500000,
        warmup_s=5.0,
    ):
        self.logger = logger
        self.warmup_s = warmup_s

        self.cs = digitalio.DigitalInOut(config.OPC_CS)
        self.device = SPIDevice(
            spi,
            self.cs,
            baudrate=baudrate,
            polarity=0,
            phase=1,
        )

        self._tx_one = bytearray(1)
        self._rx_one = bytearray(1)

        self._log("info", "OPC-N3 SPI driver initialized")

    def _log(self, level, message):
        if self.logger is None:
            print("OPC-N3: {}".format(message))
            return

        method = getattr(self.logger, level, None)
        if method is not None:
            method(message)

    def _xfer_byte(self, spi, byte_out):
        """Full-duplex transfer of one byte while CS is asserted."""
        self._tx_one[0] = byte_out
        spi.write_readinto(self._tx_one, self._rx_one)
        return self._rx_one[0]

    def _send_command(self, spi, command, delay_s=0.00001):
        response = self._xfer_byte(spi, command)
        if delay_s:
            time.sleep(delay_s)
        return response

    def _send_command_and_wait(self, spi, command):
        """
        OPC-N3 returns BUSY after a command. Keep sending the same command
        every 20 ms until READY is returned.
        """
        response = OPC_BUSY
        attempts = 0

        while response != OPC_READY:
            if response != OPC_BUSY:
                time.sleep(5)
                raise OPCError(
                    "Unexpected OPC response 0x{:02X} for command 0x{:02X}".format(
                        response, command
                    )
                )

            if attempts > 20:
                self._log(
                    "warning",
                    "OPC stayed busy; waiting 5 s for SPI buffer recovery",
                )
                time.sleep(5)

            if attempts > 25:
                raise OPCError(
                    "Timeout waiting for OPC command 0x{:02X}".format(command)
                )

            response = self._send_command(spi, command, 0.02)
            attempts += 1

    def _read_bytes(self, command, size):
        """
        Issue command, wait for READY, then clock out size bytes.

        CS remains active throughout the full operation.
        """
        result = bytearray(size)

        with self.device as spi:
            self._send_command_and_wait(spi, command)

            for index in range(size):
                result[index] = self._send_command(spi, command)

        return result

    def _write_bytes(self, command, data):
        """
        Issue command, wait for READY, then transmit data bytes.

        CS remains active throughout the full operation.
        """
        with self.device as spi:
            self._send_command_and_wait(spi, command)

            for byte in data:
                self._send_command(spi, byte)

    @staticmethod
    def _checksum(raw):
        """OPC-N3 CRC-16, calculated over all bytes except final checksum."""
        crc = 0xFFFF
        polynomial = 0xA001

        for byte in raw[:-2]:
            crc ^= byte

            for _ in range(8):
                if crc & 1:
                    crc >>= 1
                    crc ^= polynomial
                else:
                    crc >>= 1

        return crc

    def _read_model(self, command, model, validate_checksum=True):
        raw = self._read_bytes(command, model.size)
        data = model.unpack(raw)

        if validate_checksum and "checksum" in data:
            expected = self._checksum(raw)

            if data["checksum"] != expected:
                raise OPCError(
                    "Invalid OPC checksum: got 0x{:04X}, expected 0x{:04X}".format(
                        data["checksum"], expected
                    )
                )

        return data

    @staticmethod
    def _convert_temperature(raw):
        return -45.0 + 175.0 * raw / 65535.0

    @staticmethod
    def _convert_relative_humidity(raw):
        return 100.0 * raw / 65535.0

    @staticmethod
    def _convert_mtof(raw):
        return raw / 3.0

    def ping(self):
        """Return True when the OPC responds correctly."""
        try:
            with self.device as spi:
                self._send_command_and_wait(spi, CMD_CHECK_STATUS)
            return True
        except Exception as error:
            self._log("warning", "OPC ping failed: {}".format(error))
            return False

    def info(self):
        """Return the 60-byte OPC information string."""
        raw = self._read_bytes(CMD_READ_INFO_STRING, 60)
        return raw.decode("ascii", "ignore").strip("\x00\r\n ")

    def serial(self):
        """Return the 60-byte OPC serial-number string."""
        raw = self._read_bytes(CMD_READ_SERIAL_STRING, 60)
        return raw.decode("ascii", "ignore").strip("\x00\r\n ")

    def firmware_version(self):
        """Return (major, minor) firmware version."""
        raw = self._read_bytes(CMD_READ_FW_VERSION, 2)
        return raw[0], raw[1]

    def power_state(self):
        """Return fan/laser/DAC/Gain status dictionary."""
        return self._read_model(CMD_READ_POWER_STATE, POWER_STATE_MODEL)

    def config(self):
        """Read OPC-N3 internal configuration and bin-boundary settings."""
        return self._read_model(CMD_READ_CONFIG, CONFIG_MODEL, False)

    def fan_on(self):
        self._write_bytes(CMD_WRITE_POWER_STATE, [
            (POPT_FAN << 1) | 1
        ])

    def fan_off(self):
        self._write_bytes(CMD_WRITE_POWER_STATE, [
            (POPT_FAN << 1) | 0
        ])

    def laser_on(self):
        self._write_bytes(CMD_WRITE_POWER_STATE, [
            (POPT_LASER_SWITCH << 1) | 1
        ])

    def laser_off(self):
        self._write_bytes(CMD_WRITE_POWER_STATE, [
            (POPT_LASER_SWITCH << 1) | 0
        ])

    def on(self, warmup=True):
        """Turn laser and fan on; optionally wait for the configured warmup."""
        self.laser_on()
        self.fan_on()

        if warmup:
            self._log(
                "info",
                "OPC on; warming up for {:.1f} s".format(self.warmup_s),
            )
            time.sleep(self.warmup_s)

    def off(self):
        """Turn laser and fan off."""
        self.laser_off()
        self.fan_off()

    def reset(self):
        """Request a documented OPC-N3 software reset."""
        with self.device as spi:
            self._send_command_and_wait(spi, CMD_RESET)

    def pm(self):
        """Return PM values as a compact dictionary."""
        data = self._read_model(CMD_READ_PM, PM_MODEL)

        return {
            "opc_pm1": data["pm1"],
            "opc_pm25": data["pm25"],
            "opc_pm10": data["pm10"],
        }

    def histogram(self, raw=False):
        """
        Return the complete 24-bin OPC-N3 histogram.

        raw=False converts temperature, RH, sampling period, SFR, MToF and
        number concentrations. raw=True returns register values unchanged.
        """
        data = self._read_model(CMD_READ_HISTOGRAM, HISTOGRAM_MODEL)

        if raw:
            return data

        data["temperature_c"] = self._convert_temperature(
            data.pop("temperature_raw")
        )
        data["relative_humidity_percent"] = self._convert_relative_humidity(
            data.pop("relative_humidity_raw")
        )
        data["sampling_period_s"] = data.pop("sampling_period_raw") / 100.0
        data["sample_flow_rate_ml_s"] = data.pop("sfr_raw") / 100.0

        data["bin1_mtof_us"] = self._convert_mtof(
            data.pop("bin1_mtof_raw")
        )
        data["bin3_mtof_us"] = self._convert_mtof(
            data.pop("bin3_mtof_raw")
        )
        data["bin5_mtof_us"] = self._convert_mtof(
            data.pop("bin5_mtof_raw")
        )
        data["bin7_mtof_us"] = self._convert_mtof(
            data.pop("bin7_mtof_raw")
        )

        sampled_volume_ml = (
            data["sample_flow_rate_ml_s"] * data["sampling_period_s"]
        )

        if sampled_volume_ml > 0:
            for index in range(24):
                key = "bin_{}".format(index)
                data[key] = data[key] / sampled_volume_ml

        data["opc_pm1"] = data.pop("pm1")
        data["opc_pm25"] = data.pop("pm25")
        data["opc_pm10"] = data.pop("pm10")

        return data
