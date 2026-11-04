"""1.20.0 (مراجعة خارجية): فشل القراءة أو الطمس لا يَسِم اللقطة «آمنة»."""
import json
from pathlib import Path

import pytest

from ai.providers import ProviderInfo

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
VIDEO = CORPUS / "h264_mp4.mp4"


def _need_corpus():
    if not VIDEO.exists():
        pytest.skip("testdata/corpus غير مولَّد")


# ── إشارة الفشل في OCR ───────────────────────────────────────────────
def test_ocr_failure_is_distinguishable_from_empty_text(tmp_path, monkeypatch):
    import video.ocr as ocr

    monkeypatch.setattr(ocr, "_ocr_tsv", lambda path, timeout: None)
    result = ocr.extract_text_and_words(tmp_path / "x.jpg")
    assert result.failed and tuple(result) == ("", [])
    text = ocr.extract_text(tmp_path / "x.jpg")
    assert text == "" and text.failed

    monkeypatch.setattr(ocr, "_ocr_tsv", lambda path, timeout: ("", 1.0))
    assert not ocr.extract_text_and_words(tmp_path / "x.jpg").failed
    assert not ocr.extract_text(tmp_path / "x.jpg").failed


def test_redact_keyframe_strict_raises_on_blur_failure(tmp_path, monkeypatch):
    import video.redact as redact

    monkeypatch.setattr(redact, "find_name_boxes",
                        lambda words: ([{"left": 0, "top": 0, "width": 9, "height": 9}],
                                       ["Afnan"]))

    def boom(*a, **k):
        raise OSError("قرص للقراءة فقط")

    monkeypatch.setattr(redact, "blur_boxes", boom)
    text, names = redact.redact_keyframe(tmp_path / "a.jpg", [], "Afnan 9")
    assert names == ["Afnan"]                              # غير الصارم يبتلع
    with pytest.raises(OSError):
        redact.redact_keyframe(tmp_path / "a.jpg", [], "Afnan 9", strict=True)


# ── داخل خطّ المعالجة ────────────────────────────────────────────────
def _pipeline(out, **document):
    from config.settings import AppConfig
    from core.pipeline import VideoToDocPipeline
    from tests.test_corpus_matrix import StubModelManager, StubTranscriber

    config = AppConfig()
    for key, value in document.items():
        setattr(config.document, key, value)
    return VideoToDocPipeline(out, config, transcriber=StubTranscriber(),
                              model_manager=StubModelManager())


def _keyframes(pipeline):
    job_dir = pipeline.job_dir_for(VIDEO)
    return json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8")
                      )["keyframes"]


def test_unreadable_frames_stay_unscanned(tmp_path, monkeypatch):
    """مهلة Tesseract كانت تُنتج «مفحوصة ومموّهة» بلا قراءة."""
    _need_corpus()
    import video.ocr as ocr

    failed = ocr.OcrResult(("", []))
    failed.failed = True
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text_and_words", lambda p, timeout_seconds=20.0: failed)
    pipeline = _pipeline(tmp_path / "out")
    pipeline.run(VIDEO)
    frames = _keyframes(pipeline)
    assert frames
    assert not any(k["privacy_scanned"] or k["redacted"] for k in frames)


def test_unreadable_frames_stay_unscanned_without_anonymize(tmp_path, monkeypatch):
    _need_corpus()
    import video.ocr as ocr

    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text", lambda p, timeout_seconds=20.0: ocr._failed_text())
    pipeline = _pipeline(tmp_path / "out", anonymize_names=False)
    pipeline.run(VIDEO)
    assert not any(k["privacy_scanned"] for k in _keyframes(pipeline))


def test_failed_blur_is_not_marked_redacted_and_is_withheld(tmp_path, monkeypatch):
    _need_corpus()
    import video.ocr as ocr
    import video.redact as redact

    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "extract_text_and_words",
                        lambda p, timeout_seconds=20.0: ("Afnan Alharbi", []))
    monkeypatch.setattr(redact, "find_name_boxes",
                        lambda words: ([{"left": 0, "top": 0, "width": 9, "height": 9}],
                                       ["Afnan Alharbi"]))

    def boom(*a, **k):
        raise OSError("تعذّر حفظ الصورة")

    monkeypatch.setattr(redact, "blur_boxes", boom)
    pipeline = _pipeline(tmp_path / "out")
    result = pipeline.run(VIDEO)
    frames = _keyframes(pipeline)
    assert frames and all(k["redaction_failed"] and not k["redacted"] for k in frames)
    assert all("Afnan" not in k["ocr_text"] for k in frames)   # النصّ نُقّي

    plan = json.loads((pipeline.job_dir_for(VIDEO) / "plan.json").read_text(encoding="utf-8"))
    used = {b.get("image_filename") for s in plan["sections"] for b in s["blocks"]}
    assert not used & {k["filename"] for k in frames}
    import zipfile
    media = [n for n in zipfile.ZipFile(result).namelist() if n.startswith("word/media/")]
    assert len(media) <= 1                       # شعار الغلاف وحده، لا لقطة


# ── منع الإرسال ──────────────────────────────────────────────────────
def test_withhold_only_does_not_send_unscanned_images(tmp_path, monkeypatch):
    """حجب شاشات الدخول وحده كان لا يمنع إرسال لقطاتٍ لم تُقرأ."""
    from ai import providers
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin
    from tests.test_visual_author import REPLY, FakeVision, _frames, _transcript

    frames = _frames(tmp_path, 2)
    fake = FakeVision([{"title": "قسم", "summary": "", "paragraphs": ["فقرة."]}] * 5)
    fake.info = ProviderInfo(name="anthropic", label_ar="x", is_local=False,
                             privacy_note="")
    monkeypatch.setattr(providers, "provider_from_settings", lambda s: fake)

    class Host(OutputsMixin):
        def __init__(self):
            self.config = AppConfig()
            self.config.rewrite.enabled = True
            self.config.document.anonymize_names = False
            self.config.document.withhold_sensitive_screens = True

    class Meta:
        has_video = True
        duration_seconds = 60.0

    plan = Host()._build_plan(_transcript([(1.0, "نص")]), frames,
                              tmp_path / "ملف.mp4", Meta(), lambda *a: None,
                              images_dir=tmp_path)
    assert not plan.generated_by.endswith("+vision")
    assert all(isinstance(call[1], str) for call in fake.calls)

    for frame in frames:
        frame.privacy_scanned = True
    fake2 = FakeVision([REPLY])
    fake2.info = fake.info
    monkeypatch.setattr(providers, "provider_from_settings", lambda s: fake2)
    plan = Host()._build_plan(_transcript([(1.0, "نص")]), frames,
                              tmp_path / "ملف.mp4", Meta(), lambda *a: None,
                              images_dir=tmp_path)
    assert plan.generated_by.endswith("+vision")
