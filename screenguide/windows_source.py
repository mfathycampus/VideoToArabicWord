"""«سجّلني» على شاشة ويندوز كاملة، لحساب مستخدم عادي.

منقولٌ من Moeen (``infrastructure/desktop/windows.py``) بلا تغيير في السلوك:
أربعة أجزاء، كلٌّ على خيطه لأن لكلٍّ سببًا مختلفًا للانتظار:

* **hooks** — خطّافا الفأرة ولوحة المفاتيح منخفضا المستوى، والشريط العائم.
  ويندوز يستدعي الخطّاف قبل تسليم الإدخال ويُسقط خطّافًا يُبطئه، فلا يفعل
  الخطّاف إلا تدوين ما حدث ثم يعود.
* **capture** — يلتقط الصورة فورًا قبل أن يتفاعل البرنامج. الالتقاط لا ينتظر
  برنامجًا آخر، فبرنامجٌ معلَّق لا يؤخّر صورة الخطوة التالية.
* **describe** — يسأل البرنامج عمّا تحت المؤشر (Microsoft Active
  Accessibility، يجيب عنها كل برنامج ويندوز ومنها Office والمتصفحات). وهذا
  **ينتظر** البرنامج، فيقع بعد الصورة وبالترتيب.
* **watch** — برنامجٌ شُغّل «كمسؤول» لا يبلّغ إدخاله لبرنامج بلا تلك
  الصلاحيات. ما دام في المقدّمة تُراقَب أزرار الفأرة بدلًا من ذلك، فتبقى
  النقرة خطوةً بصورتها.

لا صلاحيات مدير ولا تثبيت، والإدخال لا يُحبس ولا يُغيَّر أبدًا: برامج الشخص
تتلقّى كل نقرة ومفتاح كما لو لم يكن معين يعمل.

ما يُكتب لا يُسجَّل أبدًا — فقط أن مفتاحًا نزل، لتكون «ملء الحقل» خطوة.
والاختصارات تُسجَّل بأسمائها («Ctrl+S»).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path
from typing import Any

from screenguide.model import (
    ScreenEvent,
    ScreenEventSource,
    ScreenTarget,
    ScreenWindow,
)

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
oleacc = ctypes.WinDLL("oleacc")
ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
version = ctypes.WinDLL("version")

LRESULT = wt.LPARAM
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_SETFONT = 0x0030
WM_NCHITTEST = 0x0084
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_COMMAND = 0x0111
WM_CTLCOLORSTATIC = 0x0138
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_BAR_REFRESH = WM_APP + 1
HTCLIENT = 1
HTCAPTION = 2

GA_ROOT = 2
GW_OWNER = 4
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYOUTRTL = 0x00400000
WS_EX_NOACTIVATE = 0x08000000
SS_CENTERIMAGE = 0x0200
BS_PUSHBUTTON = 0x0000
SW_MINIMIZE = 6
SW_RESTORE = 9
MONITOR_DEFAULTTONEAREST = 2
MONITOR_DEFAULTTOPRIMARY = 1
WDA_EXCLUDEFROMCAPTURE = 0x00000011
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20
COINIT_MULTITHREADED = 0x0
OBJID_CLIENT = 0xFFFFFFFC
CHILDID_SELF = 0
VT_EMPTY = 0
VT_I4 = 3
VT_DISPATCH = 9
STATE_SYSTEM_CHECKED = 0x00000010
STATE_SYSTEM_READONLY = 0x00000040
STATE_SYSTEM_PROTECTED = 0x20000000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE = 0x08, 0x09, 0x0D, 0x1B
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_F1, VK_F9, VK_F10, VK_F12 = 0x70, 0x78, 0x79, 0x7B
_MODIFIERS = frozenset({VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN,
                        0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x14, 0x90, 0x91})
_KEY_NAMES = {
    VK_BACK: "Backspace", VK_TAB: "Tab", VK_RETURN: "Enter", VK_ESCAPE: "Esc",
    0x20: "Space", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down", 0x2C: "PrintScreen",
    0x2D: "Insert", 0x2E: "Delete", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xBA: ";", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -", 0x6E: "Num .", 0x6F: "Num /",
}

#: The recorder's own keys. Swallowed, so they do nothing in the program in front.
HOTKEY_PAUSE = VK_F9     # Ctrl+Shift+F9
HOTKEY_FINISH = VK_F10   # Ctrl+Shift+F10

_IID_IACCESSIBLE = (0x618736E0, 0x3C3D, 0x11CF,
                    (0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71))

#: Microsoft Active Accessibility roles -> the role names the rest of the app uses.
_ROLES = {
    1: "pane", 2: "menubar", 3: "scrollbar", 9: "window", 10: "client", 11: "menu",
    12: "menuitem", 14: "pane", 15: "pane", 16: "pane", 20: "group", 22: "toolbar",
    23: "status", 24: "table", 25: "columnheader", 26: "rowheader", 28: "row",
    29: "cell", 30: "link", 33: "list", 34: "option", 35: "tree", 36: "treeitem",
    37: "tab", 40: "image", 41: "text", 42: "textbox", 43: "button", 44: "checkbox",
    45: "radio", 46: "combobox", 47: "combobox", 50: "slider", 52: "spinbutton",
    56: "button", 57: "button", 60: "tablist", 62: "button",
}
#: A role that is worth naming a step by. Anything else defers to its parent.
_NAMED_ROLES = frozenset({
    "menuitem", "cell", "link", "option", "treeitem", "tab", "textbox", "button",
    "checkbox", "radio", "combobox", "slider", "spinbutton", "columnheader",
    "rowheader", "image", "text",
})
_CONTAINER_ROLES = frozenset({"pane", "window", "client", "menubar", "toolbar",
                              "group", "list", "tree", "table", "row", "tablist",
                              "status", "scrollbar", "menu"})


# -------------------------------------------------------------------- structures
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _VariantValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("ptr", ctypes.c_void_p),
                ("pad", ctypes.c_ubyte * (2 * ctypes.sizeof(ctypes.c_void_p)))]


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.WORD), ("r2", wt.WORD), ("r3", wt.WORD),
                ("v", _VariantValue)]


def _child(child_id: int) -> VARIANT:
    var = VARIANT()
    var.vt = VT_I4
    var.v.lVal = child_id
    return var


# ---------------------------------------------------------------- signatures
def _sig(fn: Any, restype: Any, *argtypes: Any) -> None:
    fn.restype = restype
    fn.argtypes = list(argtypes)


_sig(user32.SetWindowsHookExW, ctypes.c_void_p, ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
_sig(user32.CallNextHookEx, LRESULT, ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM)
_sig(user32.UnhookWindowsHookEx, wt.BOOL, ctypes.c_void_p)
_sig(user32.GetMessageW, wt.BOOL, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
_sig(user32.TranslateMessage, wt.BOOL, ctypes.POINTER(wt.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wt.MSG))
_sig(user32.PostThreadMessageW, wt.BOOL, wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.SendMessageW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.RegisterClassExW, wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
     wt.HINSTANCE, wt.LPVOID)
_sig(user32.DestroyWindow, wt.BOOL, wt.HWND)
_sig(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.ShowWindowAsync, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.SetWindowTextW, wt.BOOL, wt.HWND, wt.LPCWSTR)
_sig(user32.WindowFromPoint, wt.HWND, wt.POINT)
_sig(user32.GetAncestor, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.GetWindowLongW, ctypes.c_long, wt.HWND, ctypes.c_int)
_sig(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_sig(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetForegroundWindow, wt.HWND)
_sig(user32.IsWindowVisible, wt.BOOL, wt.HWND)
_sig(user32.IsIconic, wt.BOOL, wt.HWND)
_sig(user32.GetGUIThreadInfo, wt.BOOL, wt.DWORD, ctypes.POINTER(GUITHREADINFO))
_sig(user32.ClientToScreen, wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
_sig(user32.MonitorFromPoint, wt.HMONITOR, wt.POINT, wt.DWORD)
_sig(user32.MonitorFromWindow, wt.HMONITOR, wt.HWND, wt.DWORD)
_sig(user32.GetMonitorInfoW, wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFO))
_sig(user32.GetDoubleClickTime, wt.UINT)
_sig(user32.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
_sig(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
_sig(user32.LoadCursorW, wt.HANDLE, wt.HINSTANCE, wt.LPVOID)
_sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_sig(user32.EnumWindows, wt.BOOL, EnumWindowsProc, wt.LPARAM)
_sig(kernel32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)
_sig(kernel32.GetCurrentThreadId, wt.DWORD)
_sig(kernel32.GetTickCount, wt.DWORD)
_sig(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_sig(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
_sig(kernel32.GetCurrentProcess, wt.HANDLE)
_sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR,
     ctypes.POINTER(wt.DWORD))
_sig(advapi32.OpenProcessToken, wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
_sig(advapi32.GetTokenInformation, wt.BOOL, wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD,
     ctypes.POINTER(wt.DWORD))
_sig(gdi32.CreateFontW, wt.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
     wt.DWORD, wt.LPCWSTR)
_sig(gdi32.CreateSolidBrush, wt.HBRUSH, wt.COLORREF)
_sig(gdi32.SetTextColor, wt.COLORREF, wt.HDC, wt.COLORREF)
_sig(gdi32.SetBkColor, wt.COLORREF, wt.HDC, wt.COLORREF)
_sig(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
_sig(oleacc.AccessibleObjectFromPoint, ctypes.c_long, wt.POINT,
     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(VARIANT))
_sig(oleacc.AccessibleObjectFromWindow, ctypes.c_long, wt.HWND, wt.DWORD,
     ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_sig(ole32.CoInitializeEx, ctypes.c_long, wt.LPVOID, wt.DWORD)
_sig(ole32.CoUninitialize, None)
_sig(oleaut32.SysFreeString, None, ctypes.c_void_p)
_sig(oleaut32.VariantClear, ctypes.c_long, ctypes.POINTER(VARIANT))
_sig(version.GetFileVersionInfoSizeW, wt.DWORD, wt.LPCWSTR, ctypes.POINTER(wt.DWORD))
_sig(version.GetFileVersionInfoW, wt.BOOL, wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID)
_sig(version.VerQueryValueW, wt.BOOL, wt.LPCVOID, wt.LPCWSTR,
     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT))
with contextlib.suppress(AttributeError):  # Windows 10 1607 and later
    _sig(user32.SetThreadDpiAwarenessContext, ctypes.c_void_p, ctypes.c_void_p)
    _sig(user32.GetDpiForSystem, wt.UINT)
with contextlib.suppress(AttributeError):  # Windows 10 2004 and later for "exclude"
    _sig(user32.SetWindowDisplayAffinity, wt.BOOL, wt.HWND, wt.DWORD)


def per_monitor_dpi() -> None:
    """Make this thread see real pixels.

    On a screen scaled to 125% or 150%, a thread that is not "DPI aware" is told
    scaled coordinates while the picture is taken in real pixels - and every
    step's number lands in the wrong place. Each thread here asks for real pixels.
    """
    with contextlib.suppress(AttributeError, OSError):
        user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


# --------------------------------------------------------- windows and programs
def window_text(hwnd: Any) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_class(hwnd: Any) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: Any) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _image_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _token_elevated(process: Any) -> bool | None:
    """True or False, or None when the token cannot even be read (a protected
    program, or one with more rights than this one)."""
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        elevated = wt.DWORD(0)
        size = wt.DWORD(0)
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION_CLASS,
                                            ctypes.byref(elevated), 4, ctypes.byref(size)):
            return None
        return bool(elevated.value)
    finally:
        kernel32.CloseHandle(token)


def process_elevated(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True
    try:
        result = _token_elevated(handle)
        return True if result is None else result
    finally:
        kernel32.CloseHandle(handle)


def self_elevated() -> bool:
    return bool(_token_elevated(kernel32.GetCurrentProcess()))


def file_description(path: str) -> str:
    """The name a program gives itself ("Microsoft Excel"), from its version info."""
    if not path:
        return ""
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return ""
        pointer = ctypes.c_void_p()
        length = wt.UINT(0)
        codepages: list[str] = []
        if version.VerQueryValueW(data, "\\VarFileInfo\\Translation",
                                  ctypes.byref(pointer), ctypes.byref(length)) and length.value:
            pairs = (wt.WORD * (length.value // 2)).from_address(pointer.value or 0)
            for i in range(0, len(pairs) - 1, 2):
                codepages.append(f"{pairs[i]:04x}{pairs[i + 1]:04x}")
        codepages += ["040904b0", "040904e4", "000004b0"]
        for codepage in codepages:
            if version.VerQueryValueW(
                data, f"\\StringFileInfo\\{codepage}\\FileDescription",
                ctypes.byref(pointer), ctypes.byref(length),
            ) and length.value and pointer.value:
                text = ctypes.wstring_at(pointer.value, length.value).rstrip("\x00").strip()
                if text:
                    return text[:80]
    except OSError:
        return ""
    return ""


class Programs:
    """Names and rights of the programs on screen, looked up once each."""

    def __init__(self) -> None:
        self._own = os.getpid()
        self._elevated_self = self_elevated()
        self._by_pid: dict[int, tuple[str, str, bool]] = {}

    def is_own(self, pid: int) -> bool:
        return pid == self._own

    def program(self, pid: int) -> tuple[str, str, bool]:
        """(friendly name, process stem, protected)."""
        known = self._by_pid.get(pid)
        if known is not None:
            return known
        path = _image_path(pid)
        stem = Path(path).stem.casefold() if path else ""
        name = file_description(path) or (Path(path).stem if path else "")
        protected = (not self._elevated_self) and process_elevated(pid)
        if len(self._by_pid) > 256:
            self._by_pid.clear()
        self._by_pid[pid] = (name, stem, protected)
        return name, stem, protected

    def window(self, hwnd: Any) -> ScreenWindow:
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        pid = window_pid(root)
        app, stem, protected = self.program(pid)
        title = window_text(root).strip()
        cls = window_class(root)
        owner = user32.GetWindow(root, GW_OWNER)
        style = user32.GetWindowLongW(root, GWL_STYLE) & 0xFFFFFFFF
        dialog = cls == "#32770" or bool(
            owner and title and cls != "#32768" and (style & WS_CAPTION) == WS_CAPTION
        )
        if stem in ("applicationframehost",) and title:
            # A Store app: the frame belongs to Windows, the title is the app.
            app = title
        return ScreenWindow(app=app, title=title, process=stem, dialog=dialog,
                            protected=protected)


def monitor_rect(*, point: tuple[int, int] | None = None, hwnd: Any = None) -> wt.RECT:
    if point is not None:
        handle = user32.MonitorFromPoint(wt.POINT(point[0], point[1]),
                                         MONITOR_DEFAULTTONEAREST)
    else:
        handle = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(handle, ctypes.byref(info))
    return info.rcMonitor


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000

_sig(user32.GetDC, wt.HDC, wt.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
_sig(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
_sig(gdi32.CreateCompatibleBitmap, wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_sig(gdi32.BitBlt, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)
_sig(gdi32.GetDIBits, ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID,
     ctypes.POINTER(BITMAPINFO), wt.UINT)
_sig(gdi32.DeleteDC, wt.BOOL, wt.HDC)


def grab(rect: wt.RECT) -> Any:
    """The monitor's picture, menus and tooltips included (CAPTUREBLT).

    Taken with plain system calls rather than an imaging library's: those release
    the interpreter while the screen is copied, so the hooks - which every mouse
    movement on the machine passes through - never wait for a picture.
    """
    from PIL import Image

    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise ValueError("empty monitor")
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        if not gdi32.BitBlt(memory, 0, 0, width, height, screen, rect.left, rect.top,
                            SRCCOPY | CAPTUREBLT):
            raise ctypes.WinError(ctypes.get_last_error())
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height   # top row first
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0    # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).copy()


# ------------------------------------------------------------- accessibility
def _vfunc(pointer: int, index: int, restype: Any, *argtypes: Any) -> Any:
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def _release(pointer: int | None) -> None:
    if pointer:
        with contextlib.suppress(OSError):
            _vfunc(pointer, 2, wt.ULONG)(pointer)


def _iid() -> GUID:
    d1, d2, d3, d4 = _IID_IACCESSIBLE
    return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))


class Accessible:
    """One object a program describes about itself (IAccessible), read with plain
    calls through its function table - no COM library to install or ship."""

    def __init__(self, pointer: int, child: int = CHILDID_SELF) -> None:
        self.pointer = pointer
        self.child = child

    def _bstr(self, index: int) -> str:
        out = ctypes.c_void_p()
        try:
            hr = _vfunc(self.pointer, index, ctypes.c_long, VARIANT,
                        ctypes.POINTER(ctypes.c_void_p))(
                self.pointer, _child(self.child), ctypes.byref(out))
        except OSError:
            return ""
        try:
            if hr < 0 or not out.value:
                return ""
            return ctypes.wstring_at(out.value) or ""
        finally:
            if out.value:
                oleaut32.SysFreeString(out)

    def _variant(self, index: int) -> int | None:
        out = VARIANT()
        try:
            hr = _vfunc(self.pointer, index, ctypes.c_long, VARIANT,
                        ctypes.POINTER(VARIANT))(
                self.pointer, _child(self.child), ctypes.byref(out))
        except OSError:
            return None
        try:
            if hr < 0 or out.vt != VT_I4:
                return None
            return int(out.v.lVal)
        finally:
            oleaut32.VariantClear(ctypes.byref(out))

    def name(self) -> str:
        return " ".join(self._bstr(10).split())

    def role(self) -> str | None:
        value = self._variant(13)
        return _ROLES.get(value) if value is not None else None

    def state(self) -> int:
        return self._variant(14) or 0

    def location(self) -> tuple[int, int, int, int] | None:
        x, y, w, h = ctypes.c_long(), ctypes.c_long(), ctypes.c_long(), ctypes.c_long()
        try:
            hr = _vfunc(self.pointer, 22, ctypes.c_long,
                        ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
                        ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
                        VARIANT)(
                self.pointer, ctypes.byref(x), ctypes.byref(y), ctypes.byref(w),
                ctypes.byref(h), _child(self.child))
        except OSError:
            return None
        if hr < 0 or w.value <= 0 or h.value <= 0:
            return None
        return x.value, y.value, w.value, h.value

    def parent(self) -> Accessible | None:
        """The object one level up. A child id's parent is its own object."""
        if self.child != CHILDID_SELF:
            _vfunc(self.pointer, 1, wt.ULONG)(self.pointer)  # AddRef: shared pointer
            return Accessible(self.pointer)
        out = ctypes.c_void_p()
        try:
            hr = _vfunc(self.pointer, 7, ctypes.c_long,
                        ctypes.POINTER(ctypes.c_void_p))(self.pointer, ctypes.byref(out))
        except OSError:
            return None
        if hr < 0 or not out.value:
            return None
        return _as_accessible(out.value)

    def focus(self) -> Accessible | None:
        """The descendant that has the keyboard (get_accFocus)."""
        out = VARIANT()
        try:
            hr = _vfunc(self.pointer, 18, ctypes.c_long,
                        ctypes.POINTER(VARIANT))(self.pointer, ctypes.byref(out))
        except OSError:
            return None
        if hr < 0:
            return None
        if out.vt == VT_I4 and out.v.lVal != CHILDID_SELF:
            _vfunc(self.pointer, 1, wt.ULONG)(self.pointer)
            return Accessible(self.pointer, int(out.v.lVal))
        if out.vt == VT_DISPATCH and out.v.ptr:
            # The variant's reference is handed to _as_accessible, which releases it.
            return _as_accessible(out.v.ptr)
        oleaut32.VariantClear(ctypes.byref(out))
        return None

    def release(self) -> None:
        _release(self.pointer)
        self.pointer = 0


