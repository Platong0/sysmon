#!/usr/bin/env python3
"""
Общая (кросс-платформенная) часть sysmon: реестр метрик, движок подсчёта,
конфиг и цвета. Используется и macOS-версией (monitor.py), и Windows-версией
(monitor_win.py). Зависит только от стандартной библиотеки и psutil — никаких
платформенных GUI-импортов здесь быть не должно.
"""

import json
import os
import time

import psutil

# Конфиг храним в пользовательской папке (чтобы установленный пакет не писал
# в site-packages). Можно переопределить переменной окружения SYSMON_CONFIG.
_cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CONFIG_DIR = os.path.join(_cfg_home, "sysmon-overlay")
CONFIG_PATH = os.environ.get("SYSMON_CONFIG") or os.path.join(CONFIG_DIR, "config.json")

# Корень диска для disk_usage: '/' на macOS/Linux, 'C:\' на Windows.
ROOT_PATH = "/" if os.sep == "/" else "C:\\"

# Реестр метрик: ключ -> (подпись для настроек, цвет по умолчанию #RRGGBB).
# Порядок здесь — канонический (порядок по умолчанию).
AVAILABLE = [
    ("net", "Интернет (вниз/вверх)", "#34C759"),
    ("cpu", "Загрузка CPU", "#FF9F0A"),
    ("cpufreq", "Частота CPU", "#F472B6"),
    ("ram", "Память RAM (%)", "#0A84FF"),
    ("memused", "Память RAM (ГБ)", "#22D3EE"),
    ("swap", "Подкачка (swap)", "#BF5AF2"),
    ("disk", "Диск — занято %", "#5AC8FA"),
    ("diskfree", "Диск — свободно", "#A3E635"),
    ("diskio", "Диск — чтение/запись", "#7A78FF"),
    ("battery", "Батарея", "#30D158"),
    ("fps", "FPS окна / частота экрана", "#FF375F"),
    ("loadavg", "Load average", "#FF6482"),
    ("procs", "Число процессов", "#64D2FF"),
    ("uptime", "Время работы", "#66D4CF"),
    ("clock", "Часы", "#C8C8D2"),
]
KEYS = [k for k, _, _ in AVAILABLE]
LABELS = {k: lbl for k, lbl, _ in AVAILABLE}
DEFAULT_COLORS = {k: c for k, _, c in AVAILABLE}

# Описания метрик — для палитры (подсказка при наведении) и инспектора.
DESCRIPTIONS = {
    "net": "Скорость интернета: вниз — загрузка, вверх — отдача.",
    "cpu": "Насколько загружен процессор, в процентах.",
    "cpufreq": "Текущая тактовая частота процессора.",
    "ram": "Сколько оперативной памяти занято, в процентах.",
    "memused": "Сколько оперативной памяти занято, в гигабайтах.",
    "swap": "Файл подкачки: сколько данных ОС выгрузила из памяти на диск.",
    "disk": "Сколько занято на системном диске, в процентах.",
    "diskfree": "Сколько свободного места на системном диске.",
    "diskio": "Скорость чтения и записи диска.",
    "battery": "Заряд батареи; «+» означает, что идёт зарядка.",
    "fps": "Частота, с которой рисуется окно/экран (Гц) — обычно 60 или 120.",
    "loadavg": "Средняя нагрузка на систему за 1 минуту (только macOS/Linux).",
    "procs": "Сколько сейчас запущено процессов.",
    "uptime": "Сколько система работает без выключения.",
    "clock": "Текущее время.",
}

DEFAULT_METRICS = ["net", "cpu", "fps"]

DEFAULTS = {
    "metrics": DEFAULT_METRICS,
    "colors": DEFAULT_COLORS,
    "opacity": 0.9,
    "font_size": 14,
    "x": None,
    "y": None,
    "mode": "list",   # "list" — простой столбик; "board" — мозаика из плиток
    "layout": {},     # позиции плиток в режиме board: {key: [x, y]}
}


def hex_to_rgb(s):
    """'#RRGGBB' -> (r, g, b) в диапазоне 0..1."""
    s = s.lstrip("#")
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (1.0, 1.0, 1.0)


# ------------------------------------------------------------------ конфиг
def load_config():
    cfg = dict(DEFAULTS)
    cfg["colors"] = dict(DEFAULT_COLORS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}

    # миграция старого формата show_net/... -> metrics[]
    if "metrics" not in raw and any(k.startswith("show_") for k in raw):
        cfg["metrics"] = [k for k in KEYS if raw.get("show_" + k)] or list(
            DEFAULT_METRICS
        )

    for k in ("metrics", "opacity", "font_size", "x", "y", "mode", "layout"):
        if k in raw:
            cfg[k] = raw[k]
    if isinstance(raw.get("colors"), dict):
        cfg["colors"].update({k: v for k, v in raw["colors"].items() if k in KEYS})
    if not isinstance(cfg.get("layout"), dict):
        cfg["layout"] = {}
    if cfg.get("mode") not in ("list", "board"):
        cfg["mode"] = "list"

    cfg["metrics"] = [k for k in cfg["metrics"] if k in KEYS] or list(DEFAULT_METRICS)
    return cfg


