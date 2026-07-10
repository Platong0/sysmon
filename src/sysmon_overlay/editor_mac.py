#!/usr/bin/env python3
"""
Редактор мозаики для macOS-версии sysmon (версия 2).

Одно окно, три зоны:
  • ХОЛСТ (слева-сверху) — вся мозаика: двигаешь плитки (магнитно прилипают),
    кликом выбираешь, Delete/кнопкой убираешь;
  • ИНСПЕКТОР (справа-сверху) — про выбранный блок: описание, текущее значение,
    выбор цвета (настоящий color picker), кнопка «Убрать»;
  • ПАЛИТРА (снизу) — все блоки + поиск; наведи — описание, клик — добавить/убрать.

Плюс общие настройки вида: прозрачность, размер шрифта, «показывать мозаикой».
Изменения сразу видны на живом оверлее (mon._render / refresh).
"""

import objc

from AppKit import (
    NSAffineTransform,
    NSApp,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSColorSpace,
    NSColorWell,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSMakeRect,
    NSScreenSaverWindowLevel,
    NSScrollView,
    NSSearchField,
    NSSlider,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenPrimary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakePoint, NSMakeSize, NSObject, NSString, NSTimer

from .metrics import DESCRIPTIONS, KEYS, LABELS, hex_to_rgb, human_bytes, save_config


def _fmt_val(v):
    if abs(v) >= 1024:
        return human_bytes(v) + "/s"
    return f"{v:.0f}"

# геометрия плитки (как в оверлее)
PADH, PADV, BAR, BARGAP, SNAP = 10, 6, 4, 9, 16
CANVAS_PAD = 16


def nscolor_to_hex(c):
    c2 = c.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) or c
    r = int(round(c2.redComponent() * 255))
    g = int(round(c2.greenComponent() * 255))
    b = int(round(c2.blueComponent() * 255))
    return "#%02X%02X%02X" % (r, g, b)


def ns_color(hexstr):
    r, g, b = hex_to_rgb(hexstr)
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


def _label(frame, text, size=12, bold=False):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    f = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    tf.setFont_(f)
    return tf


