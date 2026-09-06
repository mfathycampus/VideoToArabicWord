"""ملفّ يحتاج Tesseract وهو غائب — يُقال عند الاختيار لا بعد المعالجة.

وقع هذا على تشغيل فعلي: اختار المستخدم «شرح برنامج»، وانتظر المعالجة
كاملة، ثم لم يجد تحت الصور الثلاث إلا «لقطة عند 00:00:17»، وبوابة
الجودة تقول ``figure_captioning = 0٪``. والسبب كان سطرًا في السجلّ:
«OCR مفعَّل في الإعداد لكن Tesseract غير مثبَّت» — والمعلّم لا يقرأ
السجلّات.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# انظر تعليق ``tests/test_ui_layout.py``: الحارس على الوحدة الفعلية.
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from config.profiles import PROFILES  # noqa: E402
from config.settings import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(app, monkeypatch, request):
    """نافذة مع حالة Tesseract مضبوطة (``available`` من المُعلَّم)."""
    available = getattr(request, "param", False)
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: available)
    monkeypatch.setattr(ocr, "install_hint",
                        lambda: "sudo apt install tesseract-ocr-ara")

    from ui.main_window import MainWindow
    win = MainWindow(AppConfig())
    yield win
    win.close()


def _select(window, key):
    window.profile_combo.setCurrentIndex(window.profile_combo.findData(key))


@pytest.mark.parametrize("window", [False], indirect=True)
def test_choosing_screencast_without_tesseract_warns(window):
    _select(window, "screencast")
    assert window.profile_notice.isVisibleTo(window.profile_notice.parent())
    text = window.profile_notice.text()
    assert "Tesseract" in text
    assert "tesseract-ocr-ara" in text, "التحذير بلا طريقة تثبيت"


@pytest.mark.parametrize("window", [False], indirect=True)
def test_the_warning_says_processing_still_works(window):
    """ليس منعًا: المستند يخرج صحيحًا، وينقصه وصف الصور فقط."""
    _select(window, "screencast")
    assert "المعالجة تعمل" in window.profile_notice.text()


@pytest.mark.parametrize("window", [False], indirect=True)
def test_profiles_that_do_not_need_ocr_say_nothing(window):
    for key in ("slides", "whiteboard", "talking_head"):
        _select(window, key)
        assert not window.profile_notice.isVisible(), key


@pytest.mark.parametrize("window", [True], indirect=True)
def test_no_warning_when_tesseract_is_installed(window):
    _select(window, "screencast")
    assert not window.profile_notice.isVisible()


def test_only_the_screencast_profile_enables_ocr():
    """يحرس افتراض التحذير: لو فُعِّل OCR في ملفّ آخر لزم تحديثه."""
    enabling = {key for key, profile in PROFILES.items()
                if profile.overrides.get("frames", {}).get("enable_ocr")}
    assert enabling == {"screencast"}
