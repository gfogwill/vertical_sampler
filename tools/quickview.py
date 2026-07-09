#!/usr/bin/env python3
"""
QuickView: visualizador rápido para los backups JSONL generados por cli.py --log-file.

Uso:
    python tools/quickview.py --log-file ground_dump.jsonl

Soporta dos formatos de línea JSON:
 - {"pc_time": "...", "payload": "matorova", "data": {...}}
 - {...}  (diccionario directo emitido por cli.py, con campo payload_id)

Muestra subplots apilados (uno por métrica), todos compartiendo el eje X (tiempo local).
Cada subplot contiene una línea por payload (matorova y kenttarova).
"""

import argparse
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.dates as mdates

# Métricas a mostrar (puedes ajustar)
METRICS = [
    "battery_voltage",
    "pressure_sensor_pressure",
    "flow",
    "rh_sensor_temperature",
    "gps_altitude",
]

PAYLOADS = ["matorova", "kenttarova"]  # orden visual


def parse_args():
    p = argparse.ArgumentParser(description="QuickView live plot for ground JSONL log")
    p.add_argument("--log-file", "-f", required=False, default="ground_dump.jsonl",
                   help="JSONL file written by cli.py --log-file")
    p.add_argument("--max-points", type=int, default=300,
                   help="Número de puntos en la ventana deslizante")
    p.add_argument("--interval-ms", type=int, default=1000, help="Intervalo de actualización (ms)")
    return p.parse_args()


