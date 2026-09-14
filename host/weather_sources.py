"""Data retrieval and parsing for the Matorova low-cloud dashboard."""

import datetime
import html
import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from netCDF4 import Dataset, num2date


MATOROVA_LATITUDE = 67.999959
MATOROVA_LONGITUDE = 24.240158
KENTTAROVA_SITE = "kenttarova"
SODANKYLA_WMO_ID = "02836"
LOW_CLOUD_LIMIT_M = 2000.0
MWR_MIN_REFRESH_S = 60 * 60
CACHE_RETENTION_DAYS = 7

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CLOUDNET_API_URL = "https://cloudnet.fmi.fi/api"
UWYO_SOUNDING_URL = "https://weather.uwyo.edu/wsgi/sounding"
USER_AGENT = "vertical_sampler-weather-dashboard/1.0"
_NETCDF_LOCK = threading.Lock()


class WeatherSourceError(RuntimeError):
    """Raised when a weather source does not provide usable data."""


@dataclass(frozen=True)
class ForecastData:
    times: list
    boundary_layer_height_m: list
    low_cloud_cover_percent: list
    temperature_c: list
    relative_humidity_percent: list
    precipitation_mm: list
    wind_speed_kmh: list


@dataclass(frozen=True)
class ModelCloudData:
    times: list
    height_agl_m: object
    cloud_fraction_percent: object
    liquid_mask: object
    ice_mask: object
    precipitation_mask: object
    model_id: str
    model_name: str
    source_date: datetime.date
    updated_at: str


@dataclass(frozen=True)
class CloudLayers:
    times: list
    base_agl_m: list
    top_agl_m: list
    heights_agl_m: list
    target_classification: object
    source_date: datetime.date
    updated_at: str
    error_level: str


@dataclass(frozen=True)
class Profile:
    observation_time: datetime.datetime
    height_agl_m: list
    temperature_c: list
    relative_humidity_percent: list
    source_date: datetime.date
    source_name: str
    potential_temperature_c: object = None
    quality_note: str = ""


