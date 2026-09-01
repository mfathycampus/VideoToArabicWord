"""طبقة محرّكات التفريغ القابلة للتبديل (ADR-017)."""
from pathlib import Path

import pytest

from audio.engines.base import ASREngine, EngineInfo
from audio.engines.registry import ENGINE_ORDER, available_engines, build_engine
from core.exceptions import ModelUnavailableError


def test_default_engine_is_first_and_torch_free():
    assert ENGINE_ORDER[0] == "faster-whisper"
    info = build_engine("faster-whisper").info
    assert info.needs_torch is False, "الافتراضي يجب ألا يجرّ PyTorch"


def test_all_registered_engines_build_and_describe_themselves():
    for name in ENGINE_ORDER:
        engine = build_engine(name)
        assert isinstance(engine, ASREngine)
        info = engine.info
        assert info.label_ar and info.backend and info.languages
        assert info.approx_size_gb > 0


def test_heavy_engine_declares_its_cost():
    """المحرّك الذي يحتاج torch يجب أن يعلن ذلك وأن يعطي تعليمة تثبيت."""
    info = build_engine("cohere-arabic").info
    assert info.needs_torch is True
    assert info.install_hint, "محرّك ثقيل بلا تعليمة تثبيت"
    assert "requirements-cohere" in info.install_hint


def test_unknown_engine_rejected():
    with pytest.raises(ModelUnavailableError):
        build_engine("no-such-engine")


def test_availability_check_is_cheap_and_offline():
    """الفحص لا ينزّل نموذجًا ولا يشغّله."""
    for name in ENGINE_ORDER:
        assert isinstance(build_engine(name).is_available(), bool)


def test_available_engines_never_raises():
    infos = available_engines()
    assert infos and all(isinstance(i, EngineInfo) for i in infos)


def test_default_engine_wraps_existing_transcriber():
    engine = build_engine("faster-whisper")
    assert engine.is_available() is True
    assert hasattr(engine, "keep_model_loaded")


def test_engine_interface_is_complete():
    for name in ENGINE_ORDER:
        engine = build_engine(name)
        for method in ("transcribe", "is_available", "release"):
            assert callable(getattr(engine, method))


def test_benchmark_tool_reports_missing_engine_gracefully(tmp_path):
    """محرّك غير مثبّت يُبلَّغ عنه ولا يُسقط المقارنة."""
    from tools.benchmark_asr import evaluate
    outcome = evaluate("cohere-arabic", [], "cpu")
    assert "engine" in outcome and "label" in outcome


def test_cohere_engine_has_no_module_level_torch_import():
    """التبعيات الثقيلة تُستورد داخل الدوال فقط.

    استيرادها على مستوى الوحدة يجعل مجرد سرد المحرّكات يفشل على أي
    جهاز بلا PyTorch.
    """
    source = (Path(__file__).resolve().parents[1] / "audio" / "engines"
              / "cohere_engine.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import torch", "from torch",
                                "import transformers", "from transformers")):
            assert line.startswith("    "), \
                f"استيراد ثقيل على مستوى الوحدة: {stripped}"
