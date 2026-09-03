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
import pathlib
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


# ── حارس ضد تكرار العطل في أداة جديدة ────────────────────────────────

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY_POINTS = sorted(
    [p for p in (ROOT / "tools").glob("*.py") if p.name != "__init__.py"]
    + [ROOT / "app.py"]
)


@pytest.mark.parametrize("path", ENTRY_POINTS, ids=lambda p: p.name)
def test_every_cli_entry_point_enables_utf8_console(path):
    """كل أداة سطر أوامر تستدعي enable_utf8_console.

    الإصلاح الأول غطّى doctor.py وrun_pipeline.py وapp.py فقط، فانتقل
    العطل نفسه إلى make_test_corpus.py في التشغيل التالي لـ CI. الرسائل
    هنا عربية في كل مكان، فالقاعدة تخصّ كل نقطة دخول لا بعضها — وهذا
    الاختبار يمنع أن تولد أداة جديدة بالثغرة نفسها.
    """
    source = path.read_text(encoding="utf-8")
    if '__name__ == "__main__"' not in source:
        pytest.skip("ليست نقطة دخول تُشغَّل مباشرة")
    assert "enable_utf8_console()" in source, (
        f"{path.name}: نقطة دخول بلا enable_utf8_console() — "
        "ستنهار على طرفية ويندوز غير UTF-8 عند أول رسالة عربية"
    )