def _as_accessible(dispatch: int) -> Accessible | None:
    """QueryInterface an IDispatch for IAccessible; releases the IDispatch."""
    out = ctypes.c_void_p()
    iid = _iid()
    try:
        hr = _vfunc(dispatch, 0, ctypes.c_long, ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p))(dispatch, ctypes.byref(iid),
                                                     ctypes.byref(out))
    except OSError:
        hr = -1
    _release(dispatch)
    if hr < 0 or not out.value:
        return None
    return Accessible(out.value)


def _read(item: Accessible) -> tuple[str, str | None, int, tuple[int, int, int, int] | None]:
    return item.name(), item.role(), item.state(), item.location()


def describe(start: Accessible | None, *, depth: int = 4) -> dict[str, Any] | None:
    """Name the control: the innermost object worth naming, walking up from the one
    under the pointer (a label inside a button names the button)."""
    chain: list[tuple[str, str | None, int, tuple[int, int, int, int] | None]] = []
    current = start
    try:
        for _ in range(depth):
            if current is None:
                break
            chain.append(_read(current))
            parent = current.parent()
            current.release()
            current = parent
    finally:
        if current is not None:
            current.release()
    if not chain:
        return None

    def pick() -> tuple[str, str | None, int, tuple[int, int, int, int] | None] | None:
        first_name, first_role = chain[0][0], chain[0][1]
        if first_role in ("text", "image") or (first_role in _CONTAINER_ROLES and
                                              not first_name):
            # A label or an icon inside a control: the control is what was pressed.
            for entry in chain[1:3]:
                if entry[0] and entry[1] in _NAMED_ROLES and entry[1] not in ("text",
                                                                              "image"):
                    return entry
        for entry in chain:
            if entry[0] and entry[1] in _NAMED_ROLES:
                return entry
        return None

    chosen = pick()
    if chosen is None:
        return None
    name, role, state, box = chosen
    if role == "textbox" and state & STATE_SYSTEM_READONLY:
        role = "text"
    return {
        "name": name[:200],
        "role": role,
        "box": box,
        "checked": bool(state & STATE_SYSTEM_CHECKED) if role in ("checkbox", "radio")
        else None,
        "secret": bool(state & STATE_SYSTEM_PROTECTED),
        "editable": role in ("textbox", "combobox", "cell"),
    }


