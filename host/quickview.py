#!/usr/bin/env python3
"""
QuickView: live ground control dashboard for JSONL backups written by cli.py --log-file.

Usage:
python host/quickview.py --log-file ground_dump.jsonl
python host/quickview.py -f ground_dump.jsonl --qnh 1015.7 --max-points 600

Supported JSON line formats:
- {"pc_time": "...", "payload": "matorova", "data": {...}} (wrapped)
- {...} with "payload_id" inside (flat, cli.py format)

Panels (top to bottom, shared local-time X axis):
0. Battery (V) -> with warning/cutoff reference lines
1. Temperatures (C) -> CPU / pressure sensor / RH, per payload
2. Pressure (hPa)
3. Altitude (m) -> GPS (solid) vs pressure-derived via QNH (dashed)
4. Relative humidity (%)
5. Flow (L/min, left axis) + cumulative sampled volume (L, right axis)
6. OPC-N3 size-distribution heatmap (kenttarova only): time x bin (0-23), raw counts
7. OPC-N3 scalars (kenttarova only): temperature / humidity / sample flow + laser status
"""

import argparse
import json
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from matplotlib.lines import Line2D

# ---------------------------------------------------------------------------
# General configuration
# ---------------------------------------------------------------------------
PAYLOADS = ["matorova", "kenttarova"]

# "Flight instrument" palette: cyan and amber on near-black background
PAYLOAD_COLORS = {
    "matorova": "#39C8E8",    # cyan
    "kenttarova": "#F5A623",  # amber
}

BAT_WARN_V = 19.8
BAT_CUTOFF_V = 18.6
TEMP_WARN_C = 45.0
TEMP_CRITICAL_C = 55.0

MIN_VALID_YEAR = 2024        # minimum year to trust rtc_time (factory default is 2020)
MAX_ANCHOR_GAP_S = 6 * 3600  # discard reconstructed timestamps drifting further than this

FILL_UINT = 4294967295
FILL_INT = -999999


def parse_args():
    p = argparse.ArgumentParser(description="QuickView: live ground control terminal for vertical_sampler")
    p.add_argument("--log-file", "-f", default="ground_dump.jsonl",
                   help="JSONL file written by cli.py --log-file")
    p.add_argument("--max-points", type=int, default=0,
                   help="Max points per payload kept in memory (0 = unlimited, full history)")
    p.add_argument("--interval-ms", type=int, default=1000,
                   help="Refresh interval (ms)")
    p.add_argument("--qnh", type=float, default=1013.25,
                   help="Today's QNH (hPa), used to derive altitude from pressure")
    p.add_argument("--theme", choices=["dark", "light"], default="dark",
                   help="Dashboard visual theme")
    p.add_argument("--stale-after", type=float, default=90.0,
                   help="Seconds without data before a payload/actuator is marked stale")
    return p.parse_args()


def apply_theme(theme):
    if theme == "dark":
        plt.style.use("dark_background")
        return {
            "bg": "#0a0e14", "panel": "#0f141c", "grid": "#22303c",
            "text": "#d7e2ea", "text_muted": "#7c8b96",
            "ok": "#3ddc84", "warn": "#f5c518", "crit": "#ff5c5c", "off": "#3a4550",
        }
    plt.style.use("default")
    return {
        "bg": "#f4f6f8", "panel": "#ffffff", "grid": "#c9d2d8",
        "text": "#1b232a", "text_muted": "#5a6570",
        "ok": "#2e9e5b", "warn": "#b8860b", "crit": "#c62828", "off": "#a9b3ba",
    }


def baro_altitude_m(pressure_hpa, qnh_hpa):
    """Standard ISA altitude (m) from pressure and reference QNH."""
    if pressure_hpa is None or pressure_hpa <= 0 or qnh_hpa is None or qnh_hpa <= 0:
        return float("nan")
    return 44330.77 * (1.0 - (pressure_hpa / qnh_hpa) ** 0.1902632)


