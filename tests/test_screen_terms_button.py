"""زرّ «اقترح مصطلحات من الشاشة» — من الضغطة إلى بدء المسح.

سبب وجود هذا الملف عطلٌ بلغه المستخدم: «عند الضغط على اقتراحات مصطلحات
من الشاشة لا يعمل». وكان الخطأ ``'WindowsPath' object has no attribute
'get'`` — أي أن الزرّ **لم يعمل ولا مرّة واحدة** منذ كُتب.

والسبب المباشر أن ``extract_video_facts`` تأخذ خرج ffprobe (قاموسًا) لا
مسارًا، وتُعيد قاموسًا لا كائنًا. الاستدعاء الصحيح كان موجودًا في
``_refresh_estimate`` على بُعد مئتي سطر، ونسخةٌ ثانية كُتبت خطأً بلا
اختبار يمسّها. الدرس نفسه الذي أنتج ``document/titles.py``: تعريفان
لشيء واحد يفترقان بصمت.

فالاختبارات هنا تمرّ بالمسار كاملًا: ضغطة زرّ على ملف حقيقي على القرص،
لا استدعاء دالّة مساعدة.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QThread  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from config.settings import AppConfig  # noqa: E402

#: خرج ffprobe مختصرًا لملف حقيقي — بالشكل الذي تنتظره
#: ``extract_video_facts`` بالضبط.
PROBE = {
    "format": {"duration": "88.04"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280,
         "height": 720, "avg_frame_rate": "30/1", "nb_frames": "2641"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
    ],
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.probe_raw", lambda _path: PROBE)
    win = MainWindow(AppConfig())
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"\x00" * 32)
    win.video_path = source
    win.audio_only = False
    return win


class _Complaints:
    """يلتقط كل حوار يظهر للمستخدم بدل أن يحجب الاختبار."""

    def __init__(self, monkeypatch):
        self.seen: list[tuple[str, str]] = []
        for name in ("warning", "information", "critical"):
            monkeypatch.setattr(
                QMessageBox, name,
                lambda _p, title, text, *a, _s=self, **k:
                    _s.seen.append((title, text)))

    def titles(self) -> list[str]:
        return [title for title, _text in self.seen]


@pytest.fixture
def no_thread(monkeypatch):
    """يمنع إطلاق خيط حقيقي: المطلوب أنه *بلغ* البدء لا أنه مسح."""
    started = {"yes": False}

    # ‏QThread حقيقي مع ``start`` معطّل: ``moveToThread`` ترفض أي بديل
    # ليس QThread، وتعطيل الإطلاق وحده يكفي — المطلوب أن المسار بلغ
    # البدء لا أن المسح جرى.
    class _Thread(QThread):
        def start(self, *args, **kwargs):
            started["yes"] = True

    monkeypatch.setattr("ui.main_window.QThread", _Thread)
    return started


@pytest.fixture
def tesseract(monkeypatch):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "refresh", lambda: "/usr/bin/tesseract")


# ── المدة: الاستدعاء الذي كان خطأً ────────────────────────────────────

def test_the_duration_helper_returns_seconds(window):
    assert window._source_duration_seconds() == pytest.approx(88.04)


def test_the_helper_is_given_probe_output_not_a_path(window, monkeypatch):
    """الحارس المباشر على العطل: مسارٌ يصل إلى ``extract_video_facts``
    يعني عودة ``'WindowsPath' object has no attribute 'get'``."""
    seen = {}

    def _spy(data, allow_audio_only=False):
        seen["data"] = data
        return {"duration_seconds": 88.04}

    monkeypatch.setattr("ui.main_window.extract_video_facts", _spy)
    window._source_duration_seconds()
    assert isinstance(seen["data"], dict), "مُرّر مسار لا خرج ffprobe"


# ── الضغطة نفسها ──────────────────────────────────────────────────────

def test_pressing_the_button_starts_the_scan(window, monkeypatch, tesseract,
                                             no_thread):
    complaints = _Complaints(monkeypatch)
    window._scan_screen_terms()
    assert not complaints.titles(), f"شكوى بلا سبب: {complaints.seen}"
    assert no_thread["yes"], "لم يبدأ المسح"


def test_pressing_the_button_does_not_raise(window, monkeypatch, tesseract,
                                            no_thread):
    """أوضح صياغة للعطل كما رآه المستخدم: ضغطة تُخرج صندوق خطأ."""
    _Complaints(monkeypatch)
    window._scan_screen_terms()          # لا استثناء


def test_an_unreadable_file_is_reported_not_crashed(window, monkeypatch,
                                                    tesseract, no_thread):
    from utils.media_probe import MediaValidationError

    def _broken(_path):
        raise MediaValidationError("الملف تالف")

    monkeypatch.setattr("ui.main_window.probe_raw", _broken)
    complaints = _Complaints(monkeypatch)
    window._scan_screen_terms()
    assert "تعذّر قراءة الملف" in complaints.titles()
    assert not no_thread["yes"]


def test_an_audio_file_is_told_it_has_no_screen(window, monkeypatch,
                                                tesseract, no_thread):
    """مسح أربعة عشر إطارًا سوداء ثم قول «لم يُعثر على مصطلحات» جوابٌ
    صحيح لسؤال خاطئ."""
    window.audio_only = True
    complaints = _Complaints(monkeypatch)
    window._scan_screen_terms()
    assert "ملف صوتي" in complaints.titles()
    assert not no_thread["yes"]


def test_no_file_no_scan(window, monkeypatch, tesseract, no_thread):
    window.video_path = None
    complaints = _Complaints(monkeypatch)
    window._scan_screen_terms()
    assert "لا ملف" in complaints.titles()
    assert not no_thread["yes"]


def test_a_missing_tesseract_is_explained(window, monkeypatch, no_thread):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "refresh", lambda: None)
    complaints = _Complaints(monkeypatch)
    window._scan_screen_terms()
    assert not no_thread["yes"]
    assert complaints.seen, "غياب Tesseract مرّ بلا تفسير"
