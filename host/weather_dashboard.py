#!/usr/bin/env python3
"""Daily low-cloud planning dashboard for the Matorova campaign."""

import argparse
import concurrent.futures
import datetime
import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

if __package__:
    from .weather_sources import (
        LOW_CLOUD_LIMIT_M,
        CloudnetCache,
        WeatherSourceError,
        fetch_cloud_layers,
        fetch_ecmwf_forecast,
        fetch_latest_sounding,
        fetch_model_cloud_profile,
        fetch_mwr_profile,
    )
else:
    from weather_sources import (
        LOW_CLOUD_LIMIT_M,
        CloudnetCache,
        WeatherSourceError,
        fetch_cloud_layers,
        fetch_ecmwf_forecast,
        fetch_latest_sounding,
        fetch_model_cloud_profile,
        fetch_mwr_profile,
    )


@dataclass
class DashboardData:
    forecast: object = None
    model_cloud: object = None
    cloud_layers: object = None
    mwr_profile: object = None
    sounding: object = None
    errors: dict = None


LOCAL_TIMEZONE = ZoneInfo("Europe/Helsinki")
CLOUDNET_TARGET_TYPES = {
    1: ("Liquid droplets", "#27ae60"),
    2: ("Drizzle or rain", "#2980b9"),
    3: ("Liquid + drizzle/rain", "#00a6a6"),
    4: ("Ice particles", "#7b2cbf"),
    5: ("Ice + supercooled liquid", "#d450b6"),
    6: ("Melting ice", "#f39c12"),
    7: ("Melting ice + liquid", "#e74c3c"),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Current-day Matorova low-cloud planning dashboard"
    )
    parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        default=None,
        help="Fixed dashboard date in YYYY-MM-DD format (default: current day)",
    )
    parser.add_argument(
        "--refresh-minutes",
        type=float,
        default=10.0,
        help="Automatic refresh interval; 0 disables refresh (default: 10)",
    )
    parser.add_argument(
        "--cache-dir",
        help="Cloudnet cache directory (default: ~/.cache/vertical_sampler/weather)",
    )
    parser.add_argument(
        "--output",
        help="Write a PNG instead of opening the interactive dashboard",
    )
    return parser.parse_args()


def load_dashboard_data(day, cache):
    errors = {}
    results = {}
    fetchers = {
        "forecast": lambda: fetch_ecmwf_forecast(day),
        "model_cloud": lambda: fetch_model_cloud_profile(day, cache=cache),
        "cloud_layers": lambda: fetch_cloud_layers(day, cache=cache),
        "mwr_profile": lambda: fetch_mwr_profile(day, cache=cache),
        "sounding": lambda: fetch_latest_sounding(day),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(fetchers)) as executor:
        futures = {
            executor.submit(fetcher): name for name, fetcher in fetchers.items()
        }
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except (
                WeatherSourceError,
                OSError,
                KeyError,
                ValueError,
                TypeError,
                IndexError,
                RuntimeError,
            ) as exc:
                results[name] = None
                errors[name] = str(exc)
    return DashboardData(errors=errors, **results)


def _latest_cloud_layer(layers):
    for timestamp, base, top in reversed(
        list(zip(layers.times, layers.base_agl_m, layers.top_agl_m))
    ):
        if math.isfinite(base) and math.isfinite(top):
            return timestamp, base, top
    return None, float("nan"), float("nan")


def _format_time(value):
    if value is None:
        return "N/A"
    if value.tzinfo is not None:
        value = value.astimezone(LOCAL_TIMEZONE)
    return value.strftime("%Y-%m-%d %H:%M %Z")


def _local_plot_times(values):
    result = []
    for value in values:
        if value.tzinfo is not None:
            value = value.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
        result.append(value)
    return result


def _cell_edges(centers):
    centers = np.asarray(centers, dtype=float)
    if centers.size == 1:
        return np.array([centers[0] - 0.5, centers[0] + 0.5])
    midpoints = (centers[:-1] + centers[1:]) / 2.0
    return np.concatenate((
        [centers[0] - (midpoints[0] - centers[0])],
        midpoints,
        [centers[-1] + (centers[-1] - midpoints[-1])],
    ))


