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


def test_ocr_off_and_privacy_guards_off_leaves_ocr_text_empty(tmp_path):
    """OCR المحتوى معطّل **والحماية معطّلة**: لا قراءة. منذ 1.19.1 الحماية
    المفعّلة افتراضيًّا تقرأ اللقطات ولو كان OCR المحتوى معطّلًا."""
    pipeline = make(tmp_path / "out", enable_ocr=False)
    pipeline.config.document.anonymize_names = False
    pipeline.config.document.withhold_sensitive_screens = False
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert saved["keyframes"], "لا لقطات في هذا الفيديو الاختباري"
    assert all(k["ocr_text"] == "" for k in saved["keyframes"])


def test_ocr_enabled_calls_extract_text_and_persists_result(tmp_path, monkeypatch):
    # ``calls`` يخلط استدعاءين مصدرهما مختلف: مسح مصطلحات الشاشة
    # (``screen_terms.scan_video``) يستدعي OCR على إطارات مرشَّحة قبل
    # اختيار اللقطات النهائية (أسماء ``scan_*.jpg``)، ثم يُستدعى مجددًا
    # على كل لقطة نهائية محفوظة (اسمها = ``keyframe.filename``). عدّ كل
    # الاستدعاءات معًا كان يقارن 15 (10 مسح + 5 لقطات) بـ5 — لا عطل هنا،
    # فقط تأكيدٌ يجب أن يقتصر على استدعاءات اللقطات النهائية وحدها.
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

    keyframe_names = {k["filename"] for k in saved["keyframes"]}
    keyframe_calls = [name for name in calls if name in keyframe_names]
    assert len(keyframe_calls) == len(saved["keyframes"]), (
        f"OCR لكل لقطة نهائية: نودي {len(keyframe_calls)} من "
        f"{len(saved['keyframes'])} — كل الاستدعاءات: {calls}")


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


# ── 1.19.1: فحص الخصوصية في المسار المحلي الافتراضي ─────────────────────
def _login_ocr(monkeypatch):
    import video.ocr as ocr_module
    login = "Microsoft\nSign in\nEmail, phone, or Skype\nNo account? Create one!"
    monkeypatch.setattr(ocr_module, "is_available", lambda: True)
    monkeypatch.setattr(ocr_module, "extract_text", lambda p, timeout_seconds=20.0: login)
    monkeypatch.setattr(ocr_module, "extract_text_and_words",
                        lambda p, timeout_seconds=20.0: (login, []))


def test_default_local_run_withholds_login_screens_without_enable_ocr(tmp_path, monkeypatch):
    """مراجعة 1.19.0: مستندٌ محلي (بلا إرسال صور، OCR المحتوى معطّل) كان
    يُدخل شاشة الدخول رغم ``withhold_sensitive_screens``."""
    _login_ocr(monkeypatch)
    pipeline = make(tmp_path / "out", enable_ocr=False)
    assert not pipeline.config.rewrite.enabled
    pipeline.run(VIDEO)

    job_dir = pipeline.job_dir_for(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert saved["keyframes"]
    assert all(k["privacy_scanned"] and k["sensitive"] == ["login"]
               for k in saved["keyframes"])
    plan = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
    figures = [b for s in plan["sections"] for b in s["blocks"] if b.get("image_id")]
    assert figures == [], "شاشة دخول دخلت المستند"
    quality = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
    assert any(w.startswith("privacy: حُجبت") for w in quality["warnings"])


def test_default_run_without_tesseract_says_frames_were_not_scanned(tmp_path, monkeypatch):
    import video.ocr as ocr_module
    monkeypatch.setattr(ocr_module, "is_available", lambda: False)
    pipeline = make(tmp_path / "out", enable_ocr=False)
    pipeline.run(VIDEO)

    job_dir = pipeline.job_dir_for(VIDEO)
    quality = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
    assert any("لم تُفحص" in w and "Tesseract" in w for w in quality["warnings"])


def test_resumed_job_from_before_the_scan_is_scanned_and_its_plan_rebuilt(tmp_path, monkeypatch):
    """مهمّة 1.19.0 محلية (بلا فحص) تُستأنف: تُفحص لقطاتها، ولا تُعاد خطّتها
    القديمة التي فيها شاشة الدخول."""
    import video.ocr as ocr_module
    monkeypatch.setattr(ocr_module, "is_available", lambda: False)
    pipeline = make(tmp_path / "out", enable_ocr=False)
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)
    plan = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
    assert any(b.get("image_id") for s in plan["sections"] for b in s["blocks"])

    _login_ocr(monkeypatch)
    pipeline = make(tmp_path / "out", enable_ocr=False)
    pipeline.run(VIDEO)
    saved = json.loads((job_dir / "keyframes.json").read_text(encoding="utf-8"))
    assert all(k["privacy_scanned"] for k in saved["keyframes"])
    plan = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
    assert not any(b.get("image_id") for s in plan["sections"] for b in s["blocks"])
