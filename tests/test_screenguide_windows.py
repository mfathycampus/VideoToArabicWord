"""«سجّلني» على ويندوز نفسه — منقولة من WebDoc (``test_screen_windows.py``).

ما لا يُفحص في مكان آخر: أن خطّافات النظام تُركَّب وتُزال، وأن صورة الشاشة
تُلتقط، وأن اسم الزرّ يُقرأ من البرنامج نفسه، وأن نقرةً في برنامج **آخر** —
وهي الغاية كلّها — تصل خطوةً بصورتها. كل ذلك بحساب مستخدم عادي.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

#: A separate program with one button, as any desktop program has: it prints where
#: the button is and waits.
OTHER_PROGRAM = textwrap.dedent(r'''
    import ctypes, json, sys, threading
    from ctypes import wintypes as wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
        wt.HINSTANCE, wt.LPVOID]
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    hwnd = user32.CreateWindowExW(0x8, "BUTTON", "Press me", 0x90000000,
                                  200, 200, 240, 80, None, None, None, None)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.SetForegroundWindow(hwnd)
    print(json.dumps([rect.left, rect.top, rect.right, rect.bottom]), flush=True)
    threading.Thread(target=lambda: (sys.stdin.read(), user32.PostQuitMessage(0),
                                     __import__("os")._exit(0)), daemon=True).start()
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
''')


@pytest.fixture
def other_program():
    process = subprocess.Popen([sys.executable, "-c", OTHER_PROGRAM],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert line, "the other program did not start"
        left, top, right, bottom = json.loads(line)
        time.sleep(0.5)
        yield process, ((left + right) // 2, (top + bottom) // 2)
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=5)
        except Exception:
            process.kill()


def _on_new_thread(fn):
    """Run as a thread of this program would: real pixels, COM ready."""
    from screenguide import windows_source as w

    box: dict = {}

    def run():
        w.per_monitor_dpi()
        w.ole32.CoInitializeEx(None, 0)
        try:
            box["value"] = fn()
        except BaseException as exc:  # pragma: no cover - reported below
            box["error"] = exc
        finally:
            w.ole32.CoUninitialize()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def test_a_picture_of_the_monitor_is_taken():
    from screenguide import windows_source as w

    def take():
        rect = w.monitor_rect(point=(0, 0))
        return w.grab(rect), rect

    image, rect = _on_new_thread(take)
    assert image.size == (rect.right - rect.left, rect.bottom - rect.top)
    assert image.mode == "RGB"


def test_a_control_is_named_by_its_own_label(other_program):
    from screenguide import windows_source as w

    _, (x, y) = other_program
    found = _on_new_thread(lambda: w.element_at(x, y))
    assert found is not None
    assert found["name"] == "Press me"
    assert found["role"] == "button"
    bx, by, bw, bh = found["box"]
    assert bx <= x <= bx + bw and by <= y <= by + bh


def test_the_program_is_named_as_it_names_itself(other_program):
    from screenguide import windows_source as w

    _, (x, y) = other_program
    window = _on_new_thread(
        lambda: w.Programs().window(w.user32.WindowFromPoint(w.wt.POINT(x, y))))
    assert window.process.startswith("python")
    assert window.app   # "Python" - from the program's own version information
    assert window.protected is False   # same rights as this program


def test_the_recorder_starts_and_lets_go():
    from screenguide.windows_source import WindowsScreenRecorder

    recorder = WindowsScreenRecorder(language="ar")
    recorder.start()
    try:
        assert len(recorder._hooks) == 2
        assert recorder._bar, "the floating bar is shown"
        recorder.show_steps(3)
    finally:
        recorder.close()
    assert recorder._hooks == []
    assert all(not t.is_alive() for t in recorder._threads)


def test_a_click_in_another_program_becomes_a_step(other_program):
    from screenguide import windows_source as w
    from screenguide.windows_source import WindowsScreenRecorder

    _, (x, y) = other_program

    def press():
        w.per_monitor_dpi()
        w.user32.SetCursorPos(x, y)
        time.sleep(0.1)
        w.user32.mouse_event(0x0002, 0, 0, 0, 0)   # left down
        time.sleep(0.05)
        w.user32.mouse_event(0x0004, 0, 0, 0, 0)   # left up

    recorder = WindowsScreenRecorder(language="en")
    recorder.start()
    try:
        threading.Thread(target=press).start()
        events = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(e.kind == "up" for e in events):
            event = recorder.next_event(timeout=0.5)
            if event is not None:
                events.append(event)
        recorder.request_finish()
        finish = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and finish is None:
            event = recorder.next_event(timeout=0.5)
            if event is not None and event.kind == "finish":
                finish = event
    finally:
        recorder.close()

    clicks = [e for e in events if e.kind == "click"]
    assert clicks, f"no click arrived: {[e.kind for e in events]}"
    click = clicks[0]
    assert click.window.process.startswith("python")
    assert click.target.name == "Press me"
    assert click.frame is not None and click.point is not None
    px, py = click.point
    bx, by, bw, bh = click.target.box
    assert bx <= px <= bx + bw and by <= py <= by + bh, "the box is in the picture's pixels"
    assert any(e.kind == "up" for e in events)
    assert finish is not None