def element_at(x: int, y: int) -> dict[str, Any] | None:
    pointer = ctypes.c_void_p()
    child = VARIANT()
    hr = oleacc.AccessibleObjectFromPoint(wt.POINT(x, y), ctypes.byref(pointer),
                                          ctypes.byref(child))
    if hr < 0 or not pointer.value:
        return None
    child_id = int(child.v.lVal) if child.vt == VT_I4 else CHILDID_SELF
    return describe(Accessible(pointer.value, child_id))


def focused_element(hwnd_focus: Any) -> dict[str, Any] | None:
    """The field that has the keyboard, inside the window that has the focus."""
    if not hwnd_focus:
        return None
    pointer = ctypes.c_void_p()
    iid = _iid()
    hr = oleacc.AccessibleObjectFromWindow(hwnd_focus, OBJID_CLIENT, ctypes.byref(iid),
                                           ctypes.byref(pointer))
    if hr < 0 or not pointer.value:
        return None
    current = Accessible(pointer.value)
    for _ in range(6):   # a browser or an Office document nests its focus
        inner = current.focus()
        if inner is None:
            break
        current.release()
        current = inner
        if current.child != CHILDID_SELF:
            break   # a simple child has no focus of its own to ask about
    return describe(current, depth=2)


# -------------------------------------------------------------------- the bar
_BAR_CLASS = "MoeenScreenGuideBar"
_bars: dict[int, WindowsScreenRecorder] = {}
_class_registered = False


