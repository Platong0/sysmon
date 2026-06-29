#!/usr/bin/env python3
"""
sysmon — плавающий настраиваемый оверлей для macOS.

Каждая метрика — отдельный цветной блок. Набор, порядок и цвета блоков
настраиваются. Общая логика метрик — в metrics.py.

Управление:
  - перетаскивай окошко мышкой за любое место;
  - правый клик по окошку — меню (Настройки…, прозрачность, выход);
  - в окне настроек: галочка — показывать, ▲▼ — порядок, слева цвет блока.
"""

import time

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
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMenu,
    NSMenuItem,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
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

from metrics import (
    KEYS,
    LABELS,
    MetricsEngine,
    hex_to_rgb,
    load_config,
    save_config,
)


def ns_color(hexstr, alpha=1.0):
    r, g, b = hex_to_rgb(hexstr)
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


# -------------------------------------------------------------------- оверлей
# геометрия блоков
OUT = 8      # внешний отступ окна
GAP = 6      # промежуток между блоками
PADH = 10    # горизонтальный внутренний отступ
PADV = 6     # вертикальный внутренний отступ
BAR = 4      # ширина цветной полоски слева
BARGAP = 9   # отступ от полоски до текста


class OverlayView(NSView):
    def initWithController_(self, controller):
        self = self.initWithFrame_(NSMakeRect(0, 0, 200, 100))
        self.controller = controller
        self.items = []  # [{x,y,w,h, color:(r,g,b), lines:[...]}]
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

    def mouseDownCanMoveWindow(self):
        return True

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
        self._settings_win = None
        self._build_window()
        self._start_timers()
        return self

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
        win.setMovableByWindowBackground_(True)
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
        blocks = []
        for key in self.cfg["metrics"]:
            chunk = data.get(key)
            if chunk:
                blocks.append((key, chunk))
        if not blocks:
            blocks = [("clock", ["правый клик →", "Настройки"])]

        items = self._layout(blocks)
        self.view.setItems_(items)

    @objc.python_method
    def _layout(self, blocks):
        fs = self.cfg["font_size"]
        font = NSFont.monospacedSystemFontOfSize_weight_(fs, 0.0)
        attrs = {NSFontAttributeName: font}
        line_h = round(fs * 1.5)

        def text_w(s):
            return NSString.stringWithString_(s).sizeWithAttributes_(attrs).width

        content_w = 0
        for _, lines in blocks:
            for ln in lines:
                content_w = max(content_w, text_w(ln))
        block_w = PADH + BAR + BARGAP + int(content_w) + PADH

        items = []
        y = OUT
        for key, lines in blocks:
            h = PADV * 2 + len(lines) * line_h
            items.append(
                {
                    "x": OUT,
                    "y": y,
                    "w": block_w,
                    "h": h,
                    "color": hex_to_rgb(self.cfg["colors"].get(key, "#FFFFFF")),
                    "lines": lines,
                }
            )
            y += h + GAP

        win_w = OUT * 2 + block_w
        win_h = y - GAP + OUT

        old = self.window.frame()
        top = old.origin.y + old.size.height
        self.window.setFrame_display_(
            NSMakeRect(old.origin.x, top - win_h, win_w, win_h), True
        )
        return items

    # --------------------------------------------------------------- меню
    @objc.python_method
    def popUpMenu_(self, event):
        menu = NSMenu.alloc().init()

        def add(title, sel):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            item.setTarget_(self)
            menu.addItem_(item)

        add("Настройки…", b"openSettings:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Прозрачнее", b"lessOpaque:")
        add("Плотнее", b"moreOpaque:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Выход", b"quit:")
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self.view)

    def lessOpaque_(self, sender):
        self.cfg["opacity"] = max(0.2, round(self.cfg["opacity"] - 0.1, 2))
        self.window.setAlphaValue_(self.cfg["opacity"])
        save_config(self.cfg)

    def moreOpaque_(self, sender):
        self.cfg["opacity"] = min(1.0, round(self.cfg["opacity"] + 0.1, 2))
        self.window.setAlphaValue_(self.cfg["opacity"])
        save_config(self.cfg)

    def quit_(self, sender):
        self._save_position()
        NSApp().terminate_(self)

    @objc.python_method
    def _save_position(self):
        frame = self.window.frame()
        self.cfg["x"] = int(frame.origin.x)
        self.cfg["y"] = int(frame.origin.y)
        save_config(self.cfg)

    # ------------------------------------------------------------ настройки
    def openSettings_(self, sender):
        if self._settings_win is None:
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 380, 100),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
                NSBackingStoreBuffered,
                False,
            )
            win.setTitle_("Настройки sysmon")
            win.setReleasedWhenClosed_(False)
            win.setLevel_(NSScreenSaverWindowLevel)
            self._settings_win = win
        self._rebuild_settings_view()
        self._settings_win.center()
        NSApp().activateIgnoringOtherApps_(True)
        self._settings_win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _ordered_keys(self):
        enabled = list(self.cfg["metrics"])
        return enabled + [k for k in KEYS if k not in enabled]

    @objc.python_method
    def _rebuild_settings_view(self):
        win = self._settings_win
        row_h, top, bottom, width = 30, 50, 16, 380
        keys = self._ordered_keys()
        height = top + len(keys) * row_h + bottom

        win.setFrame_display_(
            NSMakeRect(win.frame().origin.x, win.frame().origin.y, width, height + 22),
            True,
        )
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        content.setFlipped_(True)
        win.setContentView_(content)

        header = NSTextField.alloc().initWithFrame_(NSMakeRect(16, 12, width - 32, 22))
        header.setStringValue_("Галочка — показывать.  ▲▼ — порядок.")
        header.setBezeled_(False)
        header.setDrawsBackground_(False)
        header.setEditable_(False)
        header.setSelectable_(False)
        content.addSubview_(header)

        enabled = self.cfg["metrics"]
        for i, key in enumerate(keys):
            y = top + i * row_h
            tag = KEYS.index(key)

            swatch = NSView.alloc().initWithFrame_(NSMakeRect(16, y + 6, 14, 14))
            swatch.setWantsLayer_(True)
            swatch.layer().setBackgroundColor_(
                ns_color(self.cfg["colors"].get(key, "#FFFFFF")).CGColor()
            )
            swatch.layer().setCornerRadius_(3.0)
            content.addSubview_(swatch)

            cb = NSButton.alloc().initWithFrame_(NSMakeRect(40, y + 3, 234, 22))
            cb.setButtonType_(NSButtonTypeSwitch)
            cb.setTitle_(LABELS[key])
            cb.setState_(1 if key in enabled else 0)
            cb.setTarget_(self)
            cb.setAction_(b"toggleMetric:")
            cb.setTag_(tag)
            content.addSubview_(cb)

            up = NSButton.alloc().initWithFrame_(NSMakeRect(282, y + 2, 44, 24))
            up.setTitle_("▲")
            up.setBezelStyle_(1)
            up.setTarget_(self)
            up.setAction_(b"moveUp:")
            up.setTag_(tag)
            up.setEnabled_(key in enabled)
            content.addSubview_(up)

            down = NSButton.alloc().initWithFrame_(NSMakeRect(328, y + 2, 44, 24))
            down.setTitle_("▼")
            down.setBezelStyle_(1)
            down.setTarget_(self)
            down.setAction_(b"moveDown:")
            down.setTag_(tag)
            down.setEnabled_(key in enabled)
            content.addSubview_(down)

    def toggleMetric_(self, sender):
        key = KEYS[sender.tag()]
        metrics = self.cfg["metrics"]
        if key in metrics:
            metrics.remove(key)
        else:
            metrics.append(key)
        save_config(self.cfg)
        self._rebuild_settings_view()

    def moveUp_(self, sender):
        self._move(KEYS[sender.tag()], -1)

    def moveDown_(self, sender):
        self._move(KEYS[sender.tag()], +1)

    @objc.python_method
    def _move(self, key, delta):
        metrics = self.cfg["metrics"]
        if key not in metrics:
            return
        i = metrics.index(key)
        j = i + delta
        if 0 <= j < len(metrics):
            metrics[i], metrics[j] = metrics[j], metrics[i]
            save_config(self.cfg)
            self._rebuild_settings_view()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    Monitor.alloc().init()
    app.run()


if __name__ == "__main__":
    main()
