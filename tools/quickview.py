#!/usr/bin/env python3
"""
QuickView: visualizador rápido para los backups JSONL generados por cli.py --log-file.

Uso:
    python tools/quickview.py --log-file ground_dump.jsonl

Soporta dos formatos de línea JSON:
 - {"pc_time": "...", "payload": "matorova", "data": {...}}
 - {...}  (diccionario directo emitido por cli.py, con campo payload_id)

Muestra 2 filas (una por payload) y varias columnas (una por métrica + status).
"""

import argparse
import json
import time
from collections import defaultdict, deque
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.animation as animation

# Métricas a mostrar (puedes ajustar)
METRICS = [
    "battery_voltage",
    "pressure_sensor_pressure",
    "flow",
    "rh_sensor_temperature",
    "gps_altitude",
]

PAYLOADS = ["matorova", "kenttarova"]  # orden visual: arriba -> abajo


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
        # timestamps por payload (en segundos relativos)
        self.timestamps = defaultdict(lambda: deque(maxlen=self.max_points))
        self.first_ts = {}  # payload -> timestamp base (float)

        # line handles: payload -> metric -> Line2D
        self.lines = defaultdict(dict)
        # last status dictionary
        self.last = {p: {} for p in PAYLOADS}

        # figure layout: filas = len(PAYLOADS), columnas = len(METRICS) + 1 (status)
        nrows = len(PAYLOADS)
        ncols = len(METRICS) + 1
        figsize = (3 * ncols, 2.5 * nrows)
        self.fig, axes_grid = plt.subplots(nrows, ncols, figsize=figsize, sharex="col")

        # Ensure axes_grid is 2D
        if nrows == 1:
            axes_grid = [axes_grid]
        if ncols == 1:
            axes_grid = [[ax] for ax in axes_grid]

        self.axes = axes_grid

        # prepare axes and lines
        for r, payload in enumerate(PAYLOADS):
            for c, metric in enumerate(METRICS):
                ax = self.axes[r][c]
                ax.set_ylabel(metric)
                ax.set_title(f"{payload} — {metric}")
                (line,) = ax.plot([], [], label=payload)
                self.lines[payload][metric] = line
                ax.grid(True)

            # status axis (last column)
            st_ax = self.axes[r][len(METRICS)]
            st_ax.axis("off")  # we'll draw text manually

        # x-label for bottom axes
        self.axes[-1][0].set_xlabel("segundos desde inicio (payload)")
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
        # A veces el CLI puede not set payload_id; try to detect by fields (not reliable)
        return None, None

    def timestamp_for_entry(self, entry, d):
        """Devuelve timestamp flotante (segundos epoch) para el registro;
        prioridades: entry.pc_time -> d._ts -> ahora"""
        # Wrapped pc_time:
        pc_time = None
        if isinstance(entry, dict) and "pc_time" in entry:
            try:
                # Try parsing ISO; fallback to epoch on failure
                pc_time = datetime.fromisoformat(entry["pc_time"]).timestamp()
            except Exception:
                try:
                    pc_time = float(entry["pc_time"])
                except Exception:
                    pc_time = None
        if pc_time is not None:
            return pc_time
        # enriched _ts
        ts = d.get("_ts")
        if ts is not None:
            return float(ts)
        # gps_time can be relative; don't use it here as epoch
        return time.time()

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
            # timestamp epoch seconds
            t_epoch = self.timestamp_for_entry(entry, d)
            # set first_ts for payload (base)
            if payload not in self.first_ts:
                self.first_ts[payload] = t_epoch
            t_rel = t_epoch - self.first_ts[payload]
            self.timestamps[payload].append(t_rel)

            # store metrics
            for metric in METRICS:
                val = d.get(metric)
                # convert None -> nan to keep arrays consistent
                if val is None:
                    val = float("nan")
                self.data[payload][metric].append(val)

            # store last values for status
            self.last[payload] = {
                "battery_voltage": d.get("battery_voltage"),
                "rssi": d.get("rssi"),
                "pump_front_state": d.get("pump_front_state"),
                "pump_back_state": d.get("pump_back_state"),
                "valve_state": d.get("valve_state"),
                "_ts": t_epoch,
            }

    def draw_status(self, row_idx, payload):
        st_ax = self.axes[row_idx][len(METRICS)]
        st_ax.clear()
        st_ax.axis("off")
        last = self.last.get(payload, {})
        lines = []
        bv = last.get("battery_voltage")
        if bv is not None:
            try:
                lines.append(f"Battery: {bv:.2f} V")
            except Exception:
                lines.append(f"Battery: {bv}")
        else:
            lines.append("Battery: N/A")
        rssi = last.get("rssi")
        lines.append(f"RSSI: {rssi if rssi is not None else 'N/A'}")
        pf = last.get("pump_front_state")
        pb = last.get("pump_back_state")
        vl = last.get("valve_state")
        lines.append(f"Pump front: {'ON' if pf else 'OFF'}")
        lines.append(f"Pump back: {'ON' if pb else 'OFF'}")
        lines.append(f"Valve: {'ON' if vl else 'OFF'}")
        ts = last.get("_ts")
        if ts:
            lines.append("Last: " + datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S UTC"))
        # Draw text centered
        txt = "\n".join(lines)
        st_ax.text(0.5, 0.5, txt, ha="center", va="center", fontsize=9, family="monospace")
        return st_ax

    def update_plot(self, frame):
        # ingest new lines
        self.update_from_lines()

        # update lines for each payload/metric
        for r, payload in enumerate(PAYLOADS):
            xs = list(self.timestamps[payload])
            for metric in METRICS:
                ys = list(self.data[payload][metric])
                line = self.lines[payload].get(metric)
                if line is not None:
                    line.set_data(xs, ys)
                    ax = self.axes[r][METRICS.index(metric)]
                    ax.relim()
                    ax.autoscale_view()

            # update status axis
            self.draw_status(r, payload)

        # set x-limits globally to max range across payloads (nice to sync)
        all_x = []
        for p in PAYLOADS:
            all_x.extend(self.timestamps[p])
        if all_x:
            xmin = min(all_x)
            xmax = max(all_x)
            # give a small margin
            dx = max(1.0, xmax - xmin)
            for r in range(len(PAYLOADS)):
                for c in range(len(METRICS)):
                    try:
                        self.axes[r][c].set_xlim(max(0, xmax - max(self.max_points, dx)), xmax + 0.5)
                    except Exception:
                        pass

        # return artists updated
        artists = []
        for payload in PAYLOADS:
            artists.extend(self.lines[payload].values())
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
