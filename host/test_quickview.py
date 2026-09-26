import datetime
import unittest

from host.quickview import QuickView


class QuickViewTimestampTests(unittest.TestCase):
    def setUp(self):
        self.quickview = object.__new__(QuickView)
        self.quickview._anchor = {
            "alma": None,
            "beni": None,
            "carla": None,
        }
        self.quickview._monotonic_anchor = {
            "alma": None,
            "beni": None,
            "carla": None,
        }

    def test_prefers_device_utc_time(self):
        timestamp = self.quickview.timestamp_for_entry(
            {"payload_id": "beni"},
            {
                "payload_id": "beni",
                "utc_time": "2026-09-25T12:34:56Z",
            },
            "beni",
        )

        self.assertEqual(timestamp.tzinfo, datetime.timezone.utc)
        self.assertEqual(
            timestamp,
            datetime.datetime(
                2026,
                9,
                25,
                12,
                34,
                56,
                tzinfo=datetime.timezone.utc,
            ),
        )

    def test_uses_host_epoch_timestamp_when_device_time_is_unavailable(self):
        expected = datetime.datetime(
            2026,
            9,
            25,
            12,
            34,
            56,
            tzinfo=datetime.timezone.utc,
        )
        timestamp = self.quickview.timestamp_for_entry(
            {"payload_id": "alma"},
            {"payload_id": "alma", "_ts": expected.timestamp()},
            "alma",
        )

        self.assertEqual(timestamp, expected)

    def test_monotonic_fallback_preserves_real_sample_spacing(self):
        first = self.quickview.timestamp_for_entry(
            {},
            {"monotonic_s": 100.0},
            "alma",
        )
        second = self.quickview.timestamp_for_entry(
            {},
            {"monotonic_s": 460.0},
            "alma",
        )

        self.assertAlmostEqual(
            (second - first).total_seconds(),
            360.0,
        )


if __name__ == "__main__":
    unittest.main()