# -------------------------------------------------------------------- холст
class CanvasView(NSView):
    def initWithEditor_(self, editor):
        self = self.initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.editor = editor
        self._drag = None
        self._boxes = {}
        self._scale = 1.0
        return self

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        ed = self.editor
        cfg = ed.mon.cfg
        NSColor.colorWithCalibratedWhite_alpha_(0.13, 1.0).set()
        NSBezierPath.fillRect_(self.bounds())

        blocks = ed._blocks()
        self._boxes = {}
        self._scale = 1.0
        if not blocks:
            return
        sizes = ed.mon._tile_sizes(blocks)
        ed._ensure_positions(blocks, sizes)
        layout = cfg["layout"]
        shown = [k for k, _ in blocks]
        minx = min(layout[k][0] for k in shown)
        miny = min(layout[k][1] for k in shown)
        maxx = max(layout[k][0] + sizes[k][0] for k in shown)
        maxy = max(layout[k][1] + sizes[k][1] for k in shown)
        cw, ch = max(maxx - minx, 1), max(maxy - miny, 1)

        b = self.bounds()
        availw = b.size.width - 2 * CANVAS_PAD
        availh = b.size.height - 2 * CANVAS_PAD
        scale = min(1.0, availw / cw, availh / ch)
        self._scale = scale
        ox = CANVAS_PAD + (availw - cw * scale) / 2.0
        oy = CANVAS_PAD + (availh - ch * scale) / 2.0

        # экранные прямоугольники плиток (для попадания мышью)
        for key, _ in blocks:
            lx, ly = layout[key]
            w, h = sizes[key]
            self._boxes[key] = (
                ox + (lx - minx) * scale, oy + (ly - miny) * scale, w * scale, h * scale
            )

        # рисуем всю мозаику со скейлом
        gc = NSGraphicsContext.currentContext()
        gc.saveGraphicsState()
        t = NSAffineTransform.transform()
        t.translateXBy_yBy_(ox, oy)
        t.scaleBy_(scale)
        t.translateXBy_yBy_(-minx, -miny)
        t.concat()

        font = NSFont.monospacedSystemFontOfSize_weight_(cfg["font_size"], 0.0)
        line_h = round(cfg["font_size"] * 1.5)
        for key, lines in blocks:
            lx, ly = layout[key]
            w, h = sizes[key]
            r, g, b = hex_to_rgb(cfg["colors"].get(key, "#FFFFFF"))

            block = NSMakeRect(lx, ly, w, h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(block, 8, 8)
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.55).set()
            path.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.18).set()
            path.fill()

            barrect = NSMakeRect(lx + PADH, ly + PADV, BAR, h - 2 * PADV)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(barrect, 2, 2).fill()

            attrs = {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, 1.0
                ),
            }
            tx = lx + PADH + BAR + BARGAP
            for i, line in enumerate(lines):
                NSString.stringWithString_(line).drawAtPoint_withAttributes_(
                    NSMakePoint(tx, ly + PADV + i * line_h), attrs
                )

            if key == ed.sel:
                ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(lx - 2, ly - 2, w + 4, h + 4), 10, 10
                )
                NSColor.whiteColor().set()
                ring.setLineWidth_(2.0)
                ring.stroke()

        gc.restoreGraphicsState()

    @objc.python_method
    def _hit(self, p):
        for k, (x, y, w, h) in self._boxes.items():
            if x <= p.x <= x + w and y <= p.y <= y + h:
                return k
        return None

    def mouseDown_(self, e):
        p = self.convertPoint_fromView_(e.locationInWindow(), None)
        key = self._hit(p)
        self.editor.select_(key)
        self._drag = {"key": key, "lx": p.x, "ly": p.y} if key else None

    def mouseDragged_(self, e):
        if not self._drag:
            return
        p = self.convertPoint_fromView_(e.locationInWindow(), None)
        s = self._scale or 1.0
        dx, dy = (p.x - self._drag["lx"]) / s, (p.y - self._drag["ly"]) / s
        self._drag["lx"], self._drag["ly"] = p.x, p.y
        pos = self.editor.mon.cfg["layout"].get(self._drag["key"])
        if pos:
            pos[0] += dx
            pos[1] += dy
        self.setNeedsDisplay_(True)
        self.editor._push_overlay()

    def mouseUp_(self, e):
        if self._drag:
            self.editor._snap(self._drag["key"])
            self._drag = None
            self.setNeedsDisplay_(True)
            self.editor._push_overlay(save=True)


# ------------------------------------------------------------- карточка палитры
class PaletteCard(NSView):
    def initWithEditor_key_(self, editor, key):
        self = self.initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.editor = editor
        self.key = key
        self.setToolTip_(DESCRIPTIONS.get(key, ""))
        return self

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        cfg = self.editor.mon.cfg
        added = self.key in cfg["metrics"]
        r, g, b = hex_to_rgb(cfg["colors"].get(self.key, "#FFFFFF"))
        bnds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(1, 1, bnds.size.width - 2, bnds.size.height - 2), 8, 8
        )
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.5 if added else 0.22).set()
        path.fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            r, g, b, 0.95 if added else 0.35
        ).set()
        path.setLineWidth_(2.0 if added else 1.0)
        path.stroke()

        bar = NSMakeRect(8, 8, BAR, bnds.size.height - 16)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar, 2, 2).fill()

        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(12),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        mark = "✓ " if added else "+ "
        NSString.stringWithString_(mark + LABELS[self.key]).drawAtPoint_withAttributes_(
            NSMakePoint(20, 8), attrs
        )

    def mouseDown_(self, e):
        self.editor.paletteToggle(self.key)


