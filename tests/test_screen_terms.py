"""اقتراح مصطلحات التفريغ من نصّ الشاشة.

كل نصّ في هذا الملف مأخوذ من مخرَج Tesseract الفعلي على تسجيل حقيقي
مدّته 88 ثانية، ومن التفريغ الذي صحّحه صاحبه بيده.
"""
from __future__ import annotations

import pytest

from video.screen_terms import (
    MAX_GLOSSARY_CHARS,
    as_glossary,
    scan_video,
    terms_from_screen_text,
)

#: أسطر شاشة حقيقية من التسجيل — بينها التسمية التي أخطأ فيها التفريغ.
SCREENS = [
    "Current Student Selection\nModify Course Schedule\nDrop Classes",
    "Daily Bulletin\nModify Course Schedule\nQuick Lookup",
    "Drop Classes\nDaily Bulletin",
    "Drop Classes\nDaily Bulletin",
    "Drop Classes",
]


def _named(terms):
    return [phrase for phrase, _count in terms]


def test_the_term_the_transcript_mangled_is_found():
    """«Modify Course Schedule» خرجت من التفريغ «مضيفه كورس سكيدور»."""
    assert "Modify Course Schedule" in _named(terms_from_screen_text(SCREENS))


def test_the_term_the_transcript_dropped_is_found():
    """«Drop Classes» سقطت من التفريغ تمامًا."""
    assert "Drop Classes" in _named(terms_from_screen_text(SCREENS))


def test_the_most_repeated_term_comes_first():
    """التكرار على الشاشة دليلُ أنها تسمية واجهة لا قراءة عابرة.

    ‏«Drop Classes» أربعًا و«Daily Bulletin» ثلاثًا و«Modify Course
    Schedule» مرّتين — وهي النسب التي خرجت من التسجيل الحقيقي.
    """
    assert _named(terms_from_screen_text(SCREENS))[0] == "Drop Classes"


def test_phrases_are_kept_whole():
    """«Modify Course Schedule» تُنطق ككتلة ويخطئ فيها النموذج ككتلة."""
    terms = _named(terms_from_screen_text(["Modify Course Schedule"]))
    assert terms == ["Modify Course Schedule"]
    assert "Modify" not in terms


def test_one_off_readings_are_dropped_when_repeats_exist():
    """«House» و«Quick Data» ظهرا مرّة على التسجيل الحقيقي — ضوضاء
    تزاحم المفيد على سقف الحروف."""
    screens = SCREENS + ["House\nQuick Data\nPostsecondary"]
    terms = _named(terms_from_screen_text(screens))
    assert "House" not in terms
    assert "Drop Classes" in terms


def test_singletons_survive_when_nothing_repeats():
    """تسجيلٌ قصير قد لا يكرّر شيئًا؛ اقتراحٌ ضعيف خير من لا شيء."""
    terms = _named(terms_from_screen_text(["Alpha Beta\nGamma Delta"]))
    assert terms


@pytest.mark.parametrize("noise", [
    "File Edit View Help",
    "Bi Lessons | Chalk - Google Chrome",
    "https://maarif-education.chalk.com/lessons",
    "August 25 2024",
])
def test_interface_chrome_is_not_a_term(noise):
    """أسماء المتصفّحات والقوائم العامّة والتواريخ لا تفيد التفريغ."""
    assert not _named(terms_from_screen_text([noise, noise]))


def test_short_tokens_are_ignored():
    """«OK» و«Go» لا يخطئ فيهما نموذج ولا يستحقّان مكانًا."""
    assert not _named(terms_from_screen_text(["OK Go", "OK Go"]))


def test_digits_are_not_terms():
    """«Section 3» بيان صفحة لا مصطلح."""
    terms = _named(terms_from_screen_text(["Section 3", "Section 3"]))
    assert terms == ["Section"] or terms == []


# ── بناء نصّ القاموس ──────────────────────────────────────────────────

def test_glossary_is_built_in_rank_order():
    assert as_glossary(terms_from_screen_text(SCREENS)).startswith(
        "Drop Classes")


def test_glossary_respects_the_hotwords_limit():
    """السقف يطابق ``_HOTWORDS_CHAR_LIMIT`` في المفرِّغ: ما يتجاوزه
    يُقتطع هناك، فاقتطاعه هنا على حدّ عبارة أنظف."""
    many = [(f"Term Number {i:03d}", 5) for i in range(200)]
    glossary = as_glossary(many)
    assert len(glossary) <= MAX_GLOSSARY_CHARS
    assert not glossary.endswith("،")
    # لا عبارة مبتورة
    for phrase in glossary.split("، "):
        assert phrase in [p for p, _ in many]


def test_an_empty_scan_yields_an_empty_glossary():
    assert as_glossary([]) == ""


# ── المسح نفسه ────────────────────────────────────────────────────────

class _FFmpeg:
    def __init__(self, frames=3):
        self.frames = frames
        self.calls = []

    def extract_frames_evenly(self, video, out_dir, count, duration,
                              width=None):
        self.calls.append((count, duration, width))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(self.frames):
            path = out_dir / f"scan_{i:04d}.jpg"
            path.write_bytes(b"")
            paths.append(path)
        return paths


def test_scan_is_skipped_without_tesseract(monkeypatch, tmp_path):
    """اقتراحٌ مساعد: غيابه لا يمنع المعالجة ولا يرفع خطأ."""
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    assert scan_video(tmp_path / "v.mp4", _FFmpeg(), 60.0) == []


def test_scan_reads_every_frame(monkeypatch, tmp_path):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text",
                        lambda _p: "Drop Classes\nDaily Bulletin")

    ffmpeg = _FFmpeg(frames=4)
    terms = scan_video(tmp_path / "v.mp4", ffmpeg, 120.0)
    assert "Drop Classes" in _named(terms)
    assert ffmpeg.calls[0][2] == 1280, "لم تُصغَّر الإطارات قبل OCR"


def test_a_failing_frame_does_not_abort_the_scan(monkeypatch, tmp_path):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    seen = {"n": 0}

    def _flaky(_path):
        seen["n"] += 1
        if seen["n"] == 2:
            raise OSError("إطار تالف")
        return "Drop Classes"

    monkeypatch.setattr(ocr, "extract_text", _flaky)
    terms = scan_video(tmp_path / "v.mp4", _FFmpeg(frames=4), 120.0)
    assert "Drop Classes" in _named(terms)


def test_a_failing_extraction_returns_nothing_not_an_error(monkeypatch,
                                                           tmp_path):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    class _Broken:
        def extract_frames_evenly(self, *a, **k):
            raise RuntimeError("ffmpeg غير موجود")

    assert scan_video(tmp_path / "v.mp4", _Broken(), 60.0) == []


def test_frame_count_is_capped_for_long_lectures(monkeypatch, tmp_path):
    """محاضرة ثلاث ساعات لا تُمسح إطارًا كل ستّ ثوانٍ."""
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text", lambda _p: "")

    ffmpeg = _FFmpeg(frames=1)
    scan_video(tmp_path / "v.mp4", ffmpeg, 3 * 3600.0)
    assert ffmpeg.calls[0][0] <= 14


def test_progress_is_reported(monkeypatch, tmp_path):
    import video.ocr as ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text", lambda _p: "")

    seen = []
    scan_video(tmp_path / "v.mp4", _FFmpeg(frames=3), 60.0,
               progress=lambda done, total: seen.append((done, total)))
    assert seen, "المسح يستغرق نحو دقيقة ومرّ بلا إشارة تقدّم"