@WNDPROC
def _bar_proc(hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
    recorder = _bars.get(int(hwnd or 0))
    if recorder is not None:
        try:
            handled = recorder._bar_message(hwnd, msg, wparam, lparam)
        except Exception:
            logger.debug("screen guide bar: message failed", exc_info=True)
            handled = None
        if handled is not None:
            return handled
    result: int = user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return result


def _register_bar_class() -> None:
    global _class_registered
    if _class_registered:
        return
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _bar_proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
    wc.hbrBackground = gdi32.CreateSolidBrush(_BAR_BG)
    wc.lpszClassName = _BAR_CLASS
    if not user32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
        raise ctypes.WinError(ctypes.get_last_error())
    _class_registered = True


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


_BAR_BG = _rgb(31, 41, 55)
_BAR_FG = _rgb(255, 255, 255)
_ID_PAUSE, _ID_UNDO, _ID_FINISH = 101, 102, 103

_TEXT = {
    "ar": {"rec": "● معين يسجّل خطواتك: {n}", "paused": "‖ متوقّف مؤقتًا: {n}",
           "pause": "إيقاف مؤقت", "resume": "استئناف", "undo": "تراجع",
           "finish": "إنهاء وحفظ"},
    "en": {"rec": "● Recording the screen: {n} steps", "paused": "‖ Paused: {n} steps",
           "pause": "Pause", "resume": "Resume", "undo": "Undo", "finish": "Stop & save"},
}


# ---------------------------------------------------------------- the recorder
class WindowsScreenRecorder(ScreenEventSource):
    def __init__(self, *, language: str = "ar", title: str = "") -> None:
        self._language = "ar" if language.startswith("ar") else "en"
        self._title = title
        self._events: queue.Queue[ScreenEvent] = queue.Queue()
        self._raw: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._described: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._threads: list[threading.Thread] = []
        self._hook_tid = 0
        self._hooks: list[Any] = []
        self._procs: list[Any] = []
        self._bar = None
        self._bar_children: dict[int, Any] = {}
        self._font = None
        self._paused = False
        self._steps = 0
        self._finished = False
        self._programs: Programs | None = None
        self._double_ms = int(user32.GetDoubleClickTime() or 500)
        # capture-thread state
        self._typing_hwnd: Any = None
        self._last_down: dict[str, tuple[int, int, int]] = {}
        self._brush: Any = None
        self._last_foreign: Any = None
        self._down_sent: dict[str, bool] = {}
        self._minimised: list[Any] = []

    @property
    def double_click_ms(self) -> int:
        return self._double_ms

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._programs = Programs()
        for target, name in ((self._hook_loop, "hooks"), (self._capture_loop, "capture"),
                             (self._describe_loop, "describe"), (self._watch_loop, "watch")):
            thread = threading.Thread(target=target, name=f"moeen-screen-{name}", daemon=True)
            self._threads.append(thread)
            thread.start()
        ready = self._ready.wait(10)
        if self._error is not None or not ready:
            self.close()
            raise RuntimeError(f"screen recording could not start: {self._error}")
        # Out of the person's way: their programs are what is being recorded.
        self._minimised = _minimise_own_windows()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._hook_tid:
            user32.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)
        self._raw.put(("stop",))
        self._described.put(None)
        for thread in self._threads:
            thread.join(3)
        _restore_windows(self._minimised)

    # ------------------------------------------------------------------ source
    def next_event(self, timeout: float | None = None) -> ScreenEvent | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def show_steps(self, steps: int) -> None:
        self._steps = steps
        if self._bar:
            user32.PostMessageW(self._bar, WM_BAR_REFRESH, 0, 0)

    def request_finish(self) -> None:
        self._raw.put(("finish", "app"))

    def _emit(self, event: ScreenEvent) -> None:
        self._events.put_nowait(event)

    # ------------------------------------------------------------- hook thread
    def _hook_loop(self) -> None:
        try:
            per_monitor_dpi()
            self._hook_tid = int(kernel32.GetCurrentThreadId())
            module = kernel32.GetModuleHandleW(None)
            mouse = HOOKPROC(self._on_mouse)
            keyboard = HOOKPROC(self._on_key)
            self._procs = [mouse, keyboard]
            for kind, proc in ((WH_MOUSE_LL, mouse), (WH_KEYBOARD_LL, keyboard)):
                hook = user32.SetWindowsHookExW(kind, proc, module, 0)
                if not hook:
                    raise ctypes.WinError(ctypes.get_last_error())
                self._hooks.append(hook)
            try:
                self._create_bar()
            except Exception:
                # Without the bar the recording still works: the shortcuts and the
                # application's own Finish button stop it.
                logger.warning("screen guide: the floating bar could not be shown", exc_info=True)
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            self._unhook()
            return
        self._ready.set()
        msg = wt.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._unhook()
            self._destroy_bar()

    def _unhook(self) -> None:
        for hook in self._hooks:
            with contextlib.suppress(OSError):
                user32.UnhookWindowsHookEx(hook)
        self._hooks.clear()

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code >= 0 and wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP,
                                        WM_RBUTTONDOWN, WM_RBUTTONUP):
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self._raw.put_nowait(("mouse", int(wparam), int(info.pt.x), int(info.pt.y),
                                      int(info.time), "hook"))
        except Exception:
            pass
        result: int = user32.CallNextHookEx(None, code, wparam, lparam)
        return result

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(info.vkCode)
                ctrl = _down(VK_CONTROL)
                shift = _down(VK_SHIFT)
                alt = _down(VK_MENU) or bool(info.flags & 0x20)
                win = _down(VK_LWIN) or _down(VK_RWIN)
                if ctrl and shift and not alt and vk == HOTKEY_PAUSE:
                    self._paused = not self._paused
                    self._refresh_bar()
                    return 1
                if ctrl and shift and not alt and vk == HOTKEY_FINISH:
                    self._raw.put_nowait(("finish", "hotkey"))
                    return 1
                self._raw.put_nowait(("key", vk, ctrl, alt, shift, win, int(info.time)))
        except Exception:
            pass
        result: int = user32.CallNextHookEx(None, code, wparam, lparam)
        return result

    # ----------------------------------------------------------------- the bar
    def _create_bar(self) -> None:
        _register_bar_class()
        scale = 1.0
        with contextlib.suppress(AttributeError, OSError):
            scale = max(1.0, (user32.GetDpiForSystem() or 96) / 96)
        width, height = int(560 * scale), int(46 * scale)
        work = MONITORINFO()
        work.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(user32.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY),
                               ctypes.byref(work))
        left = work.rcWork.left + (work.rcWork.right - work.rcWork.left - width) // 2
        top = work.rcWork.top + int(8 * scale)
        ex = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if self._language == "ar":
            ex |= WS_EX_LAYOUTRTL
        module = kernel32.GetModuleHandleW(None)
        bar = user32.CreateWindowExW(ex, _BAR_CLASS, "معين - سجّلني",
                                     WS_POPUP | WS_BORDER, left, top, width, height,
                                     None, None, module, None)
        if not bar:
            raise ctypes.WinError(ctypes.get_last_error())
        self._bar = bar
        _bars[int(bar)] = self
        self._font = gdi32.CreateFontW(-int(14 * scale), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5,
                                       0, "Segoe UI")
        pad, gap = int(8 * scale), int(6 * scale)
        button_w, button_h = int(104 * scale), height - 2 * pad
        x = width - pad
        children: dict[int, Any] = {}
        for ident in (_ID_FINISH, _ID_UNDO, _ID_PAUSE):
            x -= button_w
            children[ident] = user32.CreateWindowExW(
                0, "BUTTON", "", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                x, pad, button_w, button_h, bar, wt.HMENU(ident), module, None)
            x -= gap
        children[0] = user32.CreateWindowExW(
            0, "STATIC", "", WS_CHILD | WS_VISIBLE | SS_CENTERIMAGE,
            pad + gap, pad, x - pad - gap, button_h, bar, None, module, None)
        self._bar_children = children
        for child in children.values():
            user32.SendMessageW(child, WM_SETFONT, self._font or 0, 1)
        with contextlib.suppress(AttributeError, OSError):
            # Not in the pictures: the bar is the recorder, not the procedure.
            user32.SetWindowDisplayAffinity(bar, WDA_EXCLUDEFROMCAPTURE)
        self._refresh_bar()
        user32.ShowWindow(bar, 4)  # SW_SHOWNOACTIVATE

    def _refresh_bar(self) -> None:
        if not self._bar:
            return
        text = _TEXT[self._language]
        status = text["paused" if self._paused else "rec"].format(n=self._steps)
        user32.SetWindowTextW(self._bar_children.get(0), status)
        user32.SetWindowTextW(self._bar_children.get(_ID_PAUSE),
                              text["resume" if self._paused else "pause"])
        user32.SetWindowTextW(self._bar_children.get(_ID_UNDO), text["undo"])
        user32.SetWindowTextW(self._bar_children.get(_ID_FINISH), text["finish"])

    def _bar_message(self, hwnd: Any, msg: int, wparam: int, lparam: int) -> int | None:
        if msg == WM_BAR_REFRESH:
            self._refresh_bar()
            return 0
        if msg == WM_COMMAND:
            ident = wparam & 0xFFFF
            if ident == _ID_PAUSE:
                self._paused = not self._paused
                self._refresh_bar()
            elif ident == _ID_UNDO:
                # Through the same line as the steps, so it undoes the step the
                # person saw last, not one still being described.
                self._raw.put_nowait(("undo",))
            elif ident == _ID_FINISH:
                self._raw.put_nowait(("finish", "bar"))
            return 0
        if msg == WM_NCHITTEST:
            hit: int = user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            return HTCAPTION if hit == HTCLIENT else hit   # drag it by its background
        if msg == WM_CTLCOLORSTATIC:
            gdi32.SetTextColor(wparam, _BAR_FG)
            gdi32.SetBkColor(wparam, _BAR_BG)
            if not self._brush:
                self._brush = gdi32.CreateSolidBrush(_BAR_BG)
            return int(self._brush or 0)
        if msg == WM_CLOSE:
            return 0   # only Finish closes it
        return None

    def _destroy_bar(self) -> None:
        if self._bar:
            _bars.pop(int(self._bar), None)
            with contextlib.suppress(OSError):
                user32.DestroyWindow(self._bar)
            self._bar = None
        for handle in (self._font, self._brush):
            if handle:
                with contextlib.suppress(OSError):
                    gdi32.DeleteObject(handle)

    # ---------------------------------------------------------- capture thread
    def _capture_loop(self) -> None:
        per_monitor_dpi()
        while not self._stop.is_set():
            try:
                item = self._raw.get(timeout=0.25)
            except queue.Empty:
                continue
            if item[0] == "stop":
                break
            try:
                self._capture(item)
            except Exception:
                logger.debug("screen guide: an input could not be captured", exc_info=True)

    def _capture(self, item: tuple[Any, ...]) -> None:
        programs = self._programs
        assert programs is not None
        kind = item[0]
        if kind == "undo":
            self._described.put({"kind": "undo"})
            return
        if kind == "finish":
            if self._finished:
                return
            self._finished = True
            self._described.put(self._final_frame(programs, source=item[1]))
            return
        if kind == "mouse":
            _, message, x, y, tick, origin = item
            button = "right" if message in (WM_RBUTTONDOWN, WM_RBUTTONUP) else "left"
            down = message in (WM_LBUTTONDOWN, WM_RBUTTONDOWN)
            if down:
                # The watch and the hook can both see one press (whether a program
                # with administrator rights reaches the hook varies): keep the first.
                other = self._last_down.get("watch" if origin == "hook" else "hook")
                self._last_down[origin] = (tick, x, y)
                if other is not None:
                    last_tick, lx, ly = other
                    if ((tick - last_tick) & 0xFFFFFFFF) < 400 and abs(lx - x) + abs(ly - y) < 12:
                        return
            if not down:
                if self._down_sent.pop(button, False):
                    self._described.put({"kind": "up", "button": button, "x": x, "y": y,
                                         "tick": tick})
                return
            if self._paused:
                return
            hwnd = user32.WindowFromPoint(wt.POINT(x, y))
            root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
            pid = window_pid(root)
            if programs.is_own(pid):
                return   # Moeen's own window or its bar
            rect = monitor_rect(point=(x, y))
            frame = grab(rect)   # FIRST: before the program reacts to the press
            self._typing_hwnd = None
            self._last_foreign = root
            self._down_sent[button] = True
            self._described.put({"kind": "click", "button": button, "x": x, "y": y,
                                 "tick": tick, "rect": rect, "frame": frame,
                                 "hwnd": hwnd})
            return
        if kind == "key":
            _, vk, ctrl, alt, shift, win, tick = item
            if self._paused or vk in _MODIFIERS:
                return
            foreground = user32.GetForegroundWindow()
            if not foreground or programs.is_own(window_pid(foreground)):
                return
            self._last_foreign = foreground
            shortcut = _shortcut(vk, ctrl=ctrl, alt=alt, shift=shift, win=win)
            if shortcut is None and vk == VK_RETURN:
                record = "enter"
            elif shortcut is not None:
                record = "shortcut"
            elif _typing_key(vk):
                if self._typing_hwnd is not None and self._typing_hwnd == foreground:
                    return   # still typing into the same field: one step
                record = "type"
            else:
                # Tab, arrows, Esc: moving around. The next typing is a new field.
                self._typing_hwnd = None
                return
            self._typing_hwnd = foreground if record == "type" else None
            rect = monitor_rect(hwnd=foreground)
            frame = grab(rect)
            caret = _caret(foreground)
            self._described.put({"kind": record, "keys": shortcut or "", "tick": tick,
                                 "rect": rect, "frame": frame, "hwnd": foreground,
                                 "caret": caret})

    def _final_frame(self, programs: Programs, *, source: str) -> dict[str, Any]:
        foreground = user32.GetForegroundWindow()
        window = foreground
        if not foreground or programs.is_own(window_pid(foreground)):
            # Finished from Moeen's own window: that is not where the journey ended.
            return {"kind": "finish", "frame": None, "hwnd": self._last_foreign}
        rect = monitor_rect(hwnd=window)
        return {"kind": "finish", "frame": grab(rect), "rect": rect, "hwnd": window,
                "source": source}

    # --------------------------------------------------------- describe thread
    def _describe_loop(self) -> None:
        per_monitor_dpi()
        initialised = ole32.CoInitializeEx(None, COINIT_MULTITHREADED) >= 0
        try:
            while not self._stop.is_set():
                try:
                    item = self._described.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is None:
                    break
                try:
                    event = self._describe(item)
                except Exception:
                    logger.debug("screen guide: a step could not be described", exc_info=True)
                    event = _bare(item)
                if event is not None:
                    self._emit(event)
        finally:
            if initialised:
                ole32.CoUninitialize()

    def _describe(self, item: dict[str, Any]) -> ScreenEvent | None:
        programs = self._programs
        assert programs is not None
        kind = item["kind"]
        if kind == "undo":
            return ScreenEvent(kind="undo")
        if kind == "up":
            return ScreenEvent(kind="up", at_ms=item["tick"], button=item["button"],
                               screen_point=(float(item["x"]), float(item["y"])))
        hwnd = item.get("hwnd")
        window = programs.window(hwnd) if hwnd else ScreenWindow()
        rect = item.get("rect")
        if kind == "finish":
            return ScreenEvent(kind="finish", frame=item.get("frame"), window=window)
        found: dict[str, Any] | None = None
        point: tuple[float, float] | None = None
        screen_point: tuple[float, float] | None = None
        if kind == "click":
            x, y = item["x"], item["y"]
            screen_point = (float(x), float(y))
            if not window.protected:
                found = element_at(x, y)
        else:
            caret = item.get("caret")
            if not window.protected:
                if caret is not None:
                    found = element_at(*caret)
                if not found or found.get("role") not in ("textbox", "combobox", "cell"):
                    focus = _focus_window(hwnd)
                    found = focused_element(focus) or found
            if caret is not None:
                screen_point = (float(caret[0]), float(caret[1]))
        target = _target(found, rect)
        if screen_point is not None and rect is not None:
            point = (screen_point[0] - rect.left, screen_point[1] - rect.top)
        elif target.box is not None:
            bx, by, bw, bh = target.box
            point = (bx + bw / 2, by + bh / 2)
        return ScreenEvent(
            kind=kind, at_ms=int(item.get("tick") or 0), button=item.get("button", "left"),
            point=point, screen_point=screen_point, keys=item.get("keys", ""),
            frame=item.get("frame"), target=target, window=window,
        )

    # ------------------------------------------------------------ watch thread
    def _watch_loop(self) -> None:
        """Mouse buttons, while a program with administrator rights is in front."""
        per_monitor_dpi()
        programs = self._programs
        assert programs is not None
        pressed = {"left": False, "right": False}
        while not self._stop.wait(0.025):
            try:
                foreground = user32.GetForegroundWindow()
                protected = bool(foreground) and programs.program(window_pid(foreground))[2]
                for button, vk, down_msg, up_msg in (
                    ("left", VK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP),
                    ("right", VK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP),
                ):
                    now = _down(vk)
                    if now != pressed[button] and protected:
                        point = wt.POINT()
                        user32.GetCursorPos(ctypes.byref(point))
                        self._raw.put_nowait(("mouse", down_msg if now else up_msg,
                                              int(point.x), int(point.y),
                                              int(kernel32.GetTickCount()), "watch"))
                    pressed[button] = now
            except Exception:
                logger.debug("screen guide: watch failed", exc_info=True)


