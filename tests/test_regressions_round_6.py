"""اختبارات انحدار لجولة الإصلاحات السادسة.

كل اختبار هنا يقابل عطلًا مؤكَّدًا أو ميزة جديدة في هذه الجولة، ومكتوب
ليفشل على الشيفرة قبل الإصلاح.
"""
import json
import os
from pathlib import Path

import pytest

from audio.model_manager import ModelManager, _matches_model_dir
from config.schemas import (
    AudioSegment, DocumentBlock, DocumentPlan, DocumentSection,
    TranscriptionResult, VideoMetadata, WordTimestamp,
)
from config.settings import AppConfig
from document.subtitles import build_cues, render_srt, render_vtt


# ---------------------------------------------------------------------
# 1 · التنزيل الصامت: ``large-v3`` ليس ``large-v3-turbo``
# ---------------------------------------------------------------------
def _make_cache(root: Path, model_dir: str) -> None:
    target = root / model_dir / "snapshots" / "abc123"
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(b"\x00")


def test_turbo_in_cache_does_not_mark_large_v3_as_installed(tmp_path):
    """العطل الأصلي: المطابقة الجزئية جعلت turbo يُخفي غياب large-v3.

    الأثر كان تنزيلًا صامتًا بحجم 3.1 جيجابايت بلا إذن — خرق ADR-014.
    """
    _make_cache(tmp_path, "models--Systran--faster-whisper-large-v3-turbo")
    manager = ModelManager(tmp_path)
    assert manager.is_available("large-v3-turbo") is True
    assert manager.is_available("large-v3") is False


def test_large_v3_in_cache_does_not_mark_turbo_as_installed(tmp_path):
    _make_cache(tmp_path, "models--Systran--faster-whisper-large-v3")
    manager = ModelManager(tmp_path)
    assert manager.is_available("large-v3") is True
    assert manager.is_available("large-v3-turbo") is False


def test_plain_directory_layout_is_recognised(tmp_path):
    """بعض الإعدادات تضع اسم النموذج مجلدًا مباشرًا بلا بادئة HuggingFace."""
    (tmp_path / "medium").mkdir()
    (tmp_path / "medium" / "model.bin").write_bytes(b"\x00")
    assert ModelManager(tmp_path).is_available("medium") is True
    assert ModelManager(tmp_path).is_available("small") is False


@pytest.mark.parametrize("directory,name,expected", [
    ("models--Systran--faster-whisper-large-v3-turbo", "large-v3-turbo", True),
    ("models--Systran--faster-whisper-large-v3-turbo", "large-v3", False),
    ("models--Systran--faster-whisper-small", "small", True),
    ("models--Systran--faster-whisper-small", "medium", False),
])
def test_model_dir_matching_is_exact(directory, name, expected):
    assert _matches_model_dir(Path(directory) / "snapshots" / "x",
                              name) is expected


def test_transcription_engine_defaults_to_no_download():
    """الحاجز الثاني: المحرّك لا يُسمح له بالشبكة إلا بإذن صريح."""
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine
    assert TranscriptionEngine().allow_download is False


# ---------------------------------------------------------------------
# 2 · السجلّ لا يكتب على القرص عند الاستيراد
# ---------------------------------------------------------------------
def test_importing_logger_creates_no_directory(tmp_path, monkeypatch):
    """كان الاستيراد ينشئ ``logs/`` في مجلد العمل أيًا كان."""
    monkeypatch.chdir(tmp_path)
    import importlib

    import utils.logger as logger_module
    importlib.reload(logger_module)
    assert not (tmp_path / "logs").exists()


def test_job_context_reaches_log_records(tmp_path, monkeypatch):
    """‏JOB و STAGE في المواصفة §25 كانتا دائمًا ``-``."""
    monkeypatch.chdir(tmp_path)
    import logging

    from utils.logger import bind_job, clear_job, logger, setup_logger

    setup_logger(tmp_path / "logs")
    captured = []

    class Sink(logging.Handler):
        def emit(self, record):
            captured.append((record.job_id, record.stage))

    sink = Sink()
    logger.addHandler(sink)
    try:
        bind_job("20260901-1200", "transcription")
        logger.info("اختبار")
    finally:
        logger.removeHandler(sink)
        clear_job()

    assert captured == [("20260901-1200", "transcription")]


