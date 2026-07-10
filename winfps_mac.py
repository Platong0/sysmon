#!/usr/bin/env python3
"""
Измерение FPS выбранного окна на macOS через ScreenCaptureKit.

Внутренний fps чужого приложения снаружи не виден. Единственный способ —
захватывать содержимое его окна: ScreenCaptureKit присылает новый кадр только
когда картинка окна реально поменялась. Считаем такие кадры за секунду — это и
есть fps окна (потолок = частота монитора). Нужен доступ «Запись экрана».

Самотест из терминала:
    ./.venv/bin/python winfps_mac.py            # список окон
    ./.venv/bin/python winfps_mac.py <win_id>   # мерить fps окна 6 секунд
"""

import objc
import Quartz
import ScreenCaptureKit as sck
from CoreMedia import CMSampleBufferGetSampleAttachmentsArray, CMTimeMake
from Foundation import NSObject
from libdispatch import dispatch_queue_create

SCREEN = 0  # SCStreamOutputTypeScreen
STATUS_KEY = sck.SCStreamFrameInfoStatus  # ключ вложения со статусом кадра
STATUS_COMPLETE = 0  # SCFrameStatusComplete — кадр с новым содержимым


def list_windows():
    """Список окон приложений: [{id, name, w, h}] (без спец. служебных)."""
    opts = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements
    )
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    out = []
    for w in wins:
        if w.get("kCGWindowLayer", 0) != 0:
            continue  # только обычные окна (не меню/док)
        b = w.get("kCGWindowBounds", {})
        width, height = int(b.get("Width", 0)), int(b.get("Height", 0))
        if width < 120 or height < 80:
            continue
        out.append({
            "id": int(w.get("kCGWindowNumber", 0)),
            "name": w.get("kCGWindowOwnerName", "?"),
            "w": width,
            "h": height,
        })
    return out


class WindowFPS(NSObject):
    """Захватывает выбранное окно и считает его fps. self.fps обновляй снаружи
    раз в секунду из self.count (см. Monitor.refresh_)."""

    def init(self):
        self = objc.super(WindowFPS, self).init()
        if self is None:
            return None
        self.count = 0          # счётчик пришедших «полных» кадров
        self.active = False
        self.target = None      # имя приложения окна
        self.error = None       # текст ошибки (нет доступа и т.п.)
        self._stream = None
        self._wid = None
        self._queue = dispatch_queue_create(b"sysmon.winfps", None)
        return self

    # ---- управление ----
    @objc.python_method
    def start(self, window_id, name=None):
        self.stop()
        self.count = 0
        self.error = None
        self.target = name
        self._wid = int(window_id)
        self.active = True
        sck.SCShareableContent.getShareableContentWithCompletionHandler_(
            self._got_content
        )

    @objc.python_method
    def stop(self):
        self.active = False
        self.target = None
        if self._stream is not None:
            try:
                self._stream.stopCaptureWithCompletionHandler_(lambda e: None)
            except Exception:
                pass
            self._stream = None

    # ---- внутреннее ----
    @objc.python_method
    def _got_content(self, content, error):
        if error is not None or content is None:
            self.error = "нет доступа «Запись экрана»"
            self.active = False
            return
        target = None
        for win in content.windows():
            if int(win.windowID()) == self._wid:
                target = win
                break
        if target is None:
            self.error = "окно закрылось"
            self.active = False
            return
        try:
            filt = sck.SCContentFilter.alloc().initWithDesktopIndependentWindow_(target)
            cfg = sck.SCStreamConfiguration.alloc().init()
            fr = target.frame()
            cfg.setWidth_(max(2, int(fr.size.width / 4)))
            cfg.setHeight_(max(2, int(fr.size.height / 4)))
            cfg.setMinimumFrameInterval_(CMTimeMake(1, 240))  # потолок 240 fps
            cfg.setQueueDepth_(8)
            cfg.setShowsCursor_(False)
            stream = sck.SCStream.alloc().initWithFilter_configuration_delegate_(
                filt, cfg, self
            )
            ok = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self, SCREEN, self._queue, None
            )
            if isinstance(ok, tuple):
                ok = ok[0]
            if not ok:
                self.error = "не удалось подключить вывод"
                self.active = False
                return
            stream.startCaptureWithCompletionHandler_(self._started)
            self._stream = stream
        except Exception as e:  # noqa: BLE001
            self.error = f"ошибка захвата: {e}"
            self.active = False

    @objc.python_method
    def _started(self, error):
        if error is not None:
            self.error = "нет доступа «Запись экрана»"
            self.active = False

    # ---- SCStreamOutput ----
    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, sctype):
        if sctype != SCREEN:
            return
        status = STATUS_COMPLETE
        arr = CMSampleBufferGetSampleAttachmentsArray(sbuf, False)
        if arr is not None and len(arr) > 0:
            val = arr[0].objectForKey_(STATUS_KEY)
            if val is not None:
                status = int(val)
        if status == STATUS_COMPLETE:
            self.count += 1

    # ---- SCStreamDelegate ----
    def stream_didStopWithError_(self, stream, error):
        self.active = False
        if error is not None:
            self.error = "захват остановлен"


def _selftest():
    import sys
    import time

    from AppKit import NSApp, NSApplication
    from Foundation import NSTimer

    NSApplication.sharedApplication()
    wf = WindowFPS.alloc().init()

    if len(sys.argv) < 2:
        print("Окна (id — имя — размер):")
        for w in list_windows():
            print(f"  {w['id']:>7}  {w['name']:<24} {w['w']}x{w['h']}")
        print("\nЗапусти:  python winfps_mac.py <id>  — мерить fps этого окна")
        return

    wid = int(sys.argv[1])
    print(f"меряю окно #{wid} 6 секунд…", flush=True)
    wf.start(wid, "test")
    state = {"last": 0, "t": time.monotonic(), "n": 0}

    class Ticker(NSObject):
        def tick_(self, timer):
            now = time.monotonic()
            dt = now - state["t"]
            fps = (wf.count - state["last"]) / dt if dt > 0 else 0
            state["last"], state["t"] = wf.count, now
            state["n"] += 1
            msg = f"fps={fps:5.1f}  (кадров всего: {wf.count})"
            if wf.error:
                msg += f"  [{wf.error}]"
            print(msg, flush=True)
            if state["n"] >= 6:
                wf.stop()
                NSApp().terminate_(None)

    tk = Ticker.alloc().init()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, tk, b"tick:", None, True
    )
    NSApp().run()


if __name__ == "__main__":
    _selftest()