def _regular_model_cloud_grid(model_cloud, max_height_m, step_m=25.0):
    target_heights = np.arange(0.0, max_height_m + step_m, step_m)
    source_heights = np.asarray(model_cloud.height_agl_m, dtype=float)
    fields = (
        np.asarray(model_cloud.cloud_fraction_percent, dtype=float),
        np.asarray(model_cloud.liquid_mask, dtype=float),
        np.asarray(model_cloud.ice_mask, dtype=float),
        np.asarray(model_cloud.precipitation_mask, dtype=float),
    )
    interpolated = [
        np.full((target_heights.size, source_heights.shape[0]), np.nan)
        for _ in fields
    ]
    for time_index in range(source_heights.shape[0]):
        heights = source_heights[time_index]
        valid_heights = np.isfinite(heights)
        for output, field in zip(interpolated, fields):
            values = field[time_index]
            valid = valid_heights & np.isfinite(values)
            if np.count_nonzero(valid) < 2:
                continue
            output[:, time_index] = np.interp(
                target_heights,
                heights[valid],
                values[valid],
                left=np.nan,
                right=np.nan,
            )
    fraction, liquid, ice, precipitation = interpolated
    return (
        target_heights,
        fraction,
        liquid >= 0.5,
        ice >= 0.5,
        precipitation >= 0.5,
    )