# ---------------------------------------------------------------------
# 3 · الإطار المرجعي للدوران لا يُكتب بجوار فيديو المستخدم
# ---------------------------------------------------------------------
def test_rotation_reference_never_written_next_to_source(tmp_path):
    """المواصفة §29: لا كتابة في مجلد ملفات المستخدم الأصلية."""
    import numpy as np

    from utils.frames import resolve_rotation_plan

    source = tmp_path / "مصدر"
    scratch = tmp_path / "scratch"
    source.mkdir()
    video = source / "clip.mp4"
    video.write_bytes(b"\x00" * 16)
    before = set(os.listdir(source))

    class Recorder:
        def extract_frame(self, video_path, timestamp, output_path, width=None):
            self.path = Path(output_path)
            raise RuntimeError("مرجع غير متاح في الاختبار")

    recorder = Recorder()
    resolve_rotation_plan(video, 180, 640, 360, 640, 360,
                          sample_frame=np.zeros((360, 640, 3), dtype=np.uint8),
                          ffmpeg=recorder, temp_dir=scratch)

    assert set(os.listdir(source)) == before
    assert scratch in recorder.path.parents


# ---------------------------------------------------------------------
# 4 · ملفات الترجمة من توقيت الكلمات المحفوظ
# ---------------------------------------------------------------------
def _transcript() -> TranscriptionResult:
    return TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[
            AudioSegment(id=1, start=0.5, end=3.0,
                         text_raw="مرحبًا بكم في الدرس",
                         text_clean="مرحبًا بكم في الدرس"),
            AudioSegment(id=2, start=3.2, end=6.0,
                         text_raw="نبدأ اليوم بمقدمة",
                         text_clean="نبدأ اليوم بمقدمة"),
        ])


def test_srt_has_numbered_blocks_and_comma_milliseconds():
    output = render_srt(_transcript())
    assert output.startswith("1\n")
    assert "00:00:00,500 --> 00:00:03,000" in output
    assert "مرحبًا بكم في الدرس" in output


def test_vtt_starts_with_header_and_dot_milliseconds():
    output = render_vtt(_transcript())
    assert output.startswith("WEBVTT")
    assert "00:00:03.200 --> 00:00:06.000" in output


def test_cues_never_overlap():
    transcript = TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[
            AudioSegment(id=1, start=0.0, end=4.0, text_raw="أ", text_clean="أ"),
            AudioSegment(id=2, start=3.0, end=6.0, text_raw="ب", text_clean="ب"),
        ])
    cues = build_cues(transcript)
    for previous, current in zip(cues, cues[1:], strict=False):
        assert previous[1] <= current[0]


def test_long_segment_is_split_using_word_timestamps():
    """مقطع 20 ثانية يصير عدة بطاقات مقطوعة عند حدود كلمات حقيقية."""
    words = [WordTimestamp(word=f"كلمة{i}", start=float(i), end=float(i) + 0.9)
             for i in range(20)]
    transcript = TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[AudioSegment(
            id=1, start=0.0, end=20.0,
            text_raw=" ".join(w.word for w in words),
            text_clean=" ".join(w.word for w in words),
            words=words)])
    cues = build_cues(transcript)
    assert len(cues) > 1
    assert all(end - start <= 9.0 for start, end, _ in cues)
    # لا كلمة تضيع
    joined = " ".join(text for _, _, text in cues)
    assert "كلمة0" in joined and "كلمة19" in joined


def test_empty_transcript_writes_nothing(tmp_path):
    from document.subtitles import write_subtitles
    empty = TranscriptionResult(language="ar", full_text_raw="",
                                full_text_clean="", segments=[])
    assert write_subtitles(empty, tmp_path / "out.docx") == []


