import contextlib
import datetime
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from host import cli


class CliScheduleTests(unittest.TestCase):
    def _load(self, content):
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".txt",
            delete=False,
        ) as source:
            source.write(content)
            path = Path(source.name)
        try:
            return cli.load_schedule(path)
        finally:
            path.unlink()

    def test_load_schedule_sorts_and_parses_commands(self):
        entries = self._load(
            "# comments and blank lines are allowed\n"
            "2026-09-26T12:30:00+03:00 valve beni off\n"
            "2026-09-26T12:00:00Z pump alma front on\n"
        )

        self.assertEqual(
            [entry.scheduled_at for entry in entries],
            [
                datetime.datetime(
                    2026,
                    9,
                    26,
                    9,
                    30,
                    tzinfo=datetime.timezone.utc,
                ),
                datetime.datetime(
                    2026,
                    9,
                    26,
                    12,
                    0,
                    tzinfo=datetime.timezone.utc,
                ),
            ],
        )
        self.assertEqual(entries[0].args.subcommand, "valve")
        self.assertEqual(entries[0].args.payload, cli.Payload.BENI)
        self.assertEqual(entries[1].args.state, cli.State.ON)

    def test_load_schedule_requires_timezone(self):
        with self.assertRaisesRegex(ValueError, "must include a timezone"):
            self._load("2026-09-26T12:00:00 pump alma front on\n")

    def test_load_schedule_rejects_unsupported_capability(self):
        with self.assertRaisesRegex(ValueError, "has no electro-valve"):
            self._load("2026-09-26T12:00:00Z valve carla on\n")

    def test_run_schedule_skips_past_entries_by_default(self):
        entries = self._load(
            "2026-09-26T11:59:00Z pump alma front on\n"
            "2026-09-26T12:00:00Z pump alma front off\n"
        )
        runner = Mock(return_value=True)
        now = datetime.datetime(
            2026,
            9,
            26,
            12,
            0,
            tzinfo=datetime.timezone.utc,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            success = cli.run_schedule(
                entries,
                now_fn=lambda: now,
                command_runner=runner,
            )

        self.assertTrue(success)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0].state, cli.State.OFF)

    def test_dry_run_does_not_send_commands(self):
        entries = self._load(
            "2026-09-26T12:00:00Z pump alma front on\n"
        )
        runner = Mock(return_value=True)

        with contextlib.redirect_stdout(io.StringIO()):
            success = cli.run_schedule(
                entries,
                dry_run=True,
                command_runner=runner,
            )

        self.assertTrue(success)
        runner.assert_not_called()

    def test_scheduled_state_matching_covers_pump_locations_and_valve(self):
        cases = (
            (
                "pump",
                {"pump_location": "front", "state": cli.State.ON},
                {"pump_front_state": 1},
            ),
            (
                "pump",
                {"pump_location": "back", "state": cli.State.OFF},
                {"pump_back_state": 0},
            ),
            (
                "pump",
                {"pump_location": "both", "state": cli.State.ON},
                {"pump_front_state": 1, "pump_back_state": 1},
            ),
            (
                "valve",
                {"state": cli.State.OFF},
                {"valve_state": 0},
            ),
        )
        for subcommand, fields, data in cases:
            with self.subTest(subcommand=subcommand, fields=fields):
                args = SimpleNamespace(
                    subcommand=subcommand,
                    payload=cli.Payload.ALMA,
                    **fields,
                )
                self.assertTrue(cli._scheduled_state_matches(args, data))

    def test_scheduled_actuator_retries_after_failed_state_check(self):
        args = SimpleNamespace(
            subcommand="pump",
            payload=cli.Payload.ALMA,
            pump_location="front",
            state=cli.State.ON,
        )
        requests = []
        states = iter((
            {"pump_front_state": 0},
            {"pump_front_state": 1},
        ))
        sleeps = []

        def send(_ser, request, response_handler=None, **_kwargs):
            requests.append(request.subcommand)
            if request.subcommand == "data":
                response_handler(next(states))
                return True
            return False

        with patch.object(cli, "_send_command_on_serial", side_effect=send):
            success = cli._send_scheduled_on_serial(
                object(),
                args,
                display=False,
                sleep_fn=sleeps.append,
                verify_delay_s=5,
                max_attempts=3,
            )

        self.assertTrue(success)
        self.assertEqual(requests, ["pump", "data", "pump", "data"])
        self.assertEqual(sleeps, [5, 5])

    def test_scheduled_command_with_lost_ack_is_not_resent_when_verified(self):
        args = SimpleNamespace(
            subcommand="valve",
            payload=cli.Payload.BENI,
            state=cli.State.OFF,
        )
        requests = []

        def send(_ser, request, response_handler=None, **_kwargs):
            requests.append(request.subcommand)
            if request.subcommand == "data":
                response_handler({"valve_state": 0})
                return True
            return False

        with patch.object(cli, "_send_command_on_serial", side_effect=send):
            success = cli._send_scheduled_on_serial(
                object(),
                args,
                display=False,
                sleep_fn=lambda _delay: None,
                max_attempts=3,
            )

        self.assertTrue(success)
        self.assertEqual(requests, ["valve", "data"])


if __name__ == "__main__":
    unittest.main()
