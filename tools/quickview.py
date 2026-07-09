#!/usr/bin/env python3
"""
QuickView: terminal de control en vivo para los backups JSONL de cli.py --log-file.

Uso:
    python tools/quickview.py --log-file ground_dump.jsonl
    python tools/quickview.py -f ground_dump.jsonl --qnh 1015.7 --max-points 600

Formatos de linea JSON soportados:
 - {"pc_time": "...", "payload": "matorova", "data": {...}}   (envuelto)
 - {...}  con "payload_id" adentro                             (plano, formato cli.py)

Paneles (de arriba a abajo, eje X = tiempo local compartido):
  0. Franja de actuadores  -> luces testigo (verde=ON, rojo=OFF, gris=sin dato)
  1. Bateria (V)           -> con lineas de warning/cutoff
  2. Temperaturas (C)      -> CPU / sensor de presion / RH, por payload
  3. Presion (hPa)
  4. Altitud (m)           -> GPS (solido) vs derivada de presion via QNH (punteado)
  5. Humedad relativa (%)
  6. Flujo (L/min, eje izq) + Volumen muestreado acumulado (L, eje der, curva suave)
  7. RSSI (dBm)            -> calidad de enlace LoRa
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
# Configuracion general
# ---------------------------------------------------------------------------
PAYLOADS = ["matorova", "kenttarova"]

# Paleta "instrumento de vuelo": cian y ambar sobre fondo casi negro
PAYLOAD_COLORS = {
    "matorova": "#39C8E8",     # cian
    "kenttarova": "#F5A623",   # ambar
}

BAT_WARN_V = 19.8
BAT_CUTOFF_V = 18.6
TEMP_WARN_C = 45.0
TEMP_CRITICAL_C = 55.0

MIN_VALID_YEAR = 2024          # anio minimo para considerar rtc_time real (no default de fabrica)

ACTUATORS = [
    ("pump_front_state", "PUMP F"),
    ("pump_back_state", "PUMP B"),
    ("valve_state", "VALVE"),
]


def parse_args():
    p = argparse.ArgumentParser(description="QuickView: terminal de control en vivo para vertical_sampler")
    p.add_argument("--log-file", "-f", default="ground_dump.jsonl",
                   help="Archivo JSONL escrito por cli.py --log-file")
    p.add_argument("--max-points", type=int, default=300,
                   help="Puntos maximos por payload en la ventana deslizante")
    p.add_argument("--interval-ms", type=int, default=1000,
                   help="Intervalo de refresco (ms)")
    p.add_argument("--qnh", type=float, default=1013.25,
                   help="QNH del dia (hPa) para derivar altitud desde presion")
    p.add_argument("--theme", choices=["dark", "light"], default="dark",
                   help="Tema visual del dashboard")
    p.add_argument("--stale-after", type=float, default=90.0,
                   help="Segundos sin datos para marcar un payload/actuador como sin dato")
    return p.parse_args()


def apply_theme(theme):
    if theme == "dark":
        plt.style.use("dark_background")
        return {
            "bg": "#0a0e14",
            "panel": "#0f141c",
            "grid": "#22303c",
            "text": "#d7e2ea",
            "text_muted": "#7c8b96",
            "ok": "#3ddc84",
            "warn": "#f5c518",
            "crit": "#ff5c5c",
            "off": "#3a4550",
        }
    else:
        plt.style.use("default")
        return {
            "bg": "#f4f6f8",
            "panel": "#ffffff",
            "grid": "#c9d2d8",
            "text": "#1b232a",
            "text_muted": "#5a6570",
            "ok": "#2e9e5b",
            "warn": "#b8860b",
            "crit": "#c62828",
            "off": "#a9b3ba",
        }


def baro_altitude_m(pressure_hpa, qnh_hpa):
    """Altitud ISA estandar (m) a partir de presion y QNH de referencia."""
    if pressure_hpa is None or pressure_hpa <= 0 or qnh_hpa is None or qnh_hpa <= 0:
        return float("nan")
    return 44330.77 * (1.0 - (pressure_hpa / qnh_hpa) ** 0.1902632)


class QuickView:
    def __init__(self, log_file, max_points, stale_after, theme, qnh):
        self.log_file = log_file
        self.max_points = max_points
        self.stale_after = stale_after
        self.qnh = qnh
        self.palette = apply_theme(theme)
        self.file_pos = 0

        # Guardamos todo el historial del archivo (deques sin maxlen)
        self.timestamps = defaultdict(lambda: deque())
        self.series = defaultdict(lambda: defaultdict(lambda: deque()))
        self.last = {p: {} for p in PAYLOADS}
        self.last_seen_wall = {p: None for p in PAYLOADS}

        # volumen acumulado (litros), integracion trapezoidal continua (no se recorta con max_points)
        self._volume_l = {p: 0.0 for p in PAYLOADS}
        self._last_flow = {p: None for p in PAYLOADS}
        self._last_flow_time = {p: None for p in PAYLOADS}

        self._build_figure()
        self.read_all_existing_lines()
        
        self._anchor = {p: None for p in PAYLOADS}

    # ------------------------------------------------------------------
    # Figura y ejes
    # ------------------------------------------------------------------
    def _build_figure(self):
        pal = self.palette
        self.fig = plt.figure(figsize=(19, 15), facecolor=pal["bg"])
        try:
            self.fig.canvas.manager.set_window_title("QuickView - vertical_sampler")
        except Exception:
            pass

        gs = gridspec.GridSpec(
            8, 1, figure=self.fig,
            height_ratios=[0.55, 1, 1, 0.85, 1, 0.85, 1.1, 0.9],
            hspace=0.65,
            left=0.07, right=0.965, top=0.93, bottom=0.07,
        )

        self.ax_actuators = self.fig.add_subplot(gs[0])
        self.ax_batt = self.fig.add_subplot(gs[1])
        self.ax_temp = self.fig.add_subplot(gs[2], sharex=self.ax_batt)
        self.ax_press = self.fig.add_subplot(gs[3], sharex=self.ax_batt)
        self.ax_alt = self.fig.add_subplot(gs[4], sharex=self.ax_batt)
        self.ax_hum = self.fig.add_subplot(gs[5], sharex=self.ax_batt)
        self.ax_flow = self.fig.add_subplot(gs[6], sharex=self.ax_batt)
        self.ax_rssi = self.fig.add_subplot(gs[7], sharex=self.ax_batt)

        self.time_axes = [self.ax_batt, self.ax_temp, self.ax_press,
                           self.ax_alt, self.ax_hum, self.ax_flow, self.ax_rssi]

        self.ax_vol = self.ax_flow.twinx()

        for ax in self.time_axes + [self.ax_actuators]:
            ax.set_facecolor(pal["panel"])
            for spine in ax.spines.values():
                spine.set_color(pal["grid"])

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
        make(self.ax_rssi, "rssi")

        # -- franja de actuadores: circulos "luz testigo" --
        self.actuator_dots = {}
        self.actuator_labels_drawn = False
        n_act = len(ACTUATORS)
        n_pay = len(PAYLOADS)
        self.ax_actuators.set_xlim(0, 1)
        self.ax_actuators.set_ylim(0, 1)
        self.ax_actuators.axis("off")

        col_w = 1.0 / (n_act + 1)
        row_h = 1.0 / (n_pay + 0.6)
        for pi, payload in enumerate(PAYLOADS):
            y = 1.0 - (pi + 0.75) * row_h
            self.ax_actuators.text(
                0.01, y, payload, fontsize=12, fontweight="bold",
                color=PAYLOAD_COLORS[payload], va="center", ha="left",
                fontfamily="monospace", transform=self.ax_actuators.transAxes,
            )
            for ai, (key, label) in enumerate(ACTUATORS):
                x = col_w * (ai + 1.15)
                dot = self.ax_actuators.scatter(
                    [x], [y], s=260, color=self.palette["off"],
                    edgecolors=self.palette["text"], linewidths=0.8, zorder=3,
                    transform=self.ax_actuators.transAxes,
                )
                self.actuator_dots[(payload, key)] = dot
                if pi == 0:
                    self.ax_actuators.text(
                        x, 1.0 - 0.12, label, fontsize=9.5, color=self.palette["text_muted"],
                        va="center", ha="center", fontfamily="monospace",
                        transform=self.ax_actuators.transAxes,
                    )

    def _style_axes(self):
        pal = self.palette

        self.ax_batt.axhline(BAT_WARN_V, color=pal["warn"], linestyle=":", linewidth=1.1, alpha=0.8)
        self.ax_batt.axhline(BAT_CUTOFF_V, color=pal["crit"], linestyle=":", linewidth=1.1, alpha=0.9)
        self.ax_batt.text(0.005, 0.04, f"cutoff {BAT_CUTOFF_V}V", transform=self.ax_batt.transAxes,
                           fontsize=8.5, color=pal["crit"], fontfamily="monospace")
        self.ax_batt.text(0.005, 0.16, f"warn {BAT_WARN_V}V", transform=self.ax_batt.transAxes,
                           fontsize=8.5, color=pal["warn"], fontfamily="monospace")

        self.ax_temp.axhline(TEMP_WARN_C, color=pal["warn"], linestyle=":", linewidth=1.0, alpha=0.7)
        self.ax_temp.axhline(TEMP_CRITICAL_C, color=pal["crit"], linestyle=":", linewidth=1.0, alpha=0.8)

        titles = [
            (self.ax_batt, "BATERIA (V)"),
            (self.ax_temp, "TEMPERATURAS (C)  solido=CPU  guion=presion  punteado=RH"),
            (self.ax_press, "PRESION (hPa)"),
            (self.ax_alt, f"ALTITUD (m)  solido=GPS  guion=baro (QNH={self.qnh:.1f} hPa)"),
            (self.ax_hum, "HUMEDAD RELATIVA (%)"),
            (self.ax_flow, "FLUJO (L/min, eje izq)  +  VOLUMEN ACUMULADO (L, guion, eje der)"),
            (self.ax_rssi, "RSSI (dBm)"),
        ]
        for ax, title in titles:
            ax.set_title(title, loc="left", fontsize=10, color=pal["text_muted"],
                          fontfamily="monospace", pad=4)

        self.ax_actuators.set_title("ACTUADORES", loc="left", fontsize=10.5, fontweight="bold",
                                     color=pal["text"], fontfamily="monospace", pad=2)

        for ax in self.time_axes:
            ax.grid(True, alpha=0.35, color=pal["grid"], linewidth=0.7)
            ax.tick_params(axis="both", labelsize=9.5, colors=pal["text"])
            for lbl in ax.get_yticklabels():
                lbl.set_fontfamily("monospace")

        self.ax_vol.tick_params(axis="y", labelsize=9.5, colors=pal["text_muted"])
        for lbl in self.ax_vol.get_yticklabels():
            lbl.set_fontfamily("monospace")

        for ax in self.time_axes[:-1]:
            ax.tick_params(labelbottom=False)

        self.ax_rssi.set_xlabel("hora local", fontsize=10.5, color=pal["text"], fontfamily="monospace")
        self.date_formatter = mdates.DateFormatter("%H:%M:%S")
        self.ax_rssi.xaxis.set_major_formatter(self.date_formatter)
        for lbl in self.ax_rssi.get_xticklabels():
            lbl.set_rotation(25)
            lbl.set_ha("right")
            lbl.set_fontfamily("monospace")

        # leyenda global unica (colores por payload)
        legend_handles = [
            Line2D([0], [0], color=PAYLOAD_COLORS[p], linewidth=3, label=p.upper())
            for p in PAYLOADS
        ]
        self.fig.legend(
            handles=legend_handles, loc="upper right", fontsize=11.5, framealpha=0.25,
            ncols=2, bbox_to_anchor=(0.965, 0.975), prop={"family": "monospace", "weight": "bold"},
        )

        self.fig.suptitle(
            "QUICKVIEW  //  VERTICAL_SAMPLER  //  GROUND CONTROL",
            fontsize=17, fontweight="bold", color=pal["text"],
            fontfamily="monospace", y=0.985, x=0.07, ha="left",
        )

    # ------------------------------------------------------------------
    # Lectura y parseo del log
    # ------------------------------------------------------------------
    def read_all_existing_lines(self):
        """Lee TODAS las líneas del archivo al iniciar."""
        try:
            with open(self.log_file, "r") as f:
                lines = f.readlines()
                self.file_pos = f.tell()
                for raw in lines:
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
                    if dt_local is None:
                        continue  # sin referencia de tiempo confiable todavia, se descarta
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

                    # integracion trapezoidal continua de flujo -> volumen (L)
                    flow = d.get("flow")
                    if flow is not None:
                        prev_flow = self._last_flow[payload]
                        prev_t = self._last_flow_time[payload]
                        if prev_flow is not None and prev_t is not None:
                            dt_min = (dt_local - prev_t).total_seconds() / 60.0
                            if 0 < dt_min < 30:  # ignora huecos gigantes (payload caida, etc.)
                                self._volume_l[payload] += 0.5 * (flow + prev_flow) * dt_min
                        self._last_flow[payload] = flow
                        self._last_flow_time[payload] = dt_local
                    self.series["volume_l"][payload].append(self._volume_l[payload])

                    self.last[payload] = d
        except FileNotFoundError:
            pass

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

    def timestamp_for_entry(self, entry, d):
            """Devuelve datetime real o None si no se puede determinar con confianza."""
            rtc_str = d.get("rtc_time") if isinstance(d, dict) else None
            gps_time = d.get("gps_time") if isinstance(d, dict) else None
            payload = d.get("payload_id") if isinstance(d, dict) else entry.get("payload")
    
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
    
            # RTC invalido y sin pc_time: reconstruir usando el ancla + gps_time
            if payload in PAYLOADS and gps_time is not None and self._anchor[payload] is not None:
                anchor_gps, anchor_dt = self._anchor[payload]
                delta_s = gps_time - anchor_gps
                if abs(delta_s) < 6 * 3600:  # descarta anclas muy viejas/absurdas
                    return anchor_dt + timedelta(seconds=delta_s)
    
            # sin ancla todavia (antes del primer fix real): no graficar este punto
            return None

    def update_from_lines(self):
        now_wall = datetime.now().astimezone()
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
            if dt_local is None:
                continue  # sin referencia de tiempo confiable todavia, se descarta
            self.timestamps[payload].append(dt_local)
            self.last_seen_wall[payload] = now_wall

            for key in ("battery_voltage", "cpu_temperature", "pressure_sensor_temperature",
                        "rh_sensor_temperature", "pressure_sensor_pressure", "gps_altitude",
                        "rh_sensor_humidity", "flow", "rssi",
                        "pump_front_state", "pump_back_state", "valve_state"):
                val = d.get(key)
                self.series[key][payload].append(val if val is not None else float("nan"))

            baro_alt = baro_altitude_m(d.get("pressure_sensor_pressure"), self.qnh)
            self.series["baro_altitude"][payload].append(baro_alt)

            # integracion trapezoidal continua de flujo -> volumen (L)
            flow = d.get("flow")
            if flow is not None:
                prev_flow = self._last_flow[payload]
                prev_t = self._last_flow_time[payload]
                if prev_flow is not None and prev_t is not None:
                    dt_min = (dt_local - prev_t).total_seconds() / 60.0
                    if 0 < dt_min < 30:  # ignora huecos gigantes (payload caida, etc.)
                        self._volume_l[payload] += 0.5 * (flow + prev_flow) * dt_min
                self._last_flow[payload] = flow
                self._last_flow_time[payload] = dt_local
            self.series["volume_l"][payload].append(self._volume_l[payload])

            self.last[payload] = d

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def _set_line_data(self, ax_key, payload):
        xs = mdates.date2num(list(self.timestamps[payload])) if self.timestamps[payload] else []
        ys = list(self.series[ax_key][payload])
        self.lines[ax_key][payload].set_data(xs, ys)
        return xs

    def _update_actuator_lights(self):
        pal = self.palette
        now = datetime.now().astimezone()
        for payload in PAYLOADS:
            last = self.last.get(payload, {})
            last_seen = self.last_seen_wall.get(payload)
            stale = (last_seen is None) or ((now - last_seen).total_seconds() > self.stale_after)
            for key, _label in ACTUATORS:
                dot = self.actuator_dots[(payload, key)]
                if stale:
                    color = pal["off"]
                else:
                    val = last.get(key)
                    color = pal["ok"] if val == 1 else (pal["crit"] if val == 0 else pal["off"])
                dot.set_color(color)

    def update_plot(self, _frame):
        self.update_from_lines()

        all_xnums = []
        for payload in PAYLOADS:
            xs = self._set_line_data("battery_voltage", payload)
            if len(xs):
                all_xnums.extend(xs)
            for key in ("cpu_temperature", "pressure_sensor_temperature", "rh_sensor_temperature",
                        "pressure_sensor_pressure", "gps_altitude", "baro_altitude",
                        "rh_sensor_humidity", "flow", "volume_l", "rssi"):
                self._set_line_data(key, payload)

        if all_xnums:
            xmin, xmax = min(all_xnums), max(all_xnums)
            span = max(1 / 86400.0, xmax - xmin)
            self.ax_batt.set_xlim(xmin - span * 0.02, xmax + span * 0.05)

        for ax in self.time_axes + [self.ax_vol]:
            ax.relim()
            ax.autoscale_view(scalex=False)

        self._update_actuator_lights()

        artists = []
        for m in self.lines.values():
            artists.extend(m.values())
        artists.extend(self.actuator_dots.values())
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
