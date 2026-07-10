#!/usr/bin/env python3
"""
sysmon — плавающий настраиваемый оверлей для macOS.

Два режима:
  • «Список» — блоки сами встают в столбик (просто, ничего не таскать).
  • «Мозаика» — плитки можно перетаскивать мышкой, они магнитно прилипают
    краями друг к другу (как LEGO). Раскладка сохраняется.

Управление:
  • тащи плитку — двигаешь плитку (в режиме «Мозаика»); тащи пустое место —
    двигаешь всё окно;
  • правый клик — меню (режим, настройки, прозрачность, выход).
Общая логика метрик — в metrics.py.
"""

import time
from collections import deque

import objc

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMenu,
    NSMenuItem,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import (
    NSMakePoint,
    NSMakeRect,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSString,
    NSTimer,
)

from .metrics import (
    KEYS,
    LABELS,
    MetricsEngine,
    hex_to_rgb,
    load_config,
    save_config,
)
from .editor_mac import Editor


def ns_color(hexstr, alpha=1.0):
    r, g, b = hex_to_rgb(hexstr)
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


# геометрия плиток
OUT = 8      # внешний отступ окна
GAP = 6      # промежуток между плитками (в режиме «Список» и при авто-раскладке)
PADH = 10    # горизонтальный внутренний отступ
PADV = 6     # вертикальный внутренний отступ
BAR = 4      # ширина цветной полоски слева
BARGAP = 9   # отступ от полоски до текста
SNAP = 16    # порог магнитного прилипания, пикселей


# -------------------------------------------------------------------- оверлей
class OverlayView(NSView):
    def initWithController_(self, controller):
        self = self.initWithFrame_(NSMakeRect(0, 0, 200, 100))
        self.controller = controller
        self.items = []  # [{key,x,y,w,h, color:(r,g,b), lines:[...]}]
        return self

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        cfg = self.controller.cfg
        font = NSFont.monospacedSystemFontOfSize_weight_(cfg["font_size"], 0.0)
        line_h = round(cfg["font_size"] * 1.5)

        for it in self.items:
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            r, g, b = it["color"]
            block = NSMakeRect(x, y, w, h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(block, 8, 8)

            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.62).set()
            path.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.16).set()
            path.fill()

            bar = NSMakeRect(x + PADH, y + PADV, BAR, h - 2 * PADV)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar, 2, 2).fill()

            attrs = {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, 1.0
                ),
            }
            tx = x + PADH + BAR + BARGAP
            for i, line in enumerate(it["lines"]):
                ns = NSString.stringWithString_(line)
                ns.drawAtPoint_withAttributes_(
                    NSMakePoint(tx, y + PADV + i * line_h), attrs
                )

    def setItems_(self, items):
        self.items = items
        self.setNeedsDisplay_(True)

    # --- мышь: перетаскивание плиток / окна ---
    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.controller.onMouseDown_at_(p.x, p.y)

    def mouseDragged_(self, event):
        self.controller.onMouseDragged()

    def mouseUp_(self, event):
        self.controller.onMouseUp()

    def rightMouseDown_(self, event):
        self.controller.popUpMenu_(event)

    def acceptsFirstMouse_(self, event):
        return True