# ------------------------------------------------------------------ helpers
def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _typing_key(vk: int) -> bool:
    return (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A or 0x60 <= vk <= 0x6F
            or vk == 0x20 or 0xBA <= vk <= 0xC0 or 0xDB <= vk <= 0xDF or vk == 0xE2)


def _key_name(vk: int) -> str:
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if VK_F1 <= vk <= 0x87:
        return f"F{vk - VK_F1 + 1}"
    if 0x60 <= vk <= 0x69:
        return f"Num {vk - 0x60}"
    return _KEY_NAMES.get(vk, f"0x{vk:02X}")


def _shortcut(vk: int, *, ctrl: bool, alt: bool, shift: bool, win: bool) -> str | None:
    """A key a reader is told to press, by name - or None for ordinary typing."""
    if ctrl and alt and not win and _typing_key(vk):
        return None   # AltGr: a character, not a shortcut
    if alt and not ctrl and vk in (VK_TAB, VK_ESCAPE):
        return None   # switching programs is how the person moves, not a step
    function_key = VK_F1 <= vk <= VK_F12
    if not (ctrl or alt or win or function_key):
        return None
    parts = [name for flag, name in ((ctrl, "Ctrl"), (alt, "Alt"), (shift, "Shift"),
                                     (win, "Win")) if flag]
    return "+".join([*parts, _key_name(vk)])