def _request_bytes(url, timeout_s=30):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            return response.read()
    except HTTPError as exc:
        raise WeatherSourceError(
            "{} returned HTTP {}".format(url.split("?", 1)[0], exc.code)
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WeatherSourceError("request failed: {}".format(exc)) from exc


def _request_json(url, timeout_s=30):
    try:
        return json.loads(_request_bytes(url, timeout_s=timeout_s))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeatherSourceError("invalid JSON response") from exc


def _finite_or_nan(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def fetch_ecmwf_forecast(day, timezone_name="Europe/Helsinki"):
    """Fetch hourly ECMWF IFS forecast fields through Open-Meteo."""
    query = urlencode({
        "latitude": MATOROVA_LATITUDE,
        "longitude": MATOROVA_LONGITUDE,
        "hourly": ",".join((
            "boundary_layer_height",
            "cloud_cover_low",
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
        )),
        "models": "ecmwf_ifs",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "timezone": timezone_name,
    })
    data = _request_json("{}?{}".format(OPEN_METEO_URL, query))
    hourly = data.get("hourly")
    if not hourly or not hourly.get("time"):
        raise WeatherSourceError("ECMWF forecast contains no hourly data")

    try:
        times = [datetime.datetime.fromisoformat(value) for value in hourly["time"]]
        return ForecastData(
            times=times,
            boundary_layer_height_m=[
                _finite_or_nan(value) for value in hourly["boundary_layer_height"]
            ],
            low_cloud_cover_percent=[
                _finite_or_nan(value) for value in hourly["cloud_cover_low"]
            ],
            temperature_c=[
                _finite_or_nan(value) for value in hourly["temperature_2m"]
            ],
            relative_humidity_percent=[
                _finite_or_nan(value) for value in hourly["relative_humidity_2m"]
            ],
            precipitation_mm=[
                _finite_or_nan(value) for value in hourly["precipitation"]
            ],
            wind_speed_kmh=[
                _finite_or_nan(value) for value in hourly["wind_speed_10m"]
            ],
        )
    except (KeyError, TypeError) as exc:
        raise WeatherSourceError("ECMWF forecast response is incomplete") from exc


class CloudnetCache:
    """Cache changing Cloudnet NetCDF files by their metadata update time."""

    def __init__(self, cache_dir=None):
        default = Path.home() / ".cache" / "vertical_sampler" / "weather"
        self.cache_dir = Path(cache_dir or default)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prune()

    def _prune(self):
        cutoff = (
            datetime.datetime.now().timestamp()
            - CACHE_RETENTION_DAYS * 24 * 60 * 60
        )
        for path in self.cache_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def fetch(self, metadata, min_refresh_s=0):
        filename = Path(metadata.get("filename") or "").name
        download_url = metadata.get("downloadUrl")
        updated_at = metadata.get("updatedAt")
        if not filename or not download_url:
            raise WeatherSourceError("Cloudnet metadata is missing a download URL")

        data_path = self.cache_dir / filename
        metadata_path = data_path.with_suffix(data_path.suffix + ".json")
        if data_path.exists() and metadata_path.exists():
            try:
                cached = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = {}
            age_s = datetime.datetime.now().timestamp() - data_path.stat().st_mtime
            if cached.get("updatedAt") == updated_at or age_s < min_refresh_s:
                return data_path

        raw = _request_bytes(download_url, timeout_s=120)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=filename + ".", dir=self.cache_dir
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
            os.replace(temporary_name, data_path)
            metadata_path.write_text(
                json.dumps({"updatedAt": updated_at, "downloadUrl": download_url}),
                encoding="utf-8",
            )
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return data_path


def _cloudnet_metadata(day, product, lookback_days=2):
    for days_back in range(lookback_days + 1):
        candidate = day - datetime.timedelta(days=days_back)
        query = urlencode({
            "site": KENTTAROVA_SITE,
            "date": candidate.isoformat(),
            "product": product,
        })
        files = _request_json("{}/files?{}".format(CLOUDNET_API_URL, query))
        usable = [item for item in files if item.get("downloadUrl")]
        if usable:
            return max(usable, key=lambda item: item.get("updatedAt") or "")
    raise WeatherSourceError(
        "Cloudnet has no {} file for {} or the previous {} days".format(
            product, day.isoformat(), lookback_days
        )
    )


def _cloudnet_model_metadata(day, lookback_days=2):
    for days_back in range(lookback_days + 1):
        candidate = day - datetime.timedelta(days=days_back)
        query = urlencode({
            "site": KENTTAROVA_SITE,
            "date": candidate.isoformat(),
        })
        files = _request_json("{}/model-files?{}".format(CLOUDNET_API_URL, query))
        usable = [item for item in files if item.get("downloadUrl")]
        if usable:
            best_order = min(
                (item.get("model") or {}).get("optimumOrder") or 9999
                for item in usable
            )
            preferred = [
                item
                for item in usable
                if ((item.get("model") or {}).get("optimumOrder") or 9999)
                == best_order
            ]
            return max(preferred, key=lambda item: item.get("updatedAt") or "")
    raise WeatherSourceError(
        "Cloudnet has no model file for {} or the previous {} days".format(
            day.isoformat(), lookback_days
        )
    )


def _netcdf_times(variable):
    values = num2date(
        variable[:],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    result = []
    for value in np.atleast_1d(values):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        result.append(value.astimezone(datetime.timezone.utc))
    return result


def _float_list(variable):
    values = np.ma.filled(variable[:], np.nan)
    return [float(value) for value in np.asarray(values).reshape(-1)]


def _model_variable(dataset, name):
    variable = dataset.variables.get(name)
    if variable is None:
        return np.zeros(dataset.variables["cloud_fraction"].shape, dtype=float)
    return np.asarray(np.ma.filled(variable[:], 0.0), dtype=float)


def fetch_model_cloud_profile(day, cache=None):
    """Fetch altitude-resolved cloud fraction from Cloudnet's preferred model."""
    metadata = _cloudnet_model_metadata(day)
    path = (cache or CloudnetCache()).fetch(metadata)
    try:
        with _NETCDF_LOCK:
            with Dataset(path) as dataset:
                times = _netcdf_times(dataset.variables["time"])
                heights = np.asarray(
                    np.ma.filled(dataset.variables["height"][:], np.nan),
                    dtype=float,
                )
                cloud_fraction = np.asarray(
                    np.ma.filled(
                        dataset.variables["cloud_fraction"][:], np.nan
                    ),
                    dtype=float,
                )
                liquid = _model_variable(dataset, "ql")
                ice = _model_variable(dataset, "qi")
                precipitation = (
                    _model_variable(dataset, "qr")
                    + _model_variable(dataset, "qs")
                    + _model_variable(dataset, "qg")
                )
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        raise WeatherSourceError("unable to read Cloudnet model file") from exc

    finite_fraction = cloud_fraction[np.isfinite(cloud_fraction)]
    if finite_fraction.size and np.nanmax(finite_fraction) <= 1.5:
        cloud_fraction = cloud_fraction * 100.0
    cloud_present = cloud_fraction >= 5.0
    condensate_threshold = 1e-8
    model = metadata.get("model") or {}
    return ModelCloudData(
        times=times,
        height_agl_m=heights,
        cloud_fraction_percent=cloud_fraction,
        liquid_mask=cloud_present & (liquid > condensate_threshold),
        ice_mask=cloud_present & (ice > condensate_threshold),
        precipitation_mask=cloud_present & (
            precipitation > condensate_threshold
        ),
        model_id=model.get("id") or "unknown",
        model_name=model.get("humanReadableName") or "Cloudnet model",
        source_date=datetime.date.fromisoformat(metadata["measurementDate"]),
        updated_at=metadata.get("updatedAt") or "",
    )


def fetch_cloud_layers(day, cache=None):
    """Fetch Kenttärova Cloudnet cloud-base and cloud-top observations."""
    metadata = _cloudnet_metadata(day, "classification")
    path = (cache or CloudnetCache()).fetch(metadata)
    try:
        with _NETCDF_LOCK:
            with Dataset(path) as dataset:
                times = _netcdf_times(dataset.variables["time"])
                bases = _float_list(dataset.variables["cloud_base_height_agl"])
                tops = _float_list(dataset.variables["cloud_top_height_agl"])
                altitude = float(dataset.variables["altitude"][0])
                heights = (
                    np.ma.filled(dataset.variables["height"][:], np.nan) - altitude
                )
                classification = np.ma.filled(
                    dataset.variables["target_classification"][:], -1
                )
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        raise WeatherSourceError("unable to read Cloudnet classification file") from exc

    return CloudLayers(
        times=times,
        base_agl_m=bases,
        top_agl_m=tops,
        heights_agl_m=np.asarray(heights, dtype=float).tolist(),
        target_classification=np.asarray(classification, dtype=int),
        source_date=datetime.date.fromisoformat(metadata["measurementDate"]),
        updated_at=metadata.get("updatedAt") or "",
        error_level=metadata.get("errorLevel") or "unknown",
    )


def _latest_profile_index(dataset):
    temperature = dataset.variables["temperature"]
    humidity = dataset.variables["relative_humidity"]
    potential_temperature = dataset.variables.get("potential_temperature")
    temperature_quality = dataset.variables.get("temperature_quality_flag")
    humidity_quality = dataset.variables.get("absolute_humidity_quality_flag")
    stability_quality = dataset.variables.get("stability_quality_flag")
    for index in range(temperature.shape[0] - 1, -1, -1):
        if temperature_quality is not None and int(temperature_quality[index]) != 0:
            continue
        if humidity_quality is not None and int(humidity_quality[index]) != 0:
            continue
        if (
            potential_temperature is not None
            and stability_quality is not None
            and int(stability_quality[index]) != 0
        ):
            continue
        temp = np.ma.filled(temperature[index, :], np.nan)
        rh = np.ma.filled(humidity[index, :], np.nan)
        valid = np.isfinite(temp) & np.isfinite(rh)
        if potential_temperature is not None:
            theta = np.ma.filled(potential_temperature[index, :], np.nan)
            valid &= np.isfinite(theta)
        if np.count_nonzero(valid) >= 5:
            return index
    raise WeatherSourceError("Cloudnet MWR file contains no valid profiles")


def fetch_mwr_profile(day, cache=None):
    """Fetch the latest valid Kenttärova HATPRO temperature/RH profile."""
    metadata = _cloudnet_metadata(day, "mwr-single")
    path = (cache or CloudnetCache()).fetch(
        metadata, min_refresh_s=MWR_MIN_REFRESH_S
    )
    try:
        with _NETCDF_LOCK:
            with Dataset(path) as dataset:
                index = _latest_profile_index(dataset)
                times = _netcdf_times(dataset.variables["time"])
                height_amsl = np.ma.filled(dataset.variables["height"][:], np.nan)
                altitude_var = dataset.variables.get("altitude")
                if altitude_var is None:
                    site_altitude = 345.0
                elif altitude_var.ndim == 0:
                    site_altitude = float(altitude_var[:])
                else:
                    site_altitude = float(altitude_var[index])
                height_agl = np.asarray(height_amsl, dtype=float) - site_altitude
                temperature = np.ma.filled(
                    dataset.variables["temperature"][index, :], np.nan
                )
                humidity = np.ma.filled(
                    dataset.variables["relative_humidity"][index, :], np.nan
                )
                potential_variable = dataset.variables.get(
                    "potential_temperature"
                )
                potential_temperature = (
                    np.ma.filled(potential_variable[index, :], np.nan)
                    if potential_variable is not None
                    else None
                )
    except (OSError, KeyError, ValueError, IndexError, RuntimeError) as exc:
        raise WeatherSourceError("unable to read Cloudnet MWR file") from exc

    temperature = np.asarray(temperature, dtype=float) - 273.15
    humidity = np.asarray(humidity, dtype=float) * 100.0
    if potential_temperature is not None:
        potential_temperature = (
            np.asarray(potential_temperature, dtype=float) - 273.15
        )
    valid = (
        np.isfinite(height_agl)
        & np.isfinite(temperature)
        & np.isfinite(humidity)
        & (height_agl >= 0)
    )
    quality = metadata.get("errorLevel") or "unknown"
    quality_note = "" if quality == "pass" else "Cloudnet QC: {}".format(quality)
    return Profile(
        observation_time=times[index],
        height_agl_m=height_agl[valid].tolist(),
        temperature_c=temperature[valid].tolist(),
        relative_humidity_percent=humidity[valid].tolist(),
        source_date=datetime.date.fromisoformat(metadata["measurementDate"]),
        source_name="Kenttärova HATPRO",
        potential_temperature_c=(
            potential_temperature[valid].tolist()
            if potential_temperature is not None
            else None
        ),
        quality_note=quality_note,
    )


def parse_uwyo_sounding(page, observation_time):
    """Parse the profile table from a University of Wyoming HTML response."""
    text = html.unescape(page.decode("utf-8", errors="replace"))
    pre_match = re.search(r"<PRE>(.*?)</PRE>", text, flags=re.IGNORECASE | re.DOTALL)
    if pre_match is None:
        raise WeatherSourceError("Sodankylä sounding response has no profile table")

    rows = []
    for line in pre_match.group(1).splitlines():
        if len(line) < 35:
            continue
        try:
            pressure = float(line[0:7])
            height = float(line[7:14])
            temperature = float(line[14:21])
            humidity = float(line[28:35])
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in (
            pressure, height, temperature, humidity
        )):
            continue
        rows.append((height, temperature, humidity))
    if len(rows) < 5:
        raise WeatherSourceError("Sodankylä sounding contains too few profile levels")

    station_altitude = min(row[0] for row in rows)
    rows = [row for row in rows if row[0] >= station_altitude]
    return Profile(
        observation_time=observation_time,
        height_agl_m=[row[0] - station_altitude for row in rows],
        temperature_c=[row[1] for row in rows],
        relative_humidity_percent=[row[2] for row in rows],
        source_date=observation_time.date(),
        source_name="Sodankylä radiosonde",
    )


def _sounding_slots(day, now_utc, lookback_days):
    latest = min(
        now_utc,
        datetime.datetime.combine(
            day + datetime.timedelta(days=1),
            datetime.time.min,
            tzinfo=datetime.timezone.utc,
        ),
    )
    for days_back in range(lookback_days + 1):
        candidate_day = day - datetime.timedelta(days=days_back)
        for hour in (12, 0):
            candidate = datetime.datetime.combine(
                candidate_day,
                datetime.time(hour=hour),
                tzinfo=datetime.timezone.utc,
            )
            if candidate <= latest:
                yield candidate


def fetch_latest_sounding(day, now_utc=None, lookback_days=3):
    """Fetch the newest available 00/12 UTC Sodankylä sounding."""
    now_utc = now_utc or datetime.datetime.now(tz=datetime.timezone.utc)
    errors = []
    for candidate in _sounding_slots(day, now_utc, lookback_days):
        query = urlencode({
            "datetime": candidate.strftime("%Y-%m-%d %H:%M:%S"),
            "id": SODANKYLA_WMO_ID,
            "src": "BUFR",
            "type": "TEXT:LIST",
        })
        try:
            page = _request_bytes("{}?{}".format(UWYO_SOUNDING_URL, query))
            return parse_uwyo_sounding(page, candidate)
        except WeatherSourceError as exc:
            errors.append(str(exc))
    detail = errors[-1] if errors else "no eligible sounding times"
    raise WeatherSourceError("no recent Sodankylä sounding: {}".format(detail))
