#!/usr/bin/env python3
"""
sysmon для Windows — плавающий настраиваемый оверлей на tkinter.

Тот же набор метрик и тот же config.json, что и в macOS-версии (общий код —
в metrics.py). Из зависимостей нужен только psutil; tkinter входит в Python.

Управление:
  - перетаскивай окошко мышкой;
  - правый клик — меню (Настройки…, прозрачность, выход);
  - в окне настроек: галочка — показывать, ▲▼ — порядок, слева цвет блока.
"""

import platform
import tkinter as tk
import tkinter.font as tkfont

from .metrics import (
    KEYS,
    LABELS,
    MetricsEngine,
    load_config,
    save_config,
)

IS_WINDOWS = platform.system() == "Windows"
TRANSPARENT_KEY = "#010101"  # этот цвет окна станет полностью прозрачным (Windows)

# геометрия блоков
OUT = 8
GAP = 6
PADH = 10
PADV = 5
BAR = 4
BARGAP = 9


def screen_hz():
    """Частота обновления экрана (Гц) через Win32. Вне Windows — 0."""
    if not IS_WINDOWS:
        return 0.0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        VREFRESH = 116
        hz = gdi32.GetDeviceCaps(hdc, VREFRESH)
        user32.ReleaseDC(0, hdc)
        return float(hz) if hz and hz > 1 else 0.0
    except Exception:
        return 0.0


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Скруглённый прямоугольник на Canvas."""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Overlay:
    def __init__(self):
        self.cfg = load_config()
        save_config(self.cfg)
        self.engine = MetricsEngine()
        self._drag = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)  # без рамки
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", float(self.cfg["opacity"]))
        except tk.TclError:
            pass

        bg = TRANSPARENT_KEY if IS_WINDOWS else "#101014"
        self.root.configure(bg=bg)
        if IS_WINDOWS:
            try:
                self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            except tk.TclError:
                pass

        self.canvas = tk.Canvas(
            self.root, bg=bg, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        # позиция
        x = self.cfg["x"] if self.cfg["x"] is not None else 60
        y = self.cfg["y"] if self.cfg["y"] is not None else 60
        self.root.geometry(f"+{int(x)}+{int(y)}")

        # перетаскивание
        for w in (self.root, self.canvas):
            w.bind("<Button-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Button-3>", self._on_menu)  # правый клик
            w.bind("<Button-2>", self._on_menu)  # на всякий случай

        self._settings = None
        self._tick()

    # ----------------------------------------------------------- отрисовка
    def _font(self):
        return tkfont.Font(family="Consolas", size=int(self.cfg["font_size"]))

    def _tick(self):
        fps = screen_hz()
        data = self.engine.sample(fps)
        blocks = []
        for key in self.cfg["metrics"]:
            chunk = data.get(key)
            if chunk:
                blocks.append((key, chunk))
        if not blocks:
            blocks = [("clock", ["ПКМ →", "Настройки"])]
        self._draw(blocks)
        self.root.after(1000, self._tick)

    def _draw(self, blocks):
        c = self.canvas
        c.delete("all")
        font = self._font()
        line_h = int(self.cfg["font_size"] * 1.6)

        content_w = 0
        for _, lines in blocks:
            for ln in lines:
                content_w = max(content_w, font.measure(ln))
        block_w = PADH + BAR + BARGAP + content_w + PADH

        y = OUT
        max_right = 0
        for key, lines in blocks:
            h = PADV * 2 + len(lines) * line_h
            x1, y1, x2, y2 = OUT, y, OUT + block_w, y + h
            color = self.cfg["colors"].get(key, "#FFFFFF")

            round_rect(c, x1, y1, x2, y2, 8, fill="#15151A", outline="")
            c.create_rectangle(
                x1 + PADH, y1 + PADV, x1 + PADH + BAR, y2 - PADV,
                fill=color, outline="",
            )
            tx = x1 + PADH + BAR + BARGAP
            for i, line in enumerate(lines):
                c.create_text(
                    tx, y1 + PADV + i * line_h,
                    text=line, anchor="nw", fill=color, font=font,
                )
            max_right = max(max_right, x2)
            y = y2 + GAP

        win_w = max_right + OUT
        win_h = y - GAP + OUT
        self.root.geometry(f"{int(win_w)}x{int(win_h)}")

    # ----------------------------------------------------------- мышь
    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        sx, sy, ox, oy = self._drag
        self.root.geometry(f"+{ox + e.x_root - sx}+{oy + e.y_root - sy}")

    def _on_release(self, e):
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        save_config(self.cfg)
        self._drag = None

    # ----------------------------------------------------------- меню
    def _on_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Настройки…", command=self._open_settings)
        m.add_separator()
        m.add_command(label="Прозрачнее", command=lambda: self._opacity(-0.1))
        m.add_command(label="Плотнее", command=lambda: self._opacity(+0.1))
        m.add_separator()
        m.add_command(label="Выход", command=self._quit)
        m.tk_popup(e.x_root, e.y_root)

    def _opacity(self, d):
        self.cfg["opacity"] = max(0.2, min(1.0, round(self.cfg["opacity"] + d, 2)))
        try:
            self.root.attributes("-alpha", float(self.cfg["opacity"]))
        except tk.TclError:
            pass
        save_config(self.cfg)

    def _quit(self):
        save_config(self.cfg)
        self.root.destroy()

    # ----------------------------------------------------------- настройки
    def _open_settings(self):
        if self._settings and tk.Toplevel.winfo_exists(self._settings):
            self._settings.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Настройки sysmon")
        win.attributes("-topmost", True)
        self._settings = win
        self._build_settings()

    def _ordered_keys(self):
        enabled = list(self.cfg["metrics"])
        return enabled + [k for k in KEYS if k not in enabled]

    def _build_settings(self):
        win = self._settings
        for child in win.winfo_children():
            child.destroy()

        tk.Label(
            win, text="Галочка — показывать.  ▲▼ — порядок.", anchor="w"
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 6))

        enabled = self.cfg["metrics"]
        for i, key in enumerate(self._ordered_keys(), start=1):
            color = self.cfg["colors"].get(key, "#FFFFFF")
            tk.Label(win, bg=color, width=2, relief="solid", bd=1).grid(
                row=i, column=0, padx=(10, 6), pady=2
            )

            var = tk.IntVar(value=1 if key in enabled else 0)
            tk.Checkbutton(
                win, text=LABELS[key], variable=var, anchor="w", width=22,
                command=lambda k=key: self._toggle(k),
            ).grid(row=i, column=1, sticky="w")

            state = "normal" if key in enabled else "disabled"
            tk.Button(
                win, text="▲", width=3, state=state,
                command=lambda k=key: self._move(k, -1),
            ).grid(row=i, column=2, padx=2)
            tk.Button(
                win, text="▼", width=3, state=state,
                command=lambda k=key: self._move(k, +1),
            ).grid(row=i, column=3, padx=(2, 10))

    def _toggle(self, key):
        metrics = self.cfg["metrics"]
        if key in metrics:
            metrics.remove(key)
        else:
            metrics.append(key)
        save_config(self.cfg)
        self._build_settings()

    def _move(self, key, delta):
        metrics = self.cfg["metrics"]
        if key not in metrics:
            return
        i = metrics.index(key)
        j = i + delta
        if 0 <= j < len(metrics):
            metrics[i], metrics[j] = metrics[j], metrics[i]
            save_config(self.cfg)
            self._build_settings()

    def run(self):
        self.root.mainloop()


def main():
    Overlay().run()


if __name__ == "__main__":
    main()
