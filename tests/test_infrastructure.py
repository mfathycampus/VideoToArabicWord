"""اختبارات البنية التحتية: الإعداد، النماذج، FFmpeg، الميتاداتا."""
import json
import shutil
from pathlib import Path

import pytest

from audio.model_manager import AVAILABLE_MODELS, DEFAULT_MODEL, ModelManager
from config.settings import AppConfig, TranscriptionConfig
from core.exceptions import (
    FFmpegExecutionError, MediaValidationError, ModelUnavailableError,
)
from utils.ffmpeg_service import FFmpegService
from utils.media_probe import (
    _parse_fraction, _rotation_degrees, extract_video_facts,
    probe_raw, probe_with_ffmpeg_fallback, resolve_ffmpeg, resolve_ffprobe,
)

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"


# ---------------- الإعداد ----------------
def test_default_config_ships_best_measured_model():
    """القياس على العربية رجّح turbo على large-v3 في الدقة والسرعة معًا."""
    assert AppConfig().whisper.model_size == DEFAULT_MODEL == "large-v3-turbo"


def test_config_roundtrips_through_yaml(tmp_path):
    config = AppConfig()
    config.whisper.model_size = "medium"
    config.document.body_size_pt = 14.0
    path = tmp_path / "config.yaml"
    config.save(path)
    loaded = AppConfig.load(path)
    assert loaded.whisper.model_size == "medium"
    assert loaded.document.body_size_pt == 14.0


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("{{{ not yaml", encoding="utf-8")
    assert AppConfig.load(path).whisper.model_size == DEFAULT_MODEL


def test_missing_config_uses_defaults(tmp_path):
    assert AppConfig.load(tmp_path / "nope.yaml").whisper.model_size == DEFAULT_MODEL


def test_arabic_critical_whisper_settings_are_on():
    config = TranscriptionConfig()
    assert config.condition_on_previous_text is False, \
        "يجب إبقاؤه False وإلا دخل النموذج في حلقات تكرار على الصمت"
    assert config.language == "ar"
    assert config.vad_filter is True
    assert config.word_timestamps is True


# ---------------- مدير النماذج ----------------
def test_model_manager_refuses_download_without_permission(tmp_path):
    manager = ModelManager(tmp_path / "empty_cache")
    with pytest.raises(ModelUnavailableError) as info:
        manager.ensure_available("large-v3", allow_download=False)
    message = str(info.value)
    assert "large-v3" in message and any("؀" <= c <= "ۿ" for c in message)


def test_unknown_model_rejected(tmp_path):
    with pytest.raises(ModelUnavailableError):
        ModelManager(tmp_path).spec("does-not-exist")


def test_all_model_specs_are_well_formed():
    names = [s.name for s in AVAILABLE_MODELS]
    assert len(names) == len(set(names))
    assert DEFAULT_MODEL in names
    for spec in AVAILABLE_MODELS:
        assert spec.approx_size_gb > 0 and spec.label_ar and spec.quality_note


def test_model_availability_check_makes_no_network_call(tmp_path, monkeypatch):
    """ADR-002: فحص التوفّر يجب ألا يلمس الشبكة إطلاقًا."""
    import socket

    def blocked(*_a, **_k):
        raise AssertionError("محاولة اتصال شبكي أثناء فحص توفّر النموذج")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    assert ModelManager(tmp_path / "cache").is_available("large-v3") is False


# ---------------- FFmpeg ----------------
def test_ffmpeg_and_ffprobe_resolve():
    assert Path(resolve_ffmpeg()).exists()
    probe = resolve_ffprobe()
    assert probe is None or Path(probe).exists()


def test_audio_extraction_produces_16k_mono(tmp_path):
    service = FFmpegService()
    out = tmp_path / "a.wav"
    service.extract_audio(CORPUS / "h264_mp4.mp4", out)
    facts = probe_raw(out)
    stream = facts["streams"][0]
    assert int(stream["sample_rate"]) == 16000
    assert int(stream["channels"]) == 1


def test_audio_extraction_fails_clearly_when_no_audio(tmp_path):
    with pytest.raises(FFmpegExecutionError):
        FFmpegService().extract_audio(CORPUS / "no_audio.mp4", tmp_path / "a.wav")


def test_frame_extraction_via_ffmpeg(tmp_path):
    out = tmp_path / "frame.png"
    FFmpegService().extract_frame(CORPUS / "h264_mp4.mp4", 5.0, out)
    assert out.exists() and out.stat().st_size > 1000


# ---------------- الميتاداتا ----------------
def test_fraction_parsing_handles_ntsc_and_zero():
    assert abs(_parse_fraction("30000/1001") - 29.97) < 0.01
    assert _parse_fraction("0/0", 0.0) == 0.0
    assert _parse_fraction("", 7.0) == 7.0
    assert _parse_fraction("25") == 25.0