# ---------------------------------------------------------------- мини-график
class GraphView(NSView):
    def initWithEditor_(self, editor):
        self = self.initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.editor = editor
        return self

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        ed = self.editor
        key = ed.sel
        bnds = self.bounds()
        frame_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, bnds.size.width - 1, bnds.size.height - 1), 6, 6
        )
        NSColor.colorWithCalibratedWhite_alpha_(0.16, 1.0).set()
        frame_path.fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.35, 1.0).set()
        frame_path.setLineWidth_(1.0)
        frame_path.stroke()
        if not key:
            return

        raw = list(ed.mon.history.get(key, []))
        data = [v for v in raw if v is not None]
        r, g, b = hex_to_rgb(ed.mon.cfg["colors"].get(key, "#FFFFFF"))

        def note(text):
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(11),
                NSForegroundColorAttributeName: NSColor.grayColor(),
            }
            NSString.stringWithString_(text).drawAtPoint_withAttributes_(
                NSMakePoint(8, bnds.size.height / 2 - 8), attrs
            )

        if not data:
            note("график недоступен")
            return
        if len(data) < 2:
            note("копим данные…")
            return

        lo, hi = min(data), max(data)
        if hi - lo < 1e-9:
            hi = lo + 1.0
        pad = 8
        w = bnds.size.width - 2 * pad
        h = bnds.size.height - 2 * pad - 4
        n = len(data)

        def px(i):
            return pad + w * i / (n - 1)

        def py(v):
            return pad + h * (1 - (v - lo) / (hi - lo))

        base = pad + h
        fill = NSBezierPath.bezierPath()
        fill.moveToPoint_(NSMakePoint(px(0), base))
        for i in range(n):
            fill.lineToPoint_(NSMakePoint(px(i), py(data[i])))
        fill.lineToPoint_(NSMakePoint(px(n - 1), base))
        fill.closePath()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.18).set()
        fill.fill()

        line = NSBezierPath.bezierPath()
        line.moveToPoint_(NSMakePoint(px(0), py(data[0])))
        for i in range(1, n):
            line.lineToPoint_(NSMakePoint(px(i), py(data[i])))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        line.setLineWidth_(1.6)
        line.stroke()

        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: NSColor.grayColor(),
        }
        NSString.stringWithString_(_fmt_val(hi)).drawAtPoint_withAttributes_(
            NSMakePoint(3, 1), attrs
        )
        NSString.stringWithString_(_fmt_val(lo)).drawAtPoint_withAttributes_(
            NSMakePoint(3, bnds.size.height - 13), attrs
        )