class QuickView:
    def __init__(self, log_file, max_points=300):
        self.log_file = log_file
        self.max_points = max_points
        self.file_pos = 0

        # datos: payload -> metric -> deque
        self.data = defaultdict(lambda: defaultdict(lambda: deque(maxlen=self.max_points)))
        # timestamps por payload (datetime objects in local timezone)
        self.timestamps = defaultdict(lambda: deque(maxlen=self.max_points))

        # line handles: metric -> payload -> Line2D
        self.lines = defaultdict(dict)
        # last values for possible title/status
        self.last = {p: {} for p in PAYLOADS}

        # figure: one column, one row per metric
        nrows = len(METRICS)
        figsize = (12, 2.2 * nrows)
        self.fig, axes = plt.subplots(nrows, 1, figsize=figsize, sharex=True)
        if nrows == 1:
            axes = [axes]
        self.axes = axes

        # prepare axes and lines
        for ax, metric in zip(self.axes, METRICS):
            ax.set_ylabel(metric)
            for payload in PAYLOADS:
                (line,) = ax.plot([], [], label=payload)
                self.lines[metric][payload] = line
            ax.grid(True)
            ax.legend(loc="upper left", fontsize=8)

        # x-axis formatting: local time
        self.axes[-1].set_xlabel("hora local")
        self.date_formatter = mdates.DateFormatter('%H:%M:%S')
        self.axes[-1].xaxis.set_major_formatter(self.date_formatter)
        plt.tight_layout()

    def read_new_lines(self):
        """Lee nuevas líneas desde el archivo (append-friendly)."""
        try:
            with open(self.log_file, "r") as f:
                f.seek(self.file_pos)
                new_lines = f.readlines()
                self.file_pos = f.tell()
        except FileNotFoundError:
            return []
        return new_lines

    def extract_payload_and_data(self, entry):
        """Normaliza la entrada a (payload, data_dict) aceptando ambas formas."""
        if not isinstance(entry, dict):
            return None, None
        # Forma envuelta: {"payload": "matorova", "data": {...}}
        if "payload" in entry and "data" in entry:
            payload = entry.get("payload")
            data = entry.get("data", {})
            return payload, data
        # Forma "raw" que produce cli.py: payload_id en d.get("payload_id")
        pid = entry.get("payload_id") or entry.get("payload_id".lower())
        if pid:
            return pid, entry
        return None, None

    def parse_rtc_time_local(self, rtc_time_str):
        """Parse rtc_time ISO string and return a timezone-aware datetime in local timezone."""
        try:
            dt = datetime.fromisoformat(rtc_time_str)
        except Exception:
            return None
        # If no tzinfo, assume UTC (RTC is synced to UTC per README)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()  # convert to local timezone

    def timestamp_for_entry(self, entry, d):
        """Return a datetime object in local timezone for the entry.
        Priority: d['rtc_time'] (GPS/RTC) -> entry['pc_time'] -> d['_ts'] -> now
        """
        # Prefer RTC time from payload data when available (ISO string)
        if isinstance(d, dict) and d.get("rtc_time"):
            dt = self.parse_rtc_time_local(d.get("rtc_time"))
            if dt:
                return dt
        # Wrapped pc_time
        if isinstance(entry, dict) and entry.get("pc_time"):
            try:
                dt = datetime.fromisoformat(entry["pc_time"])
            except Exception:
                try:
                    ts = float(entry["pc_time"])
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
                except Exception:
                    dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone()
        # enriched _ts (epoch seconds) provided by CLI
        if isinstance(d, dict) and d.get("_ts") is not None:
            try:
                ts = float(d.get("_ts"))
                return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
            except Exception:
                pass
        # fallback: now local
        return datetime.now().astimezone()

    def update_from_lines(self):
        for raw in self.read_new_lines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            payload, d = self.extract_payload_and_data(entry)
            if payload not in PAYLOADS or d is None:
                continue

            dt_local = self.timestamp_for_entry(entry, d)
            # append timestamp
            self.timestamps[payload].append(dt_local)

            for metric in METRICS:
                val = d.get(metric)
                if val is None:
                    val = float("nan")
                self.data[payload][metric].append(val)

            # keep last values for possible title
            self.last[payload] = d

    def update_plot(self, frame):
        self.update_from_lines()

        # Update each metric axis with lines for both payloads
        for ax, metric in zip(self.axes, METRICS):
            # collect combined x-range
            all_dates = []
            for payload in PAYLOADS:
                xs = list(self.timestamps[payload])
                ys = list(self.data[payload][metric])
                # convert datetimes to matplotlib float dates
                if xs:
                    xnums = mdates.date2num(xs)
                else:
                    xnums = []
                line = self.lines[metric][payload]
                line.set_data(xnums, ys)
                all_dates.extend(xnums)

            if all_dates:
                xmin = min(all_dates)
                xmax = max(all_dates)
                # set xlim with small margin
                span = max(1 / 86400.0, xmax - xmin)  # at least one second
                ax.set_xlim(xmin - 0.001, xmax + 0.001 + span * 0.01)
            ax.relim()
            ax.autoscale_view(scalex=False)

        # format x axis on bottom subplot
        self.axes[-1].xaxis_date()
        self.axes[-1].xaxis.set_major_formatter(self.date_formatter)
        for lbl in self.axes[-1].get_xticklabels():
            lbl.set_rotation(30)
            lbl.set_ha("right")

        # update figure title or per-payload short titles
        # set suptitle with last timestamps per payload
        titles = []
        for p in PAYLOADS:
            last = self.last.get(p, {})
            ts = None
            if isinstance(last, dict):
                if last.get("rtc_time"):
                    try:
                        ts = self.parse_rtc_time_local(last.get("rtc_time"))
                    except Exception:
                        ts = None
            if ts is None:
                if self.timestamps[p]:
                    ts = self.timestamps[p][-1]
            if ts:
                titles.append(f"{p}: {ts.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                titles.append(p)
        self.fig.suptitle("  |  ".join(titles))

        # return artists
        artists = []
        for metric in METRICS:
            artists.extend(self.lines[metric].values())
        return artists

    def run(self, interval_ms=1000):
        ani = animation.FuncAnimation(self.fig, self.update_plot, interval=interval_ms, blit=False)
        plt.show()


def main():
    args = parse_args()
    q = QuickView(args.log_file, max_points=args.max_points)
    q.run(interval_ms=args.interval_ms)


if __name__ == "__main__":
    main()