# ---------------------------------------------------------------------
# 5 · إعادة بناء المستند من خطة محفوظة
# ---------------------------------------------------------------------
def test_rebuild_document_uses_saved_plan_without_reprocessing(tmp_path):
    from core.pipeline import VideoToDocPipeline

    job_dir = tmp_path / "job"
    (job_dir / "keyframes").mkdir(parents=True)

    plan = DocumentPlan(
        title="محاضرة تجريبية", subtitle="مستند مُولَّد",
        sections=[DocumentSection(title="القسم الأول", blocks=[
            DocumentBlock(kind="paragraph", text="نص القسم الأول.",
                          segment_ids=[1])])],
        total_segments_in=1, total_segments_out=1)
    (job_dir / "plan.json").write_text(plan.model_dump_json(indent=2),
                                       encoding="utf-8")

    metadata = VideoMetadata(
        filename="clip.mp4", path=tmp_path / "clip.mp4",
        duration_seconds=90.0, width=1280, height=720, fps=25.0,
        codec="h264", has_audio=True)
    (job_dir / "metadata.json").write_text(metadata.model_dump_json(indent=2),
                                           encoding="utf-8")

    config = AppConfig()
    config.document.export_subtitles = False
    pipeline = VideoToDocPipeline(tmp_path, config)
    result = pipeline.rebuild_document(job_dir)

    assert result.exists() and result.suffix == ".docx"
    assert result.stat().st_size > 0


def test_rebuild_without_a_saved_plan_fails_clearly(tmp_path):
    from core.exceptions import ArtifactMissingError
    from core.pipeline import VideoToDocPipeline

    job_dir = tmp_path / "empty_job"
    job_dir.mkdir()
    with pytest.raises(ArtifactMissingError) as excinfo:
        VideoToDocPipeline(tmp_path, AppConfig()).rebuild_document(job_dir)
    assert "خطة" in str(excinfo.value)


