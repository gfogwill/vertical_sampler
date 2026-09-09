"""FMI station pressure retrieval for host-side QNH calculations."""

import datetime
import math
import threading
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


DEFAULT_QNH_HPA = 1013.25
FMI_WFS_URL = "https://opendata.fmi.fi/wfs"
FMI_STATION_ID = "101985"  # Kittilä Matorova
FMI_REFRESH_INTERVAL_S = 10 * 60
FMI_MAX_OBSERVATION_AGE_S = 30 * 60
FMI_REQUEST_TIMEOUT_S = 15

_UTC = datetime.timezone.utc


class FmiQnhError(RuntimeError):
    """Raised when a usable FMI pressure observation is unavailable."""


@dataclass(frozen=True)
class QnhReading:
    qnh_hpa: float
    observation_time: datetime.datetime
    fetched_at: datetime.datetime


@dataclass(frozen=True)
class QnhState:
    qnh_hpa: float
    source: str
    observation_time: datetime.datetime = None
    fetched_at: datetime.datetime = None
    error: str = None


def is_valid_qnh(qnh_hpa):
    return (
        isinstance(qnh_hpa, (int, float))
        and math.isfinite(qnh_hpa)
        and 800.0 <= qnh_hpa <= 1100.0
    )


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _parse_timestamp(value):
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise FmiQnhError("invalid FMI observation timestamp {!r}".format(value)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC)


def parse_pressure_observations(xml_data):
    """Return valid ``(timestamp, pressure_hpa)`` pairs from an FMI response."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise FmiQnhError("invalid XML returned by FMI") from exc

    observations = []
    for tvp in root.iter():
        if _local_name(tvp.tag) != "MeasurementTVP":
            continue
        timestamp = None
        value = None
        for child in tvp:
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name == "time":
                timestamp = text
            elif name == "value":
                value = text
        if not timestamp or not value:
            continue
        try:
            pressure_hpa = float(value)
        except ValueError:
            continue
        if not is_valid_qnh(pressure_hpa):
            continue
        observations.append((_parse_timestamp(timestamp), pressure_hpa))

    if not observations:
        raise FmiQnhError("FMI returned no valid pressure observations")
    return sorted(observations, key=lambda item: item[0])


def fetch_latest_qnh(
    url=FMI_WFS_URL,
    station_id=FMI_STATION_ID,
    timeout_s=FMI_REQUEST_TIMEOUT_S,
    max_age_s=FMI_MAX_OBSERVATION_AGE_S,
):
    """Fetch the newest fresh sea-level pressure observation for the station."""
    query = urlencode({
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::timevaluepair",
        "fmisid": station_id,
        "parameters": "pressure",
    })
    request = Request(
        "{}?{}".format(url, query),
        headers={"User-Agent": "vertical_sampler/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            xml_data = response.read()
    except HTTPError as exc:
        raise FmiQnhError("FMI request failed with HTTP {}".format(exc.code)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise FmiQnhError("FMI request failed: {}".format(exc)) from exc

    observations = parse_pressure_observations(xml_data)
    latest_time, latest_pressure = observations[-1]
    fetched_at = datetime.datetime.now(tz=_UTC)
    age_s = (fetched_at - latest_time).total_seconds()
    if age_s < -300:
        raise FmiQnhError("FMI observation timestamp is too far in the future")
    if age_s > max_age_s:
        raise FmiQnhError(
            "latest FMI pressure observation is {:.0f} minutes old".format(age_s / 60.0)
        )
    return QnhReading(
        qnh_hpa=latest_pressure,
        observation_time=latest_time,
        fetched_at=fetched_at,
    )


class QnhProvider:
    """Keep a manual fallback and refresh it from FMI in a background thread."""

    def __init__(
        self,
        fallback_qnh=DEFAULT_QNH_HPA,
        enabled=True,
        refresh_interval_s=FMI_REFRESH_INTERVAL_S,
        max_age_s=FMI_MAX_OBSERVATION_AGE_S,
        fetcher=fetch_latest_qnh,
    ):
        if not is_valid_qnh(fallback_qnh):
            raise ValueError("fallback QNH must be between 800 and 1100 hPa")
        if refresh_interval_s <= 0:
            raise ValueError("QNH refresh interval must be positive")
        if max_age_s <= 0:
            raise ValueError("maximum QNH observation age must be positive")
        self.enabled = enabled
        self.refresh_interval_s = refresh_interval_s
        self.max_age_s = max_age_s
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._fallback_qnh = float(fallback_qnh)
        self._state = QnhState(qnh_hpa=self._fallback_qnh, source="manual")

    def snapshot(self):
        with self._lock:
            return self._state

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="fmi-qnh",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            self._thread = None

    def refresh_now(self):
        """Refresh once; useful for tests and callers that do not need a thread."""
        self._refresh()

    def _run(self):
        while not self._stop_event.is_set():
            self._refresh()
            self._stop_event.wait(self.refresh_interval_s)

    def _refresh(self):
        try:
            reading = self.fetcher(max_age_s=self.max_age_s)
        except FmiQnhError as exc:
            self._set_failure(str(exc))
            return
        if not is_valid_qnh(reading.qnh_hpa):
            self._set_failure("FMI returned an invalid QNH value")
            return
        with self._lock:
            self._state = QnhState(
                qnh_hpa=reading.qnh_hpa,
                source="fmi",
                observation_time=reading.observation_time,
                fetched_at=reading.fetched_at,
            )

    def _set_failure(self, message):
        now = datetime.datetime.now(tz=_UTC)
        with self._lock:
            previous = self._state
            if (
                previous.source == "fmi"
                and previous.observation_time is not None
                and (now - previous.observation_time).total_seconds() <= self.max_age_s
            ):
                qnh_hpa = previous.qnh_hpa
                source = "fmi"
                observation_time = previous.observation_time
                fetched_at = previous.fetched_at
            else:
                qnh_hpa = self._fallback_qnh
                source = "manual"
                observation_time = None
                fetched_at = None
            self._state = QnhState(
                qnh_hpa=qnh_hpa,
                source=source,
                observation_time=observation_time,
                fetched_at=fetched_at,
                error=message,
            )
