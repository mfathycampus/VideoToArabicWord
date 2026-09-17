"""OCR داخل الـ pipeline الحقيقي: التفعيل من الإعداد، الاستمرار الآمن
عند غياب Tesseract، والحفظ الصحيح في keyframes.json."""
import json
from pathlib import Path

from config.settings import AppConfig
from core.pipeline import VideoToDocPipeline
from tests.test_corpus_matrix import StubModelManager, StubTranscriber

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
VIDEO = CORPUS / "h264_mp4.mp4"


def make(out: Path, enable_ocr: bool = False,
         show_screen_text: bool = False) -> VideoToDocPipeline:
    config = AppConfig()
    config.frames.enable_ocr = enable_ocr
    config.document.show_screen_text = show_screen_text
    return VideoToDocPipeline(out, config,
                              transcriber=StubTranscriber(),
                              model_manager=StubModelManager())


def test_ocr_disabled_by_default_leaves_ocr_text_empty(tmp_path):
    pipeline = make(tmp_path / "out", enable_ocr=False)
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert saved["keyframes"], "لا لقطات في هذا الفيديو الاختباري"
    assert all(k["ocr_text"] == "" for k in saved["keyframes"])


def test_ocr_enabled_calls_extract_text_and_persists_result(tmp_path, monkeypatch):
    calls = []

    def fake_extract_text(image_path, timeout_seconds=20.0):
        calls.append(Path(image_path).name)
        return "نص وهمي من OCR"

    import video.ocr as ocr_module
    monkeypatch.setattr(ocr_module, "is_available", lambda: True)
    monkeypatch.setattr(ocr_module, "extract_text", fake_extract_text)
    # مع إخفاء الأسماء (الافتراضي) يُقرأ النصّ والكلمات بتشغيلٍ واحد
    monkeypatch.setattr(ocr_module, "extract_text_and_words",
                        lambda image_path, timeout_seconds=20.0:
                        (fake_extract_text(image_path), []))

    pipeline = make(tmp_path / "out", enable_ocr=True)
    result = pipeline.run(VIDEO)
    assert result.exists()

    job_dir = pipeline.job_dir_for(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert saved["keyframes"]
    assert all(k["ocr_text"] == "نص وهمي من OCR" for k in saved["keyframes"])
    assert len(calls) == len(saved["keyframes"])


def test_ocr_enabled_but_tesseract_missing_does_not_break_pipeline(tmp_path, monkeypatch):
    import video.ocr as ocr_module
    monkeypatch.setattr(ocr_module, "is_available", lambda: False)

    pipeline = make(tmp_path / "out", enable_ocr=True)
    result = pipeline.run(VIDEO)
    assert result.exists()

    job_dir = pipeline.job_dir_for(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert all(k["ocr_text"] == "" for k in saved["keyframes"])


def test_ocr_result_reaches_the_generated_document(tmp_path, monkeypatch):
    """السلسلة كاملة: OCR → KeyframeMetadata → DocumentBlock → docx."""
    import video.ocr as ocr_module
    monkeypatch.setattr(ocr_module, "is_available", lambda: True)
    monkeypatch.setattr(ocr_module, "extract_text",
                        lambda image_path, timeout_seconds=20.0: "نص من الشاشة الحقيقية")
    monkeypatch.setattr(ocr_module, "extract_text_and_words",
                        lambda image_path, timeout_seconds=20.0:
                        ("نص من الشاشة الحقيقية", []))

    # نصّ الشاشة لا يُطبع افتراضيًّا منذ 1.12.0 (document.show_screen_text)
    pipeline = make(tmp_path / "out", enable_ocr=True, show_screen_text=True)
    result = pipeline.run(VIDEO)

    from docx import Document
    doc = Document(str(result))
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "نص من الشاشة الحقيقية" in body