class WeatherDashboard:
    def __init__(self, day, cache_dir=None):
        self.fixed_day = day
        self.day = day or datetime.date.today()
        self.cache = CloudnetCache(cache_dir)
        self.figure = plt.figure(figsize=(16, 10), constrained_layout=True)
        self.figure.canvas.manager.set_window_title(
            "Matorova low-cloud dashboard"
        )
        grid = self.figure.add_gridspec(3, 3, height_ratios=(0.34, 1, 1))
        self.summary_axis = self.figure.add_subplot(grid[0, :])
        self.forecast_axis = self.figure.add_subplot(grid[1, :2])
        self.weather_axis = self.figure.add_subplot(grid[1, 2])
        self.cloud_axis = self.figure.add_subplot(grid[2, 0])
        self.temperature_axis = self.figure.add_subplot(grid[2, 1])
        self.humidity_axis = self.figure.add_subplot(grid[2, 2], sharey=self.temperature_axis)
        self.weather_humidity_axis = self.weather_axis.twinx()
        self.forecast_cloud_colorbar = None
        self.animation = None

    def refresh(self):
        if self.fixed_day is None:
            self.day = datetime.date.today()
        data = load_dashboard_data(self.day, self.cache)
        self._draw(data)
        self.figure.canvas.draw_idle()

    def _draw(self, data):
        axes = (
            self.summary_axis,
            self.forecast_axis,
            self.weather_axis,
            self.weather_humidity_axis,
            self.cloud_axis,
            self.temperature_axis,
            self.humidity_axis,
        )
        for axis in axes:
            axis.clear()
            axis.grid(True, alpha=0.25)

        self._draw_summary(data)
        self._draw_forecast(data.forecast, data.model_cloud)
        self._draw_surface_weather(data.forecast)
        self._draw_cloud_layers(data.cloud_layers)
        self._draw_profiles(data.mwr_profile, data.sounding)
        self.figure.suptitle(
            "Matorova low-cloud measurement dashboard — {}".format(
                self.day.isoformat()
            ),
            fontsize=16,
            fontweight="bold",
        )

    def _draw_summary(self, data):
        axis = self.summary_axis
        axis.axis("off")
        lines = [
            "Operational focus: cloud layers at or below {:.1f} km AGL".format(
                LOW_CLOUD_LIMIT_M / 1000
            ),
            "Forecast: ECMWF IFS via Open-Meteo | Observations: ACTRIS Cloudnet Kenttärova | Sounding: University of Wyoming/Sodankylä",
        ]
        if data.cloud_layers is not None:
            layer_time, base, top = _latest_cloud_layer(data.cloud_layers)
            lines.append(
                "Latest Cloudnet layer: base {} m, top {} m AGL at {} | file updated {}".format(
                    "{:.0f}".format(base) if math.isfinite(base) else "N/A",
                    "{:.0f}".format(top) if math.isfinite(top) else "N/A",
                    _format_time(layer_time),
                    data.cloud_layers.updated_at or "N/A",
                )
            )
            if data.cloud_layers.source_date != self.day:
                lines.append(
                    "WARNING: Cloudnet observations are from {}, not dashboard date".format(
                        data.cloud_layers.source_date.isoformat()
                    )
                )
            if data.cloud_layers.error_level != "pass":
                lines.append(
                    "Cloudnet classification QC: {}".format(
                        data.cloud_layers.error_level
                    )
                )
        if data.model_cloud is not None:
            lines.append(
                "Vertical forecast: {} | model file updated {}".format(
                    data.model_cloud.model_name,
                    data.model_cloud.updated_at or "N/A",
                )
            )
            if data.model_cloud.source_date != self.day:
                lines.append(
                    "WARNING: model cloud profile is from {}, not dashboard date".format(
                        data.model_cloud.source_date.isoformat()
                    )
                )
        if data.mwr_profile is not None:
            lines.append(
                "Latest MWR profile: {}{}".format(
                    _format_time(data.mwr_profile.observation_time),
                    " | " + data.mwr_profile.quality_note
                    if data.mwr_profile.quality_note
                    else "",
                )
            )
        if data.sounding is not None:
            lines.append(
                "Latest Sodankylä sounding: {}".format(
                    _format_time(data.sounding.observation_time)
                )
            )
        for source, error in data.errors.items():
            lines.append("{} unavailable: {}".format(source.replace("_", " "), error))
        axis.text(
            0.01,
            0.92,
            "\n".join(lines),
            transform=axis.transAxes,
            va="top",
            fontsize=10,
            family="monospace",
        )

    def _draw_forecast(self, forecast, model_cloud):
        axis = self.forecast_axis
        model_label = (
            model_cloud.model_id.upper() if model_cloud is not None else "Model"
        )
        axis.set_title(
            "{} cloud fraction/type and ECMWF IFS boundary layer".format(
                model_label
            )
        )
        axis.set_ylabel("Height (m AGL)")
        axis.set_ylim(0, max(2200, LOW_CLOUD_LIMIT_M * 1.1))
        axis.axhspan(0, LOW_CLOUD_LIMIT_M, color="#4c9be8", alpha=0.07)
        axis.axhline(
            LOW_CLOUD_LIMIT_M,
            color="#4c9be8",
            linestyle="--",
            linewidth=1,
            label="2 km measurement ceiling",
        )
        if forecast is None:
            axis.text(
                0.5,
                0.5,
                "Boundary-layer forecast unavailable",
                ha="center",
                va="center",
            )
        else:
            axis.plot(
                _local_plot_times(forecast.times),
                forecast.boundary_layer_height_m,
                color="#005bbb",
                marker="o",
                markersize=3,
                linewidth=2,
                label="ECMWF IFS boundary-layer height",
                zorder=5,
            )

        if model_cloud is not None:
            if self.forecast_cloud_colorbar is not None:
                self.forecast_cloud_colorbar.ax.set_visible(True)
            times = _local_plot_times(model_cloud.times)
            time_centers = mdates.date2num(times)
            (
                grid_heights,
                fractions,
                liquid_mask,
                ice_mask,
                precipitation_mask,
            ) = _regular_model_cloud_grid(
                model_cloud,
                LOW_CLOUD_LIMIT_M * 1.1,
            )
            mesh = axis.pcolormesh(
                _cell_edges(time_centers),
                _cell_edges(grid_heights),
                np.ma.masked_less(fractions, 5.0),
                shading="flat",
                cmap="Blues",
                vmin=5,
                vmax=100,
                zorder=1,
            )
            if self.forecast_cloud_colorbar is None:
                self.forecast_cloud_colorbar = self.figure.colorbar(
                    mesh,
                    ax=axis,
                    pad=0.01,
                    label="Model cloud fraction (%)",
                )
            else:
                self.forecast_cloud_colorbar.update_normal(mesh)

            contour_specs = (
                (liquid_mask, "#00a896", "-", "Liquid"),
                (ice_mask, "#7b2cbf", "--", "Ice"),
                (
                    precipitation_mask,
                    "#d1495b",
                    ":",
                    "Precipitating",
                ),
            )
            type_handles = []
            for mask, color, linestyle, label in contour_specs:
                values = np.asarray(mask, dtype=float)
                if np.nanmin(values) < 0.5 < np.nanmax(values):
                    axis.contour(
                        time_centers,
                        grid_heights,
                        values,
                        levels=[0.5],
                        colors=[color],
                        linestyles=[linestyle],
                        linewidths=1.4,
                        zorder=4,
                    )
                    type_handles.append(
                        Line2D(
                            [0],
                            [0],
                            color=color,
                            linestyle=linestyle,
                            label=label,
                        )
                    )
            handles, labels = axis.get_legend_handles_labels()
            axis.legend(handles + type_handles, labels + [
                handle.get_label() for handle in type_handles
            ], loc="upper left", fontsize=8)
        elif forecast is not None:
            if self.forecast_cloud_colorbar is not None:
                self.forecast_cloud_colorbar.ax.set_visible(False)
            axis.text(
                0.99,
                0.97,
                "Vertical cloud forecast unavailable",
                transform=axis.transAxes,
                ha="right",
                va="top",
            )
            axis.legend(loc="upper left")
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        axis.set_xlim(
            datetime.datetime.combine(self.day, datetime.time.min),
            datetime.datetime.combine(
                self.day + datetime.timedelta(days=1),
                datetime.time.min,
            ),
        )
        axis.set_xlabel("Local time")

    def _draw_surface_weather(self, forecast):
        axis = self.weather_axis
        axis.set_title("Surface forecast")
        if forecast is None:
            axis.text(0.5, 0.5, "Forecast unavailable", ha="center", va="center")
            return
        axis.plot(
            _local_plot_times(forecast.times),
            forecast.temperature_c,
            color="#d62728",
            label="Temperature (°C)",
        )
        axis.plot(
            _local_plot_times(forecast.times),
            forecast.wind_speed_kmh,
            color="#2ca02c",
            label="Wind (km/h)",
        )
        axis.plot(
            _local_plot_times(forecast.times),
            forecast.precipitation_mm,
            color="#1f77b4",
            label="Precipitation (mm)",
        )
        humidity_axis = self.weather_humidity_axis
        humidity_axis.yaxis.tick_right()
        humidity_axis.yaxis.set_label_position("right")
        humidity_axis.plot(
            _local_plot_times(forecast.times),
            forecast.relative_humidity_percent,
            color="#9467bd",
            linestyle="--",
            label="RH (%)",
        )
        humidity_axis.set_ylim(0, 100)
        humidity_axis.set_ylabel("Relative humidity (%)")
        handles, labels = axis.get_legend_handles_labels()
        rh_handles, rh_labels = humidity_axis.get_legend_handles_labels()
        axis.legend(handles + rh_handles, labels + rh_labels, fontsize=8)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        axis.set_xlabel("Local time")
        axis.tick_params(axis="x", rotation=30)

    def _draw_cloud_layers(self, layers):
        axis = self.cloud_axis
        axis.set_title("Observed cloud types — Kenttärova")
        axis.set_ylabel("Height AGL (m)")
        axis.set_ylim(0, LOW_CLOUD_LIMIT_M)
        if layers is None:
            axis.text(0.5, 0.5, "Cloudnet unavailable", ha="center", va="center")
            return
        times = _local_plot_times(layers.times)
        heights = np.asarray(layers.heights_agl_m)
        low = np.isfinite(heights) & (heights >= 0) & (
            heights <= LOW_CLOUD_LIMIT_M
        )
        if np.any(low):
            classification = np.asarray(
                layers.target_classification, dtype=float
            )[:, low].T
            classification[
                ~np.isin(classification, tuple(CLOUDNET_TARGET_TYPES))
            ] = np.nan
            colors = [
                CLOUDNET_TARGET_TYPES[index][1]
                for index in sorted(CLOUDNET_TARGET_TYPES)
            ]
            axis.pcolormesh(
                _cell_edges(mdates.date2num(times)),
                _cell_edges(heights[low]),
                classification,
                shading="flat",
                cmap=ListedColormap(colors),
                norm=BoundaryNorm(np.arange(0.5, 8.5, 1.0), len(colors)),
            )
            present = {
                int(value)
                for value in classification[np.isfinite(classification)]
            }
            handles = [
                Patch(
                    facecolor=CLOUDNET_TARGET_TYPES[index][1],
                    label=CLOUDNET_TARGET_TYPES[index][0],
                )
                for index in sorted(present)
            ]
            if handles:
                axis.legend(handles=handles, loc="upper left", fontsize=7)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        axis.set_xlabel("Local time")
        axis.tick_params(axis="x", rotation=30)

    def _draw_profiles(self, mwr, sounding):
        self.temperature_axis.set_title("Temperature profiles")
        self.temperature_axis.set_xlabel(
            "Temperature and potential temperature (°C)"
        )
        self.temperature_axis.set_ylabel("Height above each station (m)")
        self.temperature_axis.set_ylim(0, LOW_CLOUD_LIMIT_M)
        self.humidity_axis.set_title("Relative-humidity profiles")
        self.humidity_axis.set_xlabel("Relative humidity (%)")
        self.humidity_axis.set_xlim(0, 105)
        self.humidity_axis.set_ylim(0, LOW_CLOUD_LIMIT_M)

        profiles = (
            (mwr, "MWR temperature", "#005bbb", "-"),
            (sounding, "Sodankylä temperature", "#e67e22", "--"),
        )
        for profile, label, color, linestyle in profiles:
            if profile is None:
                continue
            timestamp = _format_time(profile.observation_time)
            low_levels = [
                index
                for index, height in enumerate(profile.height_agl_m)
                if math.isfinite(height) and 0 <= height <= LOW_CLOUD_LIMIT_M
            ]
            temperatures = [profile.temperature_c[index] for index in low_levels]
            humidities = [
                profile.relative_humidity_percent[index] for index in low_levels
            ]
            heights = [profile.height_agl_m[index] for index in low_levels]
            self.temperature_axis.plot(
                temperatures,
                heights,
                color=color,
                linestyle=linestyle,
                label="{} ({})".format(label, timestamp),
            )
            self.humidity_axis.plot(
                humidities,
                heights,
                color=color,
                linestyle=linestyle,
                label="{} ({})".format(label, timestamp),
            )
            if profile.potential_temperature_c is not None:
                potential_temperatures = [
                    profile.potential_temperature_c[index]
                    for index in low_levels
                ]
                self.temperature_axis.plot(
                    potential_temperatures,
                    heights,
                    color="#00a896",
                    linestyle="-.",
                    linewidth=1.8,
                    label="MWR potential temperature θ ({})".format(timestamp),
                )
        if mwr is None and sounding is None:
            self.temperature_axis.text(
                0.5, 0.5, "Profiles unavailable", ha="center", va="center"
            )
        else:
            self.temperature_axis.legend(fontsize=8)
            self.humidity_axis.legend(fontsize=8)

    def run(self, refresh_minutes):
        self.refresh()
        if refresh_minutes > 0:
            self.animation = FuncAnimation(
                self.figure,
                lambda _frame: self.refresh(),
                interval=refresh_minutes * 60 * 1000,
                cache_frame_data=False,
            )
        plt.show()


def main():
    args = parse_args()
    dashboard = WeatherDashboard(args.date, cache_dir=args.cache_dir)
    if args.output:
        dashboard.refresh()
        dashboard.figure.savefig(args.output, dpi=150)
        return
    dashboard.run(args.refresh_minutes)


if __name__ == "__main__":
    main()
