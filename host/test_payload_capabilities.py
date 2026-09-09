import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.modules.setdefault("serial", types.SimpleNamespace())

from host import cli


def load_pack_module():
    path = ROOT / "firmware" / "common" / "pack.py"
    spec = importlib.util.spec_from_file_location("payload_pack_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_actuators_module():
    created = []

    class FakeOutput:
        def __init__(self, pin):
            self.pin = pin
            self.value = None
            created.append(self)

        def switch_to_output(self, value):
            self.value = value

    fake_digitalio = types.SimpleNamespace(DigitalInOut=FakeOutput)
    fake_config = types.SimpleNamespace(
        PUMP_FRONT="front-pin",
        PUMP_BACK="back-pin",
        ELECTROVALVE="valve-pin",
    )
    path = ROOT / "firmware" / "common" / "actuators.py"
    spec = importlib.util.spec_from_file_location("actuators_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"digitalio": fake_digitalio, "config": fake_config},
    ):
        spec.loader.exec_module(module)
    return module, created


class PayloadCapabilityTests(unittest.TestCase):
    def test_campaign_payload_names(self):
        self.assertEqual(
            [str(payload) for payload in cli.Payload],
            ["alma", "beni", "carla"],
        )

    def test_data_command_does_not_require_actuator_state(self):
        with patch.object(sys, "argv", ["cli.py", "data", "carla"]):
            args = cli.parse_args()

        self.assertEqual(args.subcommand, "data")
        self.assertEqual(args.payload, cli.Payload.CARLA)

    def test_carla_accepts_front_pump(self):
        args = SimpleNamespace(
            payload=cli.Payload.CARLA,
            subcommand="pump",
            pump_location="front",
        )
        cli._validate_command_capability(args)

    def test_carla_rejects_unavailable_actuators(self):
        for subcommand, location in (("pump", "back"), ("pump", "both")):
            args = SimpleNamespace(
                payload=cli.Payload.CARLA,
                subcommand=subcommand,
                pump_location=location,
            )
            with self.assertRaises(ValueError):
                cli._validate_command_capability(args)

        args = SimpleNamespace(payload=cli.Payload.CARLA, subcommand="valve")
        with self.assertRaises(ValueError):
            cli._validate_command_capability(args)

    def test_alma_and_beni_keep_opc_and_valve_capabilities(self):
        for payload in (cli.Payload.ALMA, cli.Payload.BENI):
            capabilities = cli._PAYLOAD_CAPABILITIES[payload]
            self.assertIn("opc", capabilities)
            self.assertIn("valve", capabilities)
            self.assertIn("pump_back", capabilities)

    def test_carla_uses_fill_values_in_common_wire_format(self):
        pack = load_pack_module()
        packet = pack.dict2bytes({
            "payload_id": "carla",
            "pump_front_state": 0,
            "pump_back_state": None,
            "valve_state": None,
        })
        decoded = pack.bytes2dict(packet)

        self.assertEqual(decoded["payload_id"], "carla")
        self.assertEqual(decoded["pump_front_state"], 0)
        self.assertEqual(decoded["pump_back_state"], pack.INT_FILLVAL)
        self.assertEqual(decoded["valve_state"], pack.INT_FILLVAL)
        self.assertEqual(decoded["opc_bin_0"], pack.UNSIGNED_SHORT_FILLVAL)

    def test_front_only_pump_does_not_claim_back_output(self):
        actuators, created = load_actuators_module()
        logger = types.SimpleNamespace(info=lambda _message: None)
        pump = actuators.Pump(logger, locations=("front",))

        self.assertEqual(
            [output.pin for output in created],
            ["front-pin", "back-pin"],
        )
        self.assertEqual(pump.front_state(), 0)
        self.assertIsNone(pump.back_state())
        with self.assertRaises(ValueError):
            pump.set_state("back", "on")
        with self.assertRaises(ValueError):
            pump.set_state("both", "on")


if __name__ == "__main__":
    unittest.main()
