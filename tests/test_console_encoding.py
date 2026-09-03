"""انحدار: الأدوات تطبع العربية على طرفية ويندوز غير UTF-8.

العطل الأصلي: ‏`python tools/doctor.py` على متتبّع windows-latest في CI
انهار بـ ``UnicodeEncodeError: 'charmap' codec can't encode characters``
لأن صفحة الترميز النشطة ``cp1252`` لا تحوي حرفًا عربيًا. الأمر نفسه هو
الخطوة الثالثة في README و‏INSTALL، فكان أي مستخدم ويندوز بإعداد
إنجليزي افتراضي يصطدم به قبل أن يشغّل التطبيق أصلًا.

الاختبار يبني مجرى ``cp1252`` حقيقيًا — لا محاكاة — ويؤكّد أمرين:
العطل يقع فعلًا بلا الإصلاح، ولا يقع بعده.
"""
import io
import sys

import pytest

from utils.console import enable_utf8_console

ARABIC = "فحص البيئة: جاهز ✔"


def _cp1252_stream() -> io.TextIOWrapper:
    """مجرى نصّي فوق بايتات، بترميز ويندوز الإنجليزي الافتراضي."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


def test_cp1252_stream_really_rejects_arabic():
    """بلا الإصلاح، الطباعة تنهار — وإلا فالاختبار التالي بلا معنى."""
    stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        stream.write(ARABIC)
        stream.flush()


def test_enable_utf8_console_survives_cp1252(monkeypatch):
    stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252_stream())

    enable_utf8_console()
    print(ARABIC)              # كان ينهار هنا
    sys.stdout.flush()

    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert ARABIC in stream.buffer.getvalue().decode("utf-8")


def test_enable_utf8_console_tolerates_streams_without_reconfigure(monkeypatch):
    """مجرى بديل في بيئة اختبار لا يملك reconfigure — لا يُسقط الأداة."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    enable_utf8_console()      # لا استثناء
