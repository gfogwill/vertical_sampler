import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from host.weather_sources import (
    CloudnetCache,
    ModelCloudData,
    WeatherSourceError,
    _sounding_slots,
    parse_uwyo_sounding,
)
from host.weather_dashboard import _regular_model_cloud_grid


class SoundingParserTests(unittest.TestCase):
    def test_parses_profile_and_converts_height_to_agl(self):
        rows = "\n".join(
            "{:7.1f} {:6d} {:6.1f} {:6.1f} {:6d} {:6.2f}".format(
                995.0 - index,
                180 + index * 100,
                5.0 - index,
                4.0 - index,
                90 - index,
                4.0,
            )
            for index in range(6)
        )
        page = ("<html><PRE>\n" + rows + "\n</PRE></html>").encode()
        timestamp = datetime.datetime(
            2026, 9, 14, 0, tzinfo=datetime.timezone.utc
        )

        profile = parse_uwyo_sounding(page, timestamp)

        self.assertEqual(profile.height_agl_m[0], 0)
        self.assertEqual(profile.height_agl_m[-1], 500)
        self.assertEqual(profile.temperature_c[:2], [5.0, 4.0])
        self.assertEqual(profile.relative_humidity_percent[:2], [90.0, 89.0])

    def test_rejects_response_without_profile(self):
        with self.assertRaises(WeatherSourceError):
            parse_uwyo_sounding(
                b"<html>No data</html>",
                datetime.datetime.now(tz=datetime.timezone.utc),
            )

    def test_sounding_slots_do_not_include_future_launch(self):
        day = datetime.date(2026, 9, 14)
        now = datetime.datetime(
            2026, 9, 14, 9, tzinfo=datetime.timezone.utc
        )

        slots = list(_sounding_slots(day, now, lookback_days=1))

        self.assertEqual(slots[0].hour, 0)
        self.assertTrue(all(slot <= now for slot in slots))


class NumericHandlingTests(unittest.TestCase):
    def test_numpy_nan_is_not_finite(self):
        self.assertFalse(np.isfinite(float("nan")))


class ModelCloudGridTests(unittest.TestCase):
    def test_interpolates_cloud_fraction_and_phase_to_regular_heights(self):
        data = ModelCloudData(
            times=[
                datetime.datetime(2026, 9, 14, hour, tzinfo=datetime.timezone.utc)
                for hour in (0, 1)
            ],
            height_agl_m=np.array([[0, 100, 200], [0, 110, 220]], dtype=float),
            cloud_fraction_percent=np.array(
                [[0, 50, 100], [0, 20, 80]], dtype=float
            ),
            liquid_mask=np.array(
                [[False, True, True], [False, True, True]]
            ),
            ice_mask=np.zeros((2, 3), dtype=bool),
            precipitation_mask=np.array(
                [[False, False, True], [False, False, True]]
            ),
            model_id="meps",
            model_name="MEPS forecast",
            source_date=datetime.date(2026, 9, 14),
            updated_at="2026-09-14T10:00:00Z",
        )

        heights, fraction, liquid, ice, precipitation = (
            _regular_model_cloud_grid(data, max_height_m=200, step_m=100)
        )

        np.testing.assert_array_equal(heights, [0, 100, 200])
        self.assertAlmostEqual(fraction[1, 0], 50)
        self.assertTrue(liquid[1, 1])
        self.assertFalse(ice.any())
        self.assertTrue(precipitation[2, 0])


class CloudnetCacheTests(unittest.TestCase):
    def test_minimum_refresh_interval_reuses_volatile_file(self):
        metadata = {
            "filename": "mwr.nc",
            "downloadUrl": "https://example.invalid/mwr.nc",
            "updatedAt": "2026-09-14T12:00:00Z",
        }
        with tempfile.TemporaryDirectory() as folder:
            cache = CloudnetCache(folder)
            data_path = Path(folder) / "mwr.nc"
            data_path.write_bytes(b"cached")
            data_path.with_suffix(".nc.json").write_text(
                '{"updatedAt":"older"}', encoding="utf-8"
            )
            with patch(
                "host.weather_sources._request_bytes",
                side_effect=AssertionError("unexpected download"),
            ):
                result = cache.fetch(metadata, min_refresh_s=3600)

        self.assertEqual(result.name, "mwr.nc")


if __name__ == "__main__":
    unittest.main()
