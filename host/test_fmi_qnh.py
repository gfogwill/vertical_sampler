import datetime
import unittest

from host.fmi_qnh import (
    QnhProvider,
    QnhReading,
    FmiQnhError,
    parse_pressure_observations,
)


class FmiQnhTests(unittest.TestCase):
    def test_parse_pressure_observations_returns_latest_sample(self):
        xml_data = b"""
        <FeatureCollection xmlns:om="urn:om">
          <member>
            <om:MeasurementTimeseries>
              <om:point><om:MeasurementTVP>
                <om:time>2026-09-07T11:30:00Z</om:time>
                <om:value>1011.3</om:value>
              </om:MeasurementTVP></om:point>
              <om:point><om:MeasurementTVP>
                <om:time>2026-09-07T11:40:00Z</om:time>
                <om:value>1011.2</om:value>
              </om:MeasurementTVP></om:point>
            </om:MeasurementTimeseries>
          </member>
        </FeatureCollection>
        """

        observations = parse_pressure_observations(xml_data)

        self.assertEqual(observations[-1][1], 1011.2)
        self.assertEqual(
            observations[-1][0],
            datetime.datetime(2026, 9, 7, 11, 40, tzinfo=datetime.timezone.utc),
        )

    def test_provider_keeps_manual_fallback_when_fmi_fails(self):
        def failing_fetcher(**_kwargs):
            raise FmiQnhError("network unavailable")

        provider = QnhProvider(
            fallback_qnh=1008.0,
            enabled=False,
            fetcher=failing_fetcher,
        )
        provider.refresh_now()

        state = provider.snapshot()
        self.assertEqual(state.qnh_hpa, 1008.0)
        self.assertEqual(state.source, "manual")
        self.assertEqual(state.error, "network unavailable")

    def test_provider_accepts_fmi_reading(self):
        reading = QnhReading(
            qnh_hpa=1011.2,
            observation_time=datetime.datetime.now(tz=datetime.timezone.utc),
            fetched_at=datetime.datetime.now(tz=datetime.timezone.utc),
        )
        provider = QnhProvider(
            fallback_qnh=1008.0,
            enabled=False,
            fetcher=lambda **_kwargs: reading,
        )
        provider.refresh_now()

        state = provider.snapshot()
        self.assertEqual(state.qnh_hpa, 1011.2)
        self.assertEqual(state.source, "fmi")
        self.assertIsNone(state.error)

    def test_provider_keeps_fresh_last_known_value_after_refresh_failure(self):
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        reading = QnhReading(1011.2, now, now)
        calls = [reading, FmiQnhError("temporary outage")]

        def fetcher(**_kwargs):
            result = calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        provider = QnhProvider(
            fallback_qnh=1008.0,
            enabled=False,
            max_age_s=600,
            fetcher=fetcher,
        )
        provider.refresh_now()
        provider.refresh_now()

        state = provider.snapshot()
        self.assertEqual(state.qnh_hpa, 1011.2)
        self.assertEqual(state.source, "fmi")
        self.assertEqual(state.error, "temporary outage")


if __name__ == "__main__":
    unittest.main()