def test_vfr_prefers_avg_frame_rate():
    data = {"format": {"duration": "10"}, "streams": [{
        "codec_type": "video", "width": 1920, "height": 1080,
        "avg_frame_rate": "30000/1001", "r_frame_rate": "60/1",
        "codec_name": "h264", "tags": {}}]}
    assert abs(extract_video_facts(data)["fps"] - 29.97) < 0.01


def test_rotation_read_from_side_data_and_tags():
    assert _rotation_degrees({"side_data_list": [{"rotation": 90}]}) == 270
    assert _rotation_degrees({"side_data_list": [{"rotation": -90}]}) == 90
    assert _rotation_degrees({"tags": {"rotate": "180"}}) == 180
    assert _rotation_degrees({}) == 0


def test_ffmpeg_fallback_matches_ffprobe():
    """المسار الاحتياطي يجب أن يعطي نفس الحقائق الأساسية."""
    video = CORPUS / "h264_mp4.mp4"
    primary = extract_video_facts(probe_raw(video))
    fallback = extract_video_facts(
        probe_with_ffmpeg_fallback(video, resolve_ffmpeg()))
    assert fallback["width"] == primary["width"]
    assert fallback["height"] == primary["height"]
    assert abs(fallback["fps"] - primary["fps"]) < 0.5
    assert abs(fallback["duration_seconds"] - primary["duration_seconds"]) < 0.5


def test_missing_file_raises_media_error(tmp_path):
    with pytest.raises(MediaValidationError):
        probe_raw(tmp_path / "nope.mp4")


def test_empty_file_raises_media_error():
    with pytest.raises(MediaValidationError):
        probe_raw(CORPUS / "empty.mp4")


def test_audio_only_file_has_no_video_stream():
    with pytest.raises(MediaValidationError):
        extract_video_facts(probe_raw(CORPUS / "audio_only.m4a"))


def test_zero_fps_is_rejected():
    data = {"format": {"duration": "10"}, "streams": [{
        "codec_type": "video", "width": 640, "height": 360,
        "avg_frame_rate": "0/0", "r_frame_rate": "0/0",
        "codec_name": "h264", "tags": {}}]}
    with pytest.raises(MediaValidationError):
        extract_video_facts(data)


def test_torch_is_not_a_declared_dependency():
    """ADR-012: التثبيت الأساسي لا يجرّ PyTorch.

    الفحص على **الاعتماد المُعلَن** لا على ``sys.modules``: مكتبات مثل
    faster-whisper تستورد torch انتهازيًا إن وُجد على الجهاز، دون أن
    تشترطه. الضمانة الحقيقية أن تثبيتنا لا يطلبه.
    """
    root = Path(__file__).resolve().parents[1]
    for name in ("requirements.txt", "requirements.in"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#")[0].strip()
            if not stripped or stripped.startswith("-"):
                continue
            package = stripped.split(">")[0].split("=")[0].split("<")[0].strip()
            assert package.lower() not in ("torch", "torchaudio", "torchvision"), \
                f"{name}: PyTorch مُعلَن كاعتماد أساسي — يخالف ADR-012"


def test_no_core_module_imports_torch():
    """لا وحدة في المسار الأساسي تستورد torch مباشرةً.

    الاستثناء الوحيد المسموح: محرّك Cohere الاختياري، وهو غير مثبّت
    افتراضيًا ويُستورد داخل الدوال لا على مستوى الوحدة.
    """
    root = Path(__file__).resolve().parents[1]
    allowed = {"audio/engines/cohere_engine.py"}
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("tests/") or relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import torch", "from torch")):
                offenders.append(f"{relative}: {stripped}")
    assert not offenders, f"وحدات تستورد torch: {offenders}"


def test_gpu_detection_uses_ctranslate2_not_torch():
    """اكتشاف CUDA يتم عبر ctranslate2 — أساس حذف torch."""
    source = (Path(__file__).resolve().parents[1] / "utils" / "gpu_manager.py"
              ).read_text(encoding="utf-8")
    assert "ctranslate2" in source
    assert "import torch" not in source


def test_optional_cohere_requirements_are_separate():
    """تبعيات Cohere الثقيلة في ملف مستقل لا في التثبيت الأساسي."""
    root = Path(__file__).resolve().parents[1]
    optional = root / "requirements-cohere.txt"
    assert optional.exists(), "ملف التبعيات الاختيارية مفقود"
    text = optional.read_text(encoding="utf-8")
    assert "torch" in text and "transformers" in text