# ---------------------------------------------------------------------
# 6 · إعداد تالف: تحذير لا صمت
# ---------------------------------------------------------------------
def test_corrupt_config_reports_the_reason(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("whisper:\n  beam_size: [ليست رقمًا]\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.load_error, "الإعداد التالف كان يُتجاهَل بصمت"
    assert config.whisper.beam_size == 5          # عاد إلى الافتراضي


def test_load_error_is_not_persisted(tmp_path):
    import yaml

    config = AppConfig()
    config.load_error = "خطأ تشخيصي"
    target = tmp_path / "config.yaml"
    config.save(target)
    assert "load_error" not in yaml.safe_load(target.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# 7 · اختيار محرّك التفريغ صار موصولًا فعلًا (ADR-017)
# ---------------------------------------------------------------------
def test_engine_choice_exists_in_config_and_reaches_the_pipeline():
    from core.pipeline import VideoToDocPipeline

    config = AppConfig()
    assert config.whisper.engine == "faster-whisper"
    pipeline = VideoToDocPipeline(Path("."), config,
                                  transcriber=object())
    assert pipeline.transcriber is not None


def test_unknown_engine_falls_back_to_the_default(tmp_path):
    from core.pipeline import VideoToDocPipeline

    pytest.importorskip("faster_whisper")
    config = AppConfig()
    config.whisper.engine = "لا-يوجد-هذا-المحرّك"
    pipeline = VideoToDocPipeline(tmp_path, config)
    assert pipeline.transcriber.info.name == "faster-whisper"


# ---------------------------------------------------------------------
# 8 · بقايا محارف العزل الاتجاهي حُذفت نهائيًا
# ---------------------------------------------------------------------
def test_deprecated_bidi_isolation_helpers_are_gone():
    import document.rtl_utils as rtl

    for name in ("isolate_ltr", "isolate_mixed", "auto_isolate", "LRI", "PDI"):
        assert not hasattr(rtl, name), f"{name} ما زالت موجودة"


# ---------------------------------------------------------------------
# 9 · «غير مثبّت» ليست تشخيصًا: المحرّك يقول *لماذا*
# ---------------------------------------------------------------------
def test_engine_diagnose_names_the_missing_packages(monkeypatch):
    """مستخدم نفّذ أمر التثبيت فعلًا يحتاج اسم الحزمة الناقصة لا نفيًا عامًا."""
    import builtins

    from audio.engines.registry import build_engine

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no module named torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ready, reason = build_engine("cohere-arabic").diagnose()

    assert ready is False
    assert "torch" in reason
    assert "requirements-cohere.txt" in reason
    import sys
    assert sys.executable in reason, "يجب أن يذكر السبب المفسّر الجاري"


def test_engine_diagnose_survives_non_import_errors(monkeypatch):
    """حزمة معطوبة تُظهر سببها بدل «غير مثبّت» الغامضة.

    الفحص صار ``find_spec`` لا ``__import__`` (لئلا يُحمَّل PyTorch في
    العملية ويتنازع مع CTranslate2 على OpenMP)، لكن القاعدة نفسها باقية:
    أي خطأ غير «غير موجودة» يُعرض بنصّه لا يُبتلع.
    """
    import importlib.util

    from audio.engines.registry import build_engine

    real_find_spec = importlib.util.find_spec

    def broken_find_spec(name, *args, **kwargs):
        if name == "torch":
            raise OSError("[WinError 126] الوحدة المحددة غير موجودة")
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", broken_find_spec)
    ready, reason = build_engine("cohere-arabic").diagnose()

    assert ready is False
    assert "OSError" in reason


def test_default_engine_reports_ready():
    from audio.engines.registry import build_engine

    pytest.importorskip("faster_whisper")
    ready, reason = build_engine("faster-whisper").diagnose()
    assert ready is True
    assert reason


def test_every_engine_diagnose_returns_a_reason():
    """لا محرّك يعيد سببًا فارغًا — الفراغ هو أصل مشكلة «غير مثبّت»."""
    from audio.engines.registry import ENGINE_ORDER, build_engine

    for name in ENGINE_ORDER:
        ready, reason = build_engine(name).diagnose()
        assert isinstance(ready, bool)
        assert isinstance(reason, str) and reason.strip(), name


# ---------------------------------------------------------------------
# 10 · فشل محرّك اختياري أثناء التنفيذ لا يُفشل المهمة
# ---------------------------------------------------------------------
def test_optional_engine_runtime_failure_falls_back_to_whisper(tmp_path):
    """نموذج مقيّد الوصول على HuggingFace كان يُسقط المهمة كلها عند 20%.

    ``is_available`` تفحص وجود الحزم فقط؛ الفشل يقع لاحقًا عند تحميل
    الأوزان. هذا هو نفس نمط العطل الذي أُصلح في السقوط من CUDA إلى
    المعالج: التغطية يجب أن تشمل **التنفيذ** لا التحميل وحده.
    """
    from core.exceptions import ModelUnavailableError
    from core.job_state import Stage
    from core.pipeline import VideoToDocPipeline

    class GatedEngine:
        info = type("I", (), {"name": "cohere-arabic"})()
        allow_download = False

        def transcribe(self, *_a, **_k):
            raise ModelUnavailableError("النموذج مقيّد الوصول على HuggingFace.")

    fallback_result = TranscriptionResult(
        language="ar", full_text_raw="نص", full_text_clean="نص",
        segments=[AudioSegment(id=1, start=0.0, end=1.0,
                               text_raw="نص", text_clean="نص")])

    class WhisperStub:
        info = type("I", (), {"name": "faster-whisper"})()
        allow_download = False

        def transcribe(self, *_a, **_k):
            return fallback_result

    pipeline = VideoToDocPipeline(tmp_path, AppConfig(),
                                  transcriber=GatedEngine())
    monkey = WhisperStub()
    import audio.engines.registry as registry
    original = registry.build_engine
    registry.build_engine = lambda name, **kw: monkey
    try:
        result = pipeline._transcribe_with_fallback(
            tmp_path / "a.wav", None, lambda *_: None, "cohere-arabic")
    finally:
        registry.build_engine = original

    assert result is fallback_result, "كان يجب السقوط إلى Whisper لا الفشل"
    assert pipeline.transcriber is monkey


def test_default_engine_failure_still_raises(tmp_path):
    """فشل Whisper نفسه ليس له بديل — يجب أن يظهر الخطأ لا أن يُبتلع."""
    from core.exceptions import ResourceAllocationError
    from core.pipeline import VideoToDocPipeline

    class BrokenWhisper:
        info = type("I", (), {"name": "faster-whisper"})()
        allow_download = False

        def transcribe(self, *_a, **_k):
            raise ResourceAllocationError("فشل التفريغ")

    pipeline = VideoToDocPipeline(tmp_path, AppConfig(),
                                  transcriber=BrokenWhisper())
    with pytest.raises(ResourceAllocationError):
        pipeline._transcribe_with_fallback(
            tmp_path / "a.wav", None, lambda *_: None, "faster-whisper")


def test_cancellation_is_not_swallowed_by_the_fallback(tmp_path):
    """الإلغاء ليس عطل محرّك — يجب ألا يُعامَل معاملة الفشل."""
    from core.exceptions import PipelineCancelledError
    from core.pipeline import VideoToDocPipeline

    class Cancelling:
        info = type("I", (), {"name": "cohere-arabic"})()
        allow_download = False

        def transcribe(self, *_a, **_k):
            raise PipelineCancelledError("أُلغيت")

    pipeline = VideoToDocPipeline(tmp_path, AppConfig(),
                                  transcriber=Cancelling())
    with pytest.raises(PipelineCancelledError):
        pipeline._transcribe_with_fallback(
            tmp_path / "a.wav", None, lambda *_: None, "cohere-arabic")


# ---------------------------------------------------------------------
# 11 · رمز HuggingFace من الإعداد لا من setx وحده
# ---------------------------------------------------------------------
def test_hf_token_comes_from_config_first(monkeypatch):
    """‏setx لا يؤثر على البرامج المفتوحة — فالإعداد يسبق متغيّر البيئة."""
    from audio.engines.cohere_engine import CohereArabicEngine

    monkeypatch.setenv("HF_TOKEN", "hf_from_environment")
    config = AppConfig().whisper.model_copy(update={"hf_token": "hf_from_config"})
    assert CohereArabicEngine(config=config).hf_token == "hf_from_config"


def test_hf_token_falls_back_to_environment(monkeypatch):
    from audio.engines.cohere_engine import CohereArabicEngine

    monkeypatch.setenv("HF_TOKEN", "hf_from_environment")
    assert CohereArabicEngine().hf_token == "hf_from_environment"


def test_legacy_environment_name_still_works(monkeypatch):
    """الاسم القديم ما زال شائعًا في الأدلة."""
    from audio.engines.cohere_engine import CohereArabicEngine

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_legacy")
    assert CohereArabicEngine().hf_token == "hf_legacy"


def test_no_token_anywhere_is_empty(monkeypatch):
    from audio.engines.cohere_engine import CohereArabicEngine

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    assert CohereArabicEngine().hf_token == ""


def test_engine_reads_device_from_config():
    """الإعداد هو المصدر: لا يُعاد ضبط الجهاز يدويًا في مكانين."""
    from audio.engines.cohere_engine import CohereArabicEngine

    config = AppConfig().whisper.model_copy(update={"device": "cpu"})
    assert CohereArabicEngine(config=config).device == "cpu"


def test_hf_token_is_persisted_with_the_config(tmp_path):
    """يُحفَظ الرمز فعليًا ويُستأنف بعد إعادة التحميل — لكن ليس نصًا صريحًا
    على القرص بعد إصلاح تخزين الأسرار (انظر tests/test_secret_store.py)."""
    import yaml

    config = AppConfig()
    config.whisper.hf_token = "hf_saved"
    target = tmp_path / "config.yaml"
    config.save(target)
    stored = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert stored["whisper"]["hf_token"] != "hf_saved", (
        "رمز HuggingFace خُزِّن نصًا صريحًا على القرص")
    assert stored["whisper"]["hf_token"].startswith("enc:v1:")
    assert AppConfig.load(target).whisper.hf_token == "hf_saved"


# ---------------------------------------------------------------------
# 12 · مسار الملفات الصوتية الخالصة
# ---------------------------------------------------------------------
def test_audio_metadata_skips_visual_fields():
    """أبعاد صفرية مقبولة لمصدر صوتي، ومرفوضة للفيديو."""
    audio = VideoMetadata(
        filename="lecture.mp3", path=Path("lecture.mp3"),
        duration_seconds=600.0, width=0, height=0, fps=0.0,
        codec="mp3", has_audio=True, has_video=False)
    assert audio.has_video is False

    with pytest.raises(Exception):
        VideoMetadata(
            filename="clip.mp4", path=Path("clip.mp4"),
            duration_seconds=10.0, width=0, height=0, fps=0.0,
            codec="h264", has_audio=True)     # has_video افتراضيًا True


def test_audio_facts_require_explicit_opt_in():
    from core.exceptions import MediaValidationError
    from utils.media_probe import extract_video_facts

    data = {"format": {"duration": "120.0"},
            "streams": [{"codec_type": "audio", "codec_name": "aac",
                         "sample_rate": "44100"}]}
    with pytest.raises(MediaValidationError):
        extract_video_facts(data)

    facts = extract_video_facts(data, allow_audio_only=True)
    assert facts["has_video"] is False
    assert facts["duration_seconds"] == 120.0
    assert facts["audio_sample_rate"] == 44100
    assert facts["codec"] == "aac"


def test_zero_duration_audio_is_rejected():
    from core.exceptions import MediaValidationError
    from utils.media_probe import extract_video_facts

    data = {"format": {"duration": "0"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    with pytest.raises(MediaValidationError):
        extract_video_facts(data, allow_audio_only=True)


def test_cover_facts_omit_resolution_for_audio(tmp_path):
    """«الدقة 0×0» في مستند صوتي معلومة خاطئة لا ناقصة."""
    from document.word_generator import DocumentGenerator

    plan = DocumentPlan(title="محاضرة", sections=[
        DocumentSection(title="القسم الأول", blocks=[
            DocumentBlock(kind="paragraph", text="نص.", segment_ids=[1])])],
        total_segments_in=1, total_segments_out=1)

    audio = VideoMetadata(
        filename="lecture.mp3", path=tmp_path / "lecture.mp3",
        duration_seconds=600.0, width=0, height=0, fps=0.0,
        codec="mp3", has_audio=True, has_video=False)
    labels = [label for label, _ in
              DocumentGenerator()._cover_facts(plan, audio)]
    assert "الدقة" not in labels
    assert "عدد اللقطات" not in labels
    assert "نوع المصدر" in labels

    video = VideoMetadata(
        filename="clip.mp4", path=tmp_path / "clip.mp4",
        duration_seconds=60.0, width=1280, height=720, fps=25.0,
        codec="h264", has_audio=True)
    labels = [label for label, _ in
              DocumentGenerator()._cover_facts(plan, video)]
    assert "الدقة" in labels and "عدد اللقطات" in labels


def test_audio_pipeline_produces_a_document(tmp_path):
    """المسار كاملًا: ملف صوتي حقيقي ← مستند Word منسّق بلا لقطات."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg غير متاح")

    source = tmp_path / "درس صوتي.m4a"
    made = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3", "-c:a", "aac", str(source)],
        capture_output=True)
    if made.returncode != 0 or not source.exists():
        pytest.skip("تعذّر توليد ملف صوتي للاختبار")

    from core.pipeline import VideoToDocPipeline

    class StubTranscriber:
        info = type("I", (), {"name": "faster-whisper"})()
        allow_download = False

        def transcribe(self, *_a, **_k):
            return TranscriptionResult(
                language="ar", full_text_raw="مرحبًا", full_text_clean="مرحبًا",
                segments=[AudioSegment(id=1, start=0.0, end=3.0,
                                       text_raw="مرحبًا بكم في الدرس.",
                                       text_clean="مرحبًا بكم في الدرس.")])

    class StubModels:
        def __init__(self, *_a, **_k):
            pass

        def ensure_available(self, *_a, **_k):
            return None

    config = AppConfig()
    pipeline = VideoToDocPipeline(tmp_path / "out", config,
                                  transcriber=StubTranscriber(),
                                  model_manager=StubModels())
    docx = pipeline.run(source, allow_audio_only=True)

    assert docx.exists() and docx.suffix == ".docx"
    job_dir = docx.parent
    metadata = json.loads(
        (job_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["has_video"] is False
    # لا مجلد صور ولا ناتج مشاهد
    assert not (job_dir / "keyframes.json").exists()
    # الترجمة تُكتب من الصوت أيضًا
    assert (job_dir / f"{source.stem}.srt").exists()


def test_audio_file_rejected_without_the_flag(tmp_path):
    """نفس الملف عبر زر الفيديو يجب أن يفشل برسالة عربية واضحة."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg غير متاح")

    source = tmp_path / "sound.m4a"
    made = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(source)],
        capture_output=True)
    if made.returncode != 0:
        pytest.skip("تعذّر توليد ملف صوتي للاختبار")

    from core.exceptions import AppBaseException
    from core.pipeline import VideoToDocPipeline

    with pytest.raises(AppBaseException) as info:
        VideoToDocPipeline(tmp_path / "out2", AppConfig()).run(source)
    assert any("\u0600" <= ch <= "\u06ff" for ch in str(info.value))