# ----------------------------------------------------------------- редактор
class Editor(NSObject):
    def initWithMonitor_(self, mon):
        self = objc.super(Editor, self).init()
        if self is None:
            return None
        self.mon = mon
        self.sel = None
        self.win = None
        self.color_well = None
        self.value_label = None
        self.graph = None
        self._timer = None
        self.insp_open = True
        self._build()
        return self

    # ------------------------------------------------------------ построение
    @objc.python_method
    def _build(self):
        W, H = 720, 680
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("sysmon — редактор мозаики")
        win.setReleasedWhenClosed_(False)
        win.setLevel_(NSScreenSaverWindowLevel)
        win.setDelegate_(self)
        win.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)  # кнопка полноэкранного
        win.setContentMinSize_(NSMakeSize(560, 480))
        self.win = win

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        root.setFlipped_(True)
        win.setContentView_(root)
        self.root = root

        def button(title, sel, tip=""):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
            b.setTitle_(title)
            b.setBezelStyle_(1)
            b.setTarget_(self)
            b.setAction_(sel)
            if tip:
                b.setToolTip_(tip)
            root.addSubview_(b)
            return b

        # верхний ряд
        self.canvas_title = _label(NSMakeRect(0, 0, 70, 18), "Холст", 12, True)
        root.addSubview_(self.canvas_title)
        self.arrange_btn = button("Разложить заново", b"arrange:", "Сложить блоки столбиком")
        self.insp_btn = button("Скрыть инспектор", b"toggleInspector:")

        # холст и инспектор
        self.canvas = CanvasView.alloc().initWithEditor_(self)
        root.addSubview_(self.canvas)
        self.insp = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.insp.setFlipped_(True)
        root.addSubview_(self.insp)

        # общие настройки вида
        self.opacity_lbl = _label(NSMakeRect(0, 0, 84, 18), "Прозрачность", 11)
        root.addSubview_(self.opacity_lbl)
        sl = NSSlider.alloc().initWithFrame_(NSMakeRect(0, 0, 150, 22))
        sl.setMinValue_(0.2)
        sl.setMaxValue_(1.0)
        sl.setDoubleValue_(self.mon.cfg["opacity"])
        sl.setContinuous_(True)
        sl.setTarget_(self)
        sl.setAction_(b"opacityChanged:")
        self.opacity_sl = sl
        root.addSubview_(sl)
        self.font_lbl = _label(NSMakeRect(0, 0, 46, 18), "Шрифт", 11)
        root.addSubview_(self.font_lbl)
        self.font_minus = button("A-", b"fontMinus:")
        self.font_plus = button("A+", b"fontPlus:")

        # палитра
        self.pal_title = _label(NSMakeRect(0, 0, 130, 18), "Палитра — все блоки", 12, True)
        root.addSubview_(self.pal_title)
        search = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, 280, 24))
        search.setPlaceholderString_("поиск блока…")
        search.setDelegate_(self)
        self.search = search
        root.addSubview_(search)
        self.pal_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 636, 140))
        self.pal_scroll.setHasVerticalScroller_(True)
        self.pal_scroll.setDrawsBackground_(False)
        root.addSubview_(self.pal_scroll)

        # нижний ряд
        self.show_btn = button("Показать оверлей", b"showOverlay:")
        self.quit_btn = button("Выход", b"quitApp:")

        self._relayout()
        self._rebuild_inspector()

    # ------------------------------------------------------------ раскладка
    @objc.python_method
    def _relayout(self):
        b = self.root.frame() if self.root else None
        if b is None:
            return
        W, H = b.size.width, b.size.height
        M = 12

        # верхний ряд
        self.canvas_title.setFrame_(NSMakeRect(M, 6, 70, 18))
        self.arrange_btn.setFrame_(NSMakeRect(90, 2, 170, 24))
        self.insp_btn.setTitle_("Скрыть инспектор" if self.insp_open else "Показать инспектор")
        self.insp_btn.setFrame_(NSMakeRect(W - M - 170, 2, 170, 24))

        # нижние ряды (снизу вверх)
        bottom_y = H - 6 - 30
        self.show_btn.setFrame_(NSMakeRect(M, bottom_y, 180, 30))
        self.quit_btn.setFrame_(NSMakeRect(W - M - 128, bottom_y, 128, 30))

        pal_scroll_h = 150
        scroll_y = bottom_y - 10 - pal_scroll_h
        self.pal_title.setFrame_(NSMakeRect(M, scroll_y - 28, 130, 18))
        self.search.setFrame_(NSMakeRect(M + 150, scroll_y - 30, 280, 24))
        self.pal_scroll.setFrame_(NSMakeRect(M, scroll_y, W - 2 * M, pal_scroll_h))

        ctrl_y = scroll_y - 30 - 30
        self.opacity_lbl.setFrame_(NSMakeRect(M, ctrl_y + 2, 84, 18))
        self.opacity_sl.setFrame_(NSMakeRect(M + 96, ctrl_y, 150, 22))
        self.font_lbl.setFrame_(NSMakeRect(M + 258, ctrl_y + 2, 46, 18))
        self.font_minus.setFrame_(NSMakeRect(M + 304, ctrl_y - 2, 36, 26))
        self.font_plus.setFrame_(NSMakeRect(M + 344, ctrl_y - 2, 36, 26))

        # холст + инспектор занимают середину
        zone_top = 30
        zone_h = ctrl_y - 8 - zone_top
        if self.insp_open:
            insp_w = 236
            canvas_w = W - 2 * M - insp_w - 12
            self.insp.setHidden_(False)
            self.insp.setFrame_(NSMakeRect(M + canvas_w + 12, zone_top, insp_w, zone_h))
        else:
            canvas_w = W - 2 * M
            self.insp.setHidden_(True)
        self.canvas.setFrame_(NSMakeRect(M, zone_top, canvas_w, zone_h))
        self.canvas.setNeedsDisplay_(True)

        self._rebuild_palette()
        if self.insp_open:
            self._rebuild_inspector()  # чтобы график тянулся под высоту окна

    def windowDidResize_(self, notification):
        self._relayout()

    def toggleInspector_(self, sender):
        self.insp_open = not self.insp_open
        self._relayout()

    # ------------------------------------------------------------ данные
    @objc.python_method
    def _blocks(self):
        return list(self.mon._last_blocks)

    @objc.python_method
    def _ensure_positions(self, blocks, sizes):
        layout = self.mon.cfg["layout"]
        shown = [k for k, _ in blocks]
        placed = [k for k in shown if k in layout]
        next_y = 0
        if placed:
            next_y = max(layout[k][1] + sizes[k][1] for k in placed) + 6
        for key in shown:
            if key not in layout:
                layout[key] = [0, next_y]
                next_y += sizes[key][1] + 6

    @objc.python_method
    def _snap(self, key):
        blocks = self._blocks()
        sizes = self.mon._tile_sizes(blocks)
        layout = self.mon.cfg["layout"]
        if key not in layout or key not in sizes:
            return
        lx, ly = layout[key]
        w, h = sizes[key]
        others = [k for k, _ in blocks if k != key and k in layout]
        if not others:
            return
        cand_x, cand_y = [], []
        for k in others:
            ox, oy = layout[k]
            ow, oh = sizes[k]
            cand_x += [ox, ox + ow - w, ox + ow, ox - w]
            cand_y += [oy, oy + oh - h, oy + oh, oy - h]
        bx = min(cand_x, key=lambda c: abs(c - lx))
        if abs(bx - lx) <= SNAP:
            lx = bx
        by = min(cand_y, key=lambda c: abs(c - ly))
        if abs(by - ly) <= SNAP:
            ly = by
        layout[key] = [lx, ly]

    # ------------------------------------------------------------ обновления
    @objc.python_method
    def _push_overlay(self, save=False):
        self.mon._render()
        if save:
            save_config(self.mon.cfg)

    @objc.python_method
    def _refresh_all(self):
        self.mon.refresh_(None)  # пересобрать _last_blocks под текущий набор
        self.canvas.setNeedsDisplay_(True)
        self._rebuild_palette()
        self._rebuild_inspector()

    @objc.python_method
    def select_(self, key):
        self.sel = key
        self.canvas.setNeedsDisplay_(True)
        self._rebuild_inspector()

    # ------------------------------------------------------------ палитра
    @objc.python_method
    def _rebuild_palette(self):
        query = (self.search.stringValue() if self.search else "").strip().lower()
        keys = [k for k in KEYS if query in LABELS[k].lower() or query in k]

        pal_w = self.pal_scroll.frame().size.width or 616
        card_w, card_h, gap = 200, 44, 10
        cols = max(1, int((pal_w - gap) // (card_w + gap)))
        rows = (len(keys) + cols - 1) // cols
        doc_h = max(rows * (card_h + gap) + gap, 10)
        doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, pal_w, doc_h))
        doc.setFlipped_(True)

        for i, key in enumerate(keys):
            col, row = i % cols, i // cols
            x = gap + col * (card_w + gap)
            y = gap + row * (card_h + gap)
            card = PaletteCard.alloc().initWithEditor_key_(self, key)
            card.setFrame_(NSMakeRect(x, y, card_w, card_h))
            doc.addSubview_(card)
        self.pal_scroll.setDocumentView_(doc)

    @objc.python_method
    def paletteToggle(self, key):
        metrics = self.mon.cfg["metrics"]
        if key in metrics:
            metrics.remove(key)
            if self.sel == key:
                self.sel = None
        else:
            metrics.append(key)
            self.sel = key
        save_config(self.mon.cfg)
        self._refresh_all()

    def controlTextDidChange_(self, notification):
        self._rebuild_palette()

    # ------------------------------------------------------------ инспектор
    @objc.python_method
    def _rebuild_inspector(self):
        for v in list(self.insp.subviews()):
            v.removeFromSuperview()
        self.color_well = None
        self.value_label = None
        self.graph = None
        key = self.sel

        if key is None or key not in self.mon.cfg["metrics"]:
            self.insp.addSubview_(
                _label(NSMakeRect(6, 10, 210, 40), "Выбери блок на холсте\nили добавь из палитры", 12)
            )
            return

        self.insp.addSubview_(_label(NSMakeRect(6, 6, 210, 20), LABELS[key], 14, True))

        desc = _label(NSMakeRect(6, 30, 212, 60), DESCRIPTIONS.get(key, ""), 11)
        desc.cell().setWraps_(True)
        self.insp.addSubview_(desc)

        self.insp.addSubview_(_label(NSMakeRect(6, 96, 60, 18), "Значение:", 11, True))
        self.value_label = _label(NSMakeRect(70, 96, 148, 18), "", 11)
        self.insp.addSubview_(self.value_label)
        self._update_value()

        self.insp.addSubview_(_label(NSMakeRect(6, 126, 40, 22), "Цвет:", 11, True))
        well = NSColorWell.alloc().initWithFrame_(NSMakeRect(50, 122, 60, 26))
        well.setColor_(ns_color(self.mon.cfg["colors"][key]))
        well.setContinuous_(True)
        well.setTarget_(self)
        well.setAction_(b"colorChanged:")
        self.color_well = well
        self.insp.addSubview_(well)

        rm = NSButton.alloc().initWithFrame_(NSMakeRect(6, 160, 130, 28))
        rm.setTitle_("Убрать блок")
        rm.setBezelStyle_(1)
        rm.setTarget_(self)
        rm.setAction_(b"removeSelected:")
        self.insp.addSubview_(rm)

        iw = self.insp.frame().size.width
        ih = self.insp.frame().size.height
        gy = 220

        # график рисуем, только если под него есть место в инспекторе
        if ih - gy >= 60:
            self.insp.addSubview_(
                _label(NSMakeRect(6, gy - 22, iw - 12, 18), "График (история):", 11, True)
            )
            gh = ih - gy - 8
            gv = GraphView.alloc().initWithEditor_(self)
            gv.setFrame_(NSMakeRect(6, gy, max(iw - 12, 60), gh))
            self.graph = gv
            self.insp.addSubview_(gv)

    @objc.python_method
    def _update_value(self):
        if not self.value_label or not self.sel:
            return
        for k, lines in self.mon._last_blocks:
            if k == self.sel:
                self.value_label.setStringValue_(" / ".join(lines))
                return
        self.value_label.setStringValue_("—")

    def colorChanged_(self, sender):
        if not self.sel:
            return
        self.mon.cfg["colors"][self.sel] = nscolor_to_hex(sender.color())
        self.mon._render()
        self.canvas.setNeedsDisplay_(True)
        self._rebuild_palette()
        save_config(self.mon.cfg)

    def removeSelected_(self, sender):
        if not self.sel:
            return
        if self.sel in self.mon.cfg["metrics"]:
            self.mon.cfg["metrics"].remove(self.sel)
        self.sel = None
        save_config(self.mon.cfg)
        self._refresh_all()

    # ------------------------------------------------------------ общий вид
    def opacityChanged_(self, sender):
        self.mon.cfg["opacity"] = round(sender.doubleValue(), 2)
        self.mon.window.setAlphaValue_(self.mon.cfg["opacity"])
        save_config(self.mon.cfg)

    def fontPlus_(self, sender):
        self.mon.cfg["font_size"] = min(28, self.mon.cfg["font_size"] + 1)
        self._after_font()

    def fontMinus_(self, sender):
        self.mon.cfg["font_size"] = max(9, self.mon.cfg["font_size"] - 1)
        self._after_font()

    @objc.python_method
    def _after_font(self):
        save_config(self.mon.cfg)
        self.mon._render()
        self.canvas.setNeedsDisplay_(True)

    def showOverlay_(self, sender):
        self.mon.show_overlay()

    def quitApp_(self, sender):
        self.mon.quit_(None)

    def arrange_(self, sender):
        """Сбросить позиции — блоки сложатся аккуратным столбиком."""
        self.mon.cfg["layout"] = {}
        self.mon._board_left = None
        save_config(self.mon.cfg)
        self.mon._render()
        self.canvas.setNeedsDisplay_(True)

    # ------------------------------------------------------------ открытие
    @objc.python_method
    def open(self):
        self.mon.refresh_(None)
        # чтобы график/инспектор были видны сразу — выбираем первый блок
        if self.sel is None and self.mon._last_blocks:
            self.sel = self.mon._last_blocks[0][0]
        self._rebuild_palette()
        self._rebuild_inspector()
        self.win.center()
        NSApp().activateIgnoringOtherApps_(True)
        self.win.makeKeyAndOrderFront_(None)
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, b"tick:", None, True
            )

    def tick_(self, timer):
        if self.win.isVisible():
            self.canvas.setNeedsDisplay_(True)
            self._update_value()
            if self.graph is not None:
                self.graph.setNeedsDisplay_(True)