# ----------------------------------------------------------------- контроллер
class Monitor(NSObject):
    def init(self):
        self = objc.super(Monitor, self).init()
        if self is None:
            return None
        self.cfg = load_config()
        save_config(self.cfg)
        self.engine = MetricsEngine()
        self._frame_count = 0
        self._fps = 0.0
        self._last_fps_t = time.monotonic()
        self._editor = None
        self._last_blocks = []
        self.history = {k: deque(maxlen=120) for k in KEYS}  # история значений для графиков
        self._tile_boxes = {}     # {key: (lx, ly, w, h)} в координатах холста (board)
        self._board_left = None   # экранная X левого края окна (board)
        self._board_top = None    # экранная Y верхнего края окна (board, ось вверх)
        self._prev_off = (OUT, OUT)
        self._drag = None
        self._build_window()
        self._build_statusbar()
        self._start_timers()
        return self

    @objc.python_method
    def _build_statusbar(self):
        # иконка в строке меню — чтобы управлять, даже когда оверлей скрыт
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        item.button().setTitle_("▦")
        item.button().setToolTip_("sysmon")
        menu = NSMenu.alloc().init()

        def add(title, sel):
            m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            m.setTarget_(self)
            menu.addItem_(m)

        add("Показать / скрыть оверлей", b"toggleOverlay:")
        add("Настройки…", b"openSettings:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Выход", b"quit:")
        item.setMenu_(menu)
        self.status_item = item

    def toggleOverlay_(self, sender):
        if self.window.isVisible():
            self.window.orderOut_(None)
        else:
            self.window.orderFront_(None)

    # ----------------------------------------------------------------- окно
    @objc.python_method
    def _build_window(self):
        screen = NSScreen.mainScreen().frame()
        w, h = 200, 100
        x, y = self.cfg["x"], self.cfg["y"]
        if x is None or y is None:
            x = screen.size.width - w - 30
            y = screen.size.height - h - 60

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setLevel_(NSScreenSaverWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
        )
        win.setHasShadow_(False)
        win.setAlphaValue_(self.cfg["opacity"])

        view = OverlayView.alloc().initWithController_(self)
        win.setContentView_(view)
        win.makeKeyAndOrderFront_(None)
        self.window = win
        self.view = view

        try:
            link = view.displayLinkWithTarget_selector_(self, b"frameTick:")
            link.addToRunLoop_forMode_(NSRunLoop.currentRunLoop(), NSRunLoopCommonModes)
            self._display_link = link
        except Exception:
            self._display_link = None

    @objc.python_method
    def _start_timers(self):
        self._timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, b"refresh:", None, True
            )
        )

    # -------------------------------------------------------------- метрики
    def frameTick_(self, link):
        self._frame_count += 1

    def refresh_(self, timer):
        now = time.monotonic()
        dt = max(now - self._last_fps_t, 1e-6)
        self._fps = self._frame_count / dt
        self._frame_count = 0
        self._last_fps_t = now

        data = self.engine.sample(self._fps)

        for k in KEYS:
            self.history[k].append(self.engine.values.get(k))
        blocks = []
        for key in self.cfg["metrics"]:
            chunk = data.get(key)
            if chunk:
                blocks.append((key, chunk))
        if not blocks:
            blocks = [("clock", ["правый клик →", "Настройки"])]
        self._last_blocks = blocks
        self._render()

    @objc.python_method
    def _render(self):
        # всегда мозаика; плитки двигаются только в редакторе, здесь — только показ
        items = self._layout_board(self._last_blocks)
        self.view.setItems_(items)

    # ---------- геометрия плитки ----------
    @objc.python_method
    def _tile_sizes(self, blocks):
        fs = self.cfg["font_size"]
        font = NSFont.monospacedSystemFontOfSize_weight_(fs, 0.0)
        attrs = {NSFontAttributeName: font}
        line_h = round(fs * 1.5)

        def text_w(s):
            return NSString.stringWithString_(s).sizeWithAttributes_(attrs).width

        sizes = {}
        for key, lines in blocks:
            cw = max((text_w(ln) for ln in lines), default=40)
            w = PADH + BAR + BARGAP + int(cw) + PADH
            h = PADV * 2 + len(lines) * line_h
            sizes[key] = (w, h)
        return sizes

    # ---------- режим «Список» ----------
    @objc.python_method
    def _layout_list(self, blocks):
        sizes = self._tile_sizes(blocks)
        block_w = max((sizes[k][0] for k, _ in blocks), default=150)

        items = []
        y = OUT
        for key, lines in blocks:
            h = sizes[key][1]
            items.append({
                "key": key, "x": OUT, "y": y, "w": block_w, "h": h,
                "color": hex_to_rgb(self.cfg["colors"].get(key, "#FFFFFF")),
                "lines": lines,
            })
            y += h + GAP

        win_w = OUT * 2 + block_w
        win_h = y - GAP + OUT
        old = self.window.frame()
        top = old.origin.y + old.size.height
        self.window.setFrame_display_(
            NSMakeRect(old.origin.x, top - win_h, win_w, win_h), True
        )
        return items

    # ---------- режим «Мозаика» ----------
    @objc.python_method
    def _layout_board(self, blocks):
        sizes = self._tile_sizes(blocks)
        layout = self.cfg["layout"]
        shown = [k for k, _ in blocks]

        # авто-позиции для плиток без сохранённой позиции — столбиком
        placed = [k for k in shown if k in layout]
        next_y = 0
        if placed:
            next_y = max(layout[k][1] + sizes[k][1] for k in placed) + GAP
        for key in shown:
            if key not in layout:
                layout[key] = [0, next_y]
                next_y += sizes[key][1] + GAP

        xs = [layout[k][0] for k in shown]
        ys = [layout[k][1] for k in shown]
        minx, miny = min(xs), min(ys)
        rights = [layout[k][0] + sizes[k][0] for k in shown]
        bottoms = [layout[k][1] + sizes[k][1] for k in shown]

        off = (-minx + OUT, -miny + OUT)
        win_w = (max(rights) - minx) + 2 * OUT
        win_h = (max(bottoms) - miny) + 2 * OUT

        if self._board_left is None:
            fr = self.window.frame()
            self._board_left = fr.origin.x
            self._board_top = fr.origin.y + fr.size.height
            self._prev_off = off

        # компенсируем сдвиг холста, чтобы неподвижные плитки не «прыгали»
        dox = off[0] - self._prev_off[0]
        doy = off[1] - self._prev_off[1]
        self._board_left -= dox
        self._board_top += doy
        self._prev_off = off

        self.window.setFrame_display_(
            NSMakeRect(self._board_left, self._board_top - win_h, win_w, win_h), True
        )

        items = []
        boxes = {}
        for key, lines in blocks:
            lx, ly = layout[key]
            w, h = sizes[key]
            boxes[key] = (lx, ly, w, h)
            items.append({
                "key": key, "x": lx + off[0], "y": ly + off[1], "w": w, "h": h,
                "color": hex_to_rgb(self.cfg["colors"].get(key, "#FFFFFF")),
                "lines": lines,
            })
        self._tile_boxes = boxes
        return items

    # ---------- перетаскивание ----------
    @objc.python_method
    def onMouseDown_at_(self, px, py):
        # на оверлее двигаем только всё окно; отдельные плитки — только в редакторе
        loc = NSEvent.mouseLocation()
        self._drag = {"type": "win", "lx": loc.x, "ly": loc.y}

    @objc.python_method
    def onMouseDragged(self):
        if not self._drag:
            return
        loc = NSEvent.mouseLocation()
        dx = loc.x - self._drag["lx"]
        dy = loc.y - self._drag["ly"]
        self._drag["lx"] = loc.x
        self._drag["ly"] = loc.y

        if self._drag["type"] == "win":
            o = self.window.frame().origin
            self.window.setFrameOrigin_(NSMakePoint(o.x + dx, o.y + dy))
            if self.cfg.get("mode") == "board" and self._board_left is not None:
                self._board_left += dx
                self._board_top += dy
        else:
            key = self._drag["key"]
            pos = self.cfg["layout"].get(key)
            if pos:
                pos[0] += dx      # экран X == холст X
                pos[1] += -dy     # экран Y вверх, холст Y вниз
                self._render()

    @objc.python_method
    def onMouseUp(self):
        if not self._drag:
            return
        if self._drag["type"] == "tile":
            self._snap(self._drag["key"])
            self._render()
        else:
            o = self.window.frame().origin
            self.cfg["x"] = int(o.x)
            self.cfg["y"] = int(o.y)
        save_config(self.cfg)
        self._drag = None

    @objc.python_method
    def _snap(self, key):
        """Магнитное прилипание: двигаем плитку к краям соседей."""
        boxes = self._tile_boxes
        if key not in boxes:
            return
        lx, ly, w, h = boxes[key]
        others = [(k, b) for k, b in boxes.items() if k != key]
        if not others:
            return

        # кандидаты по X: выровнять края или встать вплотную слева/справа
        cand_x, cand_y = [], []
        for _, (ox, oy, ow, oh) in others:
            cand_x += [ox, ox + ow - w, ox + ow, ox - w]
            cand_y += [oy, oy + oh - h, oy + oh, oy - h]

        best = min(cand_x, key=lambda c: abs(c - lx))
        if abs(best - lx) <= SNAP:
            lx = best
        best = min(cand_y, key=lambda c: abs(c - ly))
        if abs(best - ly) <= SNAP:
            ly = best

        self.cfg["layout"][key] = [lx, ly]

    # --------------------------------------------------------------- меню
    @objc.python_method
    def popUpMenu_(self, event):
        menu = NSMenu.alloc().init()

        def add(title, sel):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            item.setTarget_(self)
            menu.addItem_(item)

        add("Настройки…", b"openSettings:")
        add("Скрыть", b"hideOverlay:")
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self.view)

    def hideOverlay_(self, sender):
        # просто прячем оверлей; вернуть можно через иконку в строке меню (▦)
        self.window.orderOut_(None)

    @objc.python_method
    def show_overlay(self):
        self.window.orderFront_(None)

    def toggleMode_(self, sender):
        self.cfg["mode"] = "list" if self.cfg.get("mode") == "board" else "board"
        self._board_left = None  # переинициализировать привязку окна
        self._prev_off = (OUT, OUT)
        save_config(self.cfg)
        self._render()

    def lessOpaque_(self, sender):
        self.cfg["opacity"] = max(0.2, round(self.cfg["opacity"] - 0.1, 2))
        self.window.setAlphaValue_(self.cfg["opacity"])
        save_config(self.cfg)

    def moreOpaque_(self, sender):
        self.cfg["opacity"] = min(1.0, round(self.cfg["opacity"] + 0.1, 2))
        self.window.setAlphaValue_(self.cfg["opacity"])
        save_config(self.cfg)

    def quit_(self, sender):
        o = self.window.frame().origin
        self.cfg["x"] = int(o.x)
        self.cfg["y"] = int(o.y)
        save_config(self.cfg)
        NSApp().terminate_(self)

    # ------------------------------------------------------------ настройки
    def openSettings_(self, sender):
        if self._editor is None:
            self._editor = Editor.alloc().initWithMonitor_(self)
        self._editor.open()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    Monitor.alloc().init()
    app.run()


if __name__ == "__main__":
    main()
