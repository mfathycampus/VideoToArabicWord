"""منع نوم النظام أثناء المعالجة — وألّا يُسقط فشلُه المعالجة.

محاضرة ثلاث ساعات تُفرَّغ في نحو ساعة، والمعلّم يترك الحاسوب أثناءها
(ولا شيء يستدعي جلوسه). سياسة الطاقة الافتراضية في ويندوز تُنيم الجهاز
بعد نحو ثلاثين دقيقة، فيقف التنفيذ بلا خطأ ولا تفسير.
"""
from __future__ import annotations

import sys
import types

import pytest

from utils.power import _ES_CONTINUOUS, _ES_SYSTEM_REQUIRED, keep_awake


class _Kernel32:
    def __init__(self, result=1, raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def SetThreadExecutionState(self, flags):     # noqa: N802 (اسم Win32)
        if self._raises:
            raise self._raises
        self.calls.append(flags)
        return self._result


@pytest.fixture()
def windows(monkeypatch):
    """يُظهر النظام كأنّه ويندوز ويحقن kernel32 مزيّفًا."""
    def _install(kernel):
        monkeypatch.setattr(sys, "platform", "win32")
        fake = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel))
        monkeypatch.setitem(sys.modules, "ctypes", fake)
        return kernel
    return _install


def test_sleep_is_blocked_then_restored(windows):
    kernel = windows(_Kernel32())
    with keep_awake("معالجة محاضرة.mp4"):
        assert kernel.calls == [_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED]
    assert kernel.calls[-1] == _ES_CONTINUOUS, "لم تُعَد سياسة الطاقة"


def test_policy_is_restored_even_when_the_body_raises(windows):
    """المعالجة تفشل أحيانًا؛ الجهاز يجب ألّا يبقى ممنوعًا من النوم."""
    kernel = windows(_Kernel32())
    with pytest.raises(RuntimeError):
        with keep_awake():
            raise RuntimeError("فشلت المرحلة")
    assert kernel.calls[-1] == _ES_CONTINUOUS


def test_screen_is_not_kept_on(windows):
    """‏ES_DISPLAY_REQUIRED غير مطلوب: إبقاء الشاشة مضاءة ساعةً إهدار."""
    kernel = windows(_Kernel32())
    with keep_awake():
        pass
    assert kernel.calls[0] & 0x00000002 == 0


def test_a_refusing_system_does_not_break_processing(windows):
    """‏SetThreadExecutionState تُعيد صفرًا عند الرفض."""
    windows(_Kernel32(result=0))
    with keep_awake():
        pass                                  # لا استثناء


def test_a_throwing_api_does_not_break_processing(windows):
    """منع النوم راحة لا شرط صحّة: فشله لا يُسقط معالجة ناجحة."""
    windows(_Kernel32(raises=OSError("رفض النظام")))
    with keep_awake():
        pass


def test_non_windows_is_a_transparent_passthrough(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    entered = []
    with keep_awake("x"):
        entered.append(True)
    assert entered == [True]