def save_config(cfg):
    out = {
        "metrics": cfg["metrics"],
        "colors": cfg["colors"],
        "opacity": cfg["opacity"],
        "font_size": cfg["font_size"],
        "x": cfg["x"],
        "y": cfg["y"],
        "mode": cfg.get("mode", "list"),
        "layout": cfg.get("layout", {}),
    }
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# --------------------------------------------------------------- форматтеры
def human_speed(bytes_per_sec):
    bits = bytes_per_sec * 8
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mb/s"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} Kb/s"
    return f"{bits:.0f} b/s"


def human_bytes(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def human_uptime(secs):
    secs = int(secs)
    d, secs = divmod(secs, 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# --------------------------------------------------------------- движок метрик
class MetricsEngine:
    """Считает все метрики. Хранит прошлые счётчики для скоростей.

    Кросс-платформенно (psutil). FPS/частота экрана передаётся снаружи, так как
    меряется по-разному на каждой ОС (CADisplayLink на macOS, Win32 на Windows).
    """

    def __init__(self):
        self.last_net = psutil.net_io_counters()
        self.last_dio = psutil.disk_io_counters()
        self.last_t = time.monotonic()
        psutil.cpu_percent(interval=None)
        # числовые значения последней выборки — для графиков (None = не строится)
        self.values = {k: None for k in KEYS}

    def sample(self, fps):
        """Возвращает {ключ: [строки] | None}. Числа кладёт в self.values."""
        now = time.monotonic()
        dt = max(now - self.last_t, 1e-6)
        out = {}
        vals = {k: None for k in KEYS}

        try:
            net = psutil.net_io_counters()
            down = (net.bytes_recv - self.last_net.bytes_recv) / dt
            up = (net.bytes_sent - self.last_net.bytes_sent) / dt
            self.last_net = net
            out["net"] = [f"NET  v {human_speed(down)}", f"     ^ {human_speed(up)}"]
            vals["net"] = down
        except Exception:
            out["net"] = None

        cpu = psutil.cpu_percent(interval=None)
        out["cpu"] = [f"CPU  {cpu:.0f}%"]
        vals["cpu"] = cpu

        try:
            fr = psutil.cpu_freq()
            if fr and fr.current:
                if fr.current >= 1000:
                    out["cpufreq"] = [f"FRQ  {fr.current / 1000:.2f} GHz"]
                else:
                    out["cpufreq"] = [f"FRQ  {fr.current:.0f} MHz"]
                vals["cpufreq"] = fr.current
            else:
                out["cpufreq"] = None
        except Exception:
            out["cpufreq"] = None

        vm = psutil.virtual_memory()
        out["ram"] = [f"RAM  {vm.percent:.0f}%"]
        vals["ram"] = vm.percent
        out["memused"] = [f"MEM  {human_bytes(vm.used)}"]
        vals["memused"] = float(vm.used)

        sw = psutil.swap_memory()
        out["swap"] = [f"SWAP {human_bytes(sw.used)} ({sw.percent:.0f}%)"]
        vals["swap"] = sw.percent
        du = psutil.disk_usage(ROOT_PATH)
        out["disk"] = [f"DISK {du.percent:.0f}%"]
        vals["disk"] = du.percent
        out["diskfree"] = [f"FREE {human_bytes(du.free)}"]
        vals["diskfree"] = float(du.free)

        try:
            dio = psutil.disk_io_counters()
            r = (dio.read_bytes - self.last_dio.read_bytes) / dt
            w = (dio.write_bytes - self.last_dio.write_bytes) / dt
            self.last_dio = dio
            out["diskio"] = [f"IO   R {human_bytes(r)}/s W {human_bytes(w)}/s"]
            vals["diskio"] = r
        except Exception:
            out["diskio"] = None

        try:
            bat = psutil.sensors_battery()
            if bat is None:
                out["battery"] = None
            else:
                out["battery"] = [f"BAT  {bat.percent:.0f}%{' +' if bat.power_plugged else ''}"]
                vals["battery"] = bat.percent
        except Exception:
            out["battery"] = None

        out["fps"] = [f"FPS  {fps:.0f}"]
        vals["fps"] = fps

        try:
            la = os.getloadavg()[0]
            out["loadavg"] = [f"LOAD {la:.2f}"]
            vals["loadavg"] = la
        except (OSError, AttributeError):
            out["loadavg"] = None  # на Windows load average нет

        n = len(psutil.pids())
        out["procs"] = [f"PROC {n}"]
        vals["procs"] = float(n)
        out["uptime"] = [f"UP   {human_uptime(time.time() - psutil.boot_time())}"]
        out["clock"] = [f"TIME {time.strftime('%H:%M')}"]

        self.values = vals
        self.last_t = now
        return out