class QuickView:
    def __init__(self, log_file, max_points, stale_after, theme, qnh):
        self.log_file = log_file
        self.max_points = max_points if max_points > 0 else None
        self.stale_after = stale_after
        self.qnh = qnh
        self.palette = apply_theme(theme)
        self.file_pos = 0

        maker = (lambda: deque(maxlen=self.max_points)) if self.max_points else (lambda: deque())
        self.timestamps = defaultdict(maker)
        self.series = defaultdict(lambda: defaultdict(maker))
        self.last = {p: {} for p in PAYLOADS}
        self.last_seen_wall = {p: None for p in PAYLOADS}

        # cumulative sampled volume (L), continuous trapezoidal integration (never trimmed)
        self._volume_l = {p: 0.0 for p in PAYLOADS}
        self._last_flow = {p: None for p in PAYLOADS}
        self._last_flow_time = {p: None for p in PAYLOADS}

        # time anchor per payload: (gps_time_at_anchor, real_datetime_at_anchor)
        self._anchor = {p: None for p in PAYLOADS}

        self._build_figure()
        self.read_all_existing_lines()

    # ------------------------------------------------------------------
    # Figure and axes
    # ------------------------------------------------------------------
    def _build_figure(self):
        pal = self.palette
        self.fig = plt.figure(figsize=(19, 17.5), facecolor=pal["bg"])
        try:
            self.fig.canvas.manager.set_window_title("QuickView - vertical_sampler")
        except Exception:
            pass

        # 8 rows: original 6 + OPC-N3 heatmap + OPC-N3 scalars (kenttarova only)
        gs = gridspec.GridSpec(
            8, 1, figure=self.fig,
            height_ratios=[1, 1, 0.85, 1, 0.85, 1.05, 1.3, 1],
            hspace=0.32,
            left=0.075, right=0.965, top=0.955, bottom=0.055,
        )

        self.ax_batt = self.fig.add_subplot(gs[0])
        self.ax_temp = self.fig.add_subplot(gs[1], sharex=self.ax_batt)
        self.ax_press = self.fig.add_subplot(gs[2], sharex=self.ax_batt)
        self.ax_alt = self.fig.add_subplot(gs[3], sharex=self.ax_batt)
        self.ax_hum = self.fig.add_subplot(gs[4], sharex=self.ax_batt)
        self.ax_flow = self.fig.add_subplot(gs[5], sharex=self.ax_batt)
        self.ax_opc_heat = self.fig.add_subplot(gs[6], sharex=self.ax_batt)
        self.ax_opc_scalars = self.fig.add_subplot(gs[7], sharex=self.ax_batt)

        self.time_axes = [self.ax_batt, self.ax_temp, self.ax_press,
                           self.ax_alt, self.ax_hum, self.ax_flow,
                           self.ax_opc_heat, self.ax_opc_scalars]

        self.ax_vol = self.ax_flow.twinx()
        self.ax_opc_laser = self.ax_opc_scalars.twinx()

        for ax in self.time_axes:
            ax.set_facecolor(pal["panel"])
            for spine in ax.spines.values():
                spine.set_color(pal["grid"])

        # OPC-N3 heatmap: 24 size bins x time, raw counts, kenttarova only.
        # pcolormesh needs a full redraw each frame (no incremental set_data),
        # so we keep it cheap by capping resolution via OPC_HEATMAP_MAX_COLS.
        self._opc_bin_history = deque(maxlen=None)   # list of 24-value rows
        self._opc_time_history = deque(maxlen=None)  # matching datetimes
        self._opc_mesh = None
        self._opc_colorbar = None

        self._init_lines()
        self._style_axes()

    def _init_lines(self):
        self.lines = defaultdict(dict)

        def make(ax, key, linestyle="-", alpha=1.0, marker=None):
            for payload in PAYLOADS:
                color = PAYLOAD_COLORS[payload]
                (line,) = ax.plot(
                    [], [], color=color, linewidth=1.9, linestyle=linestyle,
                    alpha=alpha, marker=marker, markersize=2.5,
                )
                self.lines[key][payload] = line

        make(self.ax_batt, "battery_voltage")
        make(self.ax_temp, "cpu_temperature")
        make(self.ax_temp, "pressure_sensor_temperature", linestyle="--", alpha=0.75)
        make(self.ax_temp, "rh_sensor_temperature", linestyle=":", alpha=0.85)
        make(self.ax_press, "pressure_sensor_pressure")
        make(self.ax_alt, "gps_altitude")
        make(self.ax_alt, "baro_altitude", linestyle="--", alpha=0.85)
        make(self.ax_hum, "rh_sensor_humidity")
        make(self.ax_flow, "flow")
        make(self.ax_vol, "volume_l", linestyle="--", alpha=0.9)

        # OPC-N3 scalars: kenttarova only, single-payload lines (no per-payload loop).
        opc_color = PAYLOAD_COLORS["kenttarova"]
        (self.opc_temp_line,) = self.ax_opc_scalars.plot(
            [], [], color=opc_color, linewidth=1.8, linestyle="-", label="temp"
        )
        (self.opc_hum_line,) = self.ax_opc_scalars.plot(
            [], [], color=opc_color, linewidth=1.6, linestyle="--", alpha=0.8, label="humidity"
        )
        (self.opc_flow_line,) = self.ax_opc_scalars.plot(
            [], [], color=opc_color, linewidth=1.4, linestyle=":", alpha=0.85, label="sample flow"
        )
        (self.opc_laser_line,) = self.ax_opc_laser.plot(
            [], [], color=self.palette["crit"], linewidth=1.2, linestyle="-",
            drawstyle="steps-post", alpha=0.9, label="laser"
        )

    def _style_axes(self):
        pal = self.palette

        self.ax_batt.axhline(BAT_WARN_V, color=pal["warn"], linestyle=":", linewidth=1.1, alpha=0.8)
        self.ax_batt.axhline(BAT_CUTOFF_V, color=pal["crit"], linestyle=":", linewidth=1.1, alpha=0.9)
        self.ax_batt.text(0.005, 0.06, f"cutoff {BAT_CUTOFF_V}V", transform=self.ax_batt.transAxes,
                           fontsize=8.5, color=pal["crit"], fontfamily="monospace")
        self.ax_batt.text(0.005, 0.18, f"warn {BAT_WARN_V}V", transform=self.ax_batt.transAxes,
                           fontsize=8.5, color=pal["warn"], fontfamily="monospace")

        self.ax_temp.axhline(TEMP_WARN_C, color=pal["warn"], linestyle=":", linewidth=1.0, alpha=0.7)
        self.ax_temp.axhline(TEMP_CRITICAL_C, color=pal["crit"], linestyle=":", linewidth=1.0, alpha=0.8)

        # compact ylabels to save vertical space
        ylabels = [
            (self.ax_batt, "BATTERY\n(V)"),
            (self.ax_temp, "TEMP\n(C)"),
            (self.ax_press, "PRESSURE\n(hPa)"),
            (self.ax_alt, "ALTITUDE\n(m)"),
            (self.ax_hum, "HUMIDITY\n(%)"),
            (self.ax_flow, "FLOW\n(L/min)"),
            (self.ax_opc_heat, "OPC BIN\n(0-23)"),
            (self.ax_opc_scalars, "OPC\n(C, %RH)"),
        ]
        for ax, label in ylabels:
            ax.set_ylabel(label, fontsize=9, color=pal["text_muted"],
                           fontfamily="monospace", rotation=0, ha="right", va="center", labelpad=14)

        self.ax_vol.set_ylabel("VOL\n(L)", fontsize=9, color=pal["text_muted"],
                                fontfamily="monospace", rotation=0, ha="left", va="center", labelpad=14)
        self.ax_opc_laser.set_ylabel("LASER\n(0/1)", fontsize=9, color=pal["text_muted"],
                                      fontfamily="monospace", rotation=0, ha="left", va="center", labelpad=14)
        self.ax_opc_laser.set_ylim(-0.15, 1.15)

        # small legend markers inside each panel corner instead of a text-heavy title
        self.ax_temp.text(0.995, 0.94, "solid=CPU  dash=press  dot=RH", transform=self.ax_temp.transAxes,
                           fontsize=7.5, color=pal["text_muted"], ha="right", va="top", fontfamily="monospace")
        self.ax_alt.text(0.995, 0.94, "solid=GPS  dash=baro", transform=self.ax_alt.transAxes,
                          fontsize=7.5, color=pal["text_muted"], ha="right", va="top", fontfamily="monospace")
        self.ax_flow.text(0.995, 0.94, f"QNH={self.qnh:.1f}hPa  dash=cum.vol", transform=self.ax_flow.transAxes,
                           fontsize=7.5, color=pal["text_muted"], ha="right", va="top", fontfamily="monospace")
        self.ax_opc_heat.text(0.995, 0.94, "kenttarova raw bin counts", transform=self.ax_opc_heat.transAxes,
                               fontsize=7.5, color=pal["text_muted"], ha="right", va="top", fontfamily="monospace")
        self.ax_opc_scalars.text(0.995, 0.94, "solid=temp  dash=humidity  dot=flow  red=laser",
                                  transform=self.ax_opc_scalars.transAxes,
                                  fontsize=7.5, color=pal["text_muted"], ha="right", va="top", fontfamily="monospace")

        for ax in self.time_axes:
            ax.grid(True, alpha=0.35, color=pal["grid"], linewidth=0.7)
            ax.tick_params(axis="both", labelsize=9, colors=pal["text"])
            for lbl in ax.get_yticklabels():
                lbl.set_fontfamily("monospace")

        self.ax_vol.tick_params(axis="y", labelsize=9, colors=pal["text_muted"])
        for lbl in self.ax_vol.get_yticklabels():
            lbl.set_fontfamily("monospace")
        self.ax_opc_laser.tick_params(axis="y", labelsize=9, colors=pal["text_muted"])
        for lbl in self.ax_opc_laser.get_yticklabels():
            lbl.set_fontfamily("monospace")

        # hide x labels on all but the bottom (OPC scalars) axis and set a proper time formatter
        for ax in self.time_axes[:-1]:
            ax.tick_params(labelbottom=False)

        self.date_formatter = mdates.DateFormatter("%H:%M:%S")
        self.ax_opc_scalars.xaxis.set_major_formatter(self.date_formatter)
        for lbl in self.ax_opc_scalars.get_xticklabels():
            lbl.set_rotation(25)
            lbl.set_ha("right")
            lbl.set_fontfamily("monospace")

        # single global legend (payload colors)
        legend_handles = [
            Line2D([0], [0], color=PAYLOAD_COLORS[p], linewidth=3, label=p.upper())
            for p in PAYLOADS
        ]
        self.fig.legend(
            handles=legend_handles, loc="upper right", fontsize=11, framealpha=0.25,
            ncols=2, bbox_to_anchor=(0.965, 0.985), prop={"family": "monospace", "weight": "bold"},
        )

    # ------------------------------------------------------------------
    # Log reading and parsing
    # ------------------------------------------------------------------
    def read_all_existing_lines(self):
        """Load the entire existing history from the file on startup."""
        try:
            with open(self.log_file, "r") as f:
                lines = f.readlines()
                self.file_pos = f.tell()
        except FileNotFoundError:
            return
        for raw in lines:
            self._ingest_line(raw)

    def read_new_lines(self):
        try:
            with open(self.log_file, "r") as f:
                f.seek(self.file_pos)
                new_lines = f.readlines()
                self.file_pos = f.tell()
        except FileNotFoundError:
            return []
        return new_lines

    @staticmethod
    def extract_payload_and_data(entry):
        if not isinstance(entry, dict):
            return None, None
        if "payload" in entry and "data" in entry:
            return entry.get("payload"), entry.get("data", {})
        pid = entry.get("payload_id")
        if pid:
            return pid, entry
        return None, None

    def timestamp_for_entry(self, entry, d, payload):
        """Return a real local datetime for this sample.

        Priority: valid rtc_time -> wrapped pc_time -> anchor + gps_time delta
        -> bootstrap a new anchor at "now" (degraded mode, e.g. GPS never got
        a fix during this session) so data is never silently dropped.
        """
        rtc_str = d.get("rtc_time") if isinstance(d, dict) else None
        gps_time = d.get("gps_time") if isinstance(d, dict) else None

        if rtc_str:
            try:
                dt = datetime.fromisoformat(rtc_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt.year >= MIN_VALID_YEAR:
                    dt_local = dt.astimezone()
                    if payload in PAYLOADS and gps_time is not None:
                        self._anchor[payload] = (gps_time, dt_local)
                    return dt_local
            except Exception:
                pass

        pc_time = entry.get("pc_time") if isinstance(entry, dict) else None
        if pc_time:
            try:
                dt = datetime.fromisoformat(pc_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone()
            except Exception:
                pass

        if payload in PAYLOADS and gps_time is not None:
            anchor = self._anchor[payload]
            if anchor is not None:
                anchor_gps, anchor_dt = anchor
                delta_s = gps_time - anchor_gps
                if abs(delta_s) < MAX_ANCHOR_GAP_S:
                    return anchor_dt + timedelta(seconds=delta_s)
            # no anchor yet at all (RTC never synced this session): bootstrap
            # one at "now" so the point is still plotted, using relative
            # gps_time spacing for everything that follows.
            now_local = datetime.now().astimezone()
            self._anchor[payload] = (gps_time, now_local)
            return now_local

        # no rtc_time, no pc_time, no gps_time at all: last resort
        return datetime.now().astimezone()

    def _ingest_line(self, raw):
        raw = raw.strip()
        if not raw:
            return
        try:
            entry = json.loads(raw)
        except Exception:
            return
        payload, d = self.extract_payload_and_data(entry)
        if payload not in PAYLOADS or d is None:
            return

        dt_local = self.timestamp_for_entry(entry, d, payload)
        self.timestamps[payload].append(dt_local)
        self.last_seen_wall[payload] = datetime.now().astimezone()

        for key in ("battery_voltage", "cpu_temperature", "pressure_sensor_temperature",
                    "rh_sensor_temperature", "pressure_sensor_pressure", "gps_altitude",
                    "rh_sensor_humidity", "flow", "rssi",
                    "pump_front_state", "pump_back_state", "valve_state"):
            val = d.get(key)
            self.series[key][payload].append(val if val is not None else float("nan"))

        baro_alt = baro_altitude_m(d.get("pressure_sensor_pressure"), self.qnh)
        self.series["baro_altitude"][payload].append(baro_alt)

        # continuous trapezoidal integration of flow -> volume (L)
        flow = d.get("flow")
        if flow is not None:
            prev_flow = self._last_flow[payload]
            prev_t = self._last_flow_time[payload]
            if prev_flow is not None and prev_t is not None:
                dt_min = (dt_local - prev_t).total_seconds() / 60.0
                if 0 < dt_min < 30:  # ignore huge gaps (payload dropout, restart, etc.)
                    self._volume_l[payload] += 0.5 * (flow + prev_flow) * dt_min
            self._last_flow[payload] = flow
            self._last_flow_time[payload] = dt_local
        self.series["volume_l"][payload].append(self._volume_l[payload])

        # OPC-N3: kenttarova only. Raw bin counts arrive as uint16 with a fill
        # value (FILL_UINT = 4294967295) when the sensor was unavailable.
        if payload == "kenttarova":
            bin_vals = []
            any_bin = False
            for i in range(24):
                v = d.get("opc_bin_{}".format(i))
                if v is None or v == FILL_UINT:
                    bin_vals.append(float("nan"))
                else:
                    bin_vals.append(float(v))
                    any_bin = True
            if any_bin:
                self._opc_bin_history.append(bin_vals)
                self._opc_time_history.append(dt_local)

            for key in ("opc_temperature", "opc_humidity", "opc_sample_flow"):
                val = d.get(key)
                self.series[key][payload].append(val if val is not None else float("nan"))
            laser = d.get("opc_laser_status")
            self.series["opc_laser_status"][payload].append(
                float(laser) if laser is not None and laser != FILL_INT else float("nan")
            )

        self.last[payload] = d

    def update_from_lines(self):
        for raw in self.read_new_lines():
            self._ingest_line(raw)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def _set_line_data(self, ax_key, payload):
        xs = mdates.date2num(list(self.timestamps[payload])) if self.timestamps[payload] else []
        ys = list(self.series[ax_key][payload])
        self.lines[ax_key][payload].set_data(xs, ys)
        return xs

    OPC_HEATMAP_MAX_COLS = 400  # cap redraw cost: downsample if more points accumulate

    def _redraw_opc_heatmap(self):
        import numpy as np

        n = len(self._opc_time_history)
        if n < 2:
            return

        times = list(self._opc_time_history)
        bins = list(self._opc_bin_history)

        if n > self.OPC_HEATMAP_MAX_COLS:
            step = n // self.OPC_HEATMAP_MAX_COLS
            times = times[::step]
            bins = bins[::step]
            n = len(times)

        data = np.array(bins, dtype=float).T  # shape (24, n)
        xnums = mdates.date2num(times)

        if self._opc_mesh is not None:
            self._opc_mesh.remove()

        # pcolormesh needs edges, not centers: pad x by one dt and y by 0..24
        if n >= 2:
            dt_edge = (xnums[-1] - xnums[0]) / max(n - 1, 1) if n > 1 else 1.0 / 86400.0
        else:
            dt_edge = 1.0 / 86400.0
        x_edges = np.concatenate([xnums, [xnums[-1] + dt_edge]])
        y_edges = np.arange(25)

        self._opc_mesh = self.ax_opc_heat.pcolormesh(
            x_edges, y_edges, data, cmap="viridis", shading="flat"
        )

        if self._opc_colorbar is None:
            self._opc_colorbar = self.fig.colorbar(
                self._opc_mesh, ax=self.ax_opc_heat, pad=0.01, fraction=0.02
            )
            self._opc_colorbar.set_label("raw count", fontsize=8, color=self.palette["text_muted"])
            self._opc_colorbar.ax.tick_params(labelsize=7.5, colors=self.palette["text_muted"])
        else:
            self._opc_colorbar.update_normal(self._opc_mesh)

        self.ax_opc_heat.set_ylim(0, 24)

    def update_plot(self, _frame):
        self.update_from_lines()

        all_xnums = []
        for payload in PAYLOADS:
            xs = self._set_line_data("battery_voltage", payload)
            if len(xs):
                all_xnums.extend(xs)
            for key in ("cpu_temperature", "pressure_sensor_temperature", "rh_sensor_temperature",
                        "pressure_sensor_pressure", "gps_altitude", "baro_altitude",
                        "rh_sensor_humidity", "flow", "volume_l"):
                self._set_line_data(key, payload)

        opc_payload = "kenttarova"
        opc_xs = mdates.date2num(list(self.timestamps[opc_payload])) if self.timestamps[opc_payload] else []
        self.opc_temp_line.set_data(opc_xs, list(self.series["opc_temperature"][opc_payload]))
        self.opc_hum_line.set_data(opc_xs, list(self.series["opc_humidity"][opc_payload]))
        self.opc_flow_line.set_data(opc_xs, list(self.series["opc_sample_flow"][opc_payload]))
        self.opc_laser_line.set_data(opc_xs, list(self.series["opc_laser_status"][opc_payload]))

        self._redraw_opc_heatmap()

        if all_xnums:
            xmin, xmax = min(all_xnums), max(all_xnums)
            span = max(1 / 86400.0, xmax - xmin)
            self.ax_batt.set_xlim(xmin - span * 0.02, xmax + span * 0.05)

        for ax in self.time_axes + [self.ax_vol, self.ax_opc_laser]:
            if ax is self.ax_opc_heat:
                continue
            ax.relim()
            ax.autoscale_view(scalex=False)

        artists = []
        for m in self.lines.values():
            artists.extend(m.values())
        artists.extend([self.opc_temp_line, self.opc_hum_line, self.opc_flow_line, self.opc_laser_line])
        if self._opc_mesh is not None:
            artists.append(self._opc_mesh)
        return artists

    def run(self, interval_ms=1000):
        self.ani = animation.FuncAnimation(
            self.fig, self.update_plot, interval=interval_ms, blit=False, cache_frame_data=False,
        )
        plt.show()


def main():
    args = parse_args()
    qv = QuickView(
        log_file=args.log_file,
        max_points=args.max_points,
        stale_after=args.stale_after,
        theme=args.theme,
        qnh=args.qnh,
    )
    qv.run(interval_ms=args.interval_ms)


if __name__ == "__main__":
    main()