def _caret(foreground: Any) -> tuple[int, int] | None:
    """Where the text cursor is, on the screen - a field that has the keyboard."""
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    thread = user32.GetWindowThreadProcessId(foreground, None)
    if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.hwndCaret:
        return None
    rect = info.rcCaret
    if rect.right <= rect.left and rect.bottom <= rect.top:
        return None
    point = wt.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    user32.ClientToScreen(info.hwndCaret, ctypes.byref(point))
    return int(point.x), int(point.y)


def _focus_window(foreground: Any) -> Any:
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    thread = user32.GetWindowThreadProcessId(foreground, None)
    if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return foreground
    return info.hwndFocus or foreground


def _target(found: dict[str, Any] | None, rect: wt.RECT | None) -> ScreenTarget:
    if not found:
        return ScreenTarget()
    box = None
    raw = found.get("box")
    if raw and rect is not None:
        x, y, w, h = raw
        box = (float(x - rect.left), float(y - rect.top), float(w), float(h))
    return ScreenTarget(
        name=str(found.get("name") or ""),
        role=found.get("role"),
        box=box,
        checked=found.get("checked"),
        secret=bool(found.get("secret")),
        editable=bool(found.get("editable")),
    )


def _bare(item: dict[str, Any]) -> ScreenEvent | None:
    """An event whose description failed: still a step, named by its window."""
    kind = item.get("kind")
    if kind in (None, "up"):
        return None
    rect = item.get("rect")
    point = None
    if "x" in item and rect is not None:
        point = (float(item["x"] - rect.left), float(item["y"] - rect.top))
    return ScreenEvent(kind=kind, at_ms=int(item.get("tick") or 0),
                       button=item.get("button", "left"), point=point,
                       keys=item.get("keys", ""), frame=item.get("frame"))


def _own_top_windows() -> list[Any]:
    own = os.getpid()
    found: list[Any] = []

    @EnumWindowsProc
    def visit(hwnd: Any, _: int) -> bool:
        if (window_pid(hwnd) == own and user32.IsWindowVisible(hwnd)
                and not user32.IsIconic(hwnd) and window_class(hwnd) != _BAR_CLASS
                and window_text(hwnd)):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def _minimise_own_windows() -> list[Any]:
    windows = _own_top_windows()
    for hwnd in windows:
        # Async: the window belongs to the interface's thread, which must never be
        # made to wait on the recorder (nor the recorder on it).
        user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
    return windows


def _restore_windows(windows: list[Any]) -> None:
    for hwnd in windows:
        with contextlib.suppress(OSError):
            user32.ShowWindowAsync(hwnd, SW_RESTORE)
    if windows:
        with contextlib.suppress(OSError):
            user32.SetForegroundWindow(windows[0])


def wait_until(predicate: Any, timeout: float = 5.0) -> bool:
    """Test aid: poll until true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False
