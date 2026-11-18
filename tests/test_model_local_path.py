"""1.16.2: التفريغ يحمّل النموذج من مجلده المحلي لا من مخبأ huggingface_hub.

في 1.16.1 فشل التفريغ بـ E4001 «The cached snapshot … is incomplete» على
نموذجٍ كامل: ‏huggingface_hub 2.x رأى روابط اللقطة الرمزية مفقودة. المجلد
المحلي يُمرَّر إلى faster-whisper مباشرةً فلا يمرّ بالمخبأ أصلًا.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from audio.model_manager import ModelManager

REPO = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json",
         "preprocessor_config.json")


def _hf_cache(root: Path, commit: str = "0a363e9161cbc7ed", *, links: bool = True,
              main: bool = True, missing: tuple = ()) -> Path:
    repo = root / REPO
    snapshot = repo / "snapshots" / commit
    snapshot.mkdir(parents=True)
    (repo / "blobs").mkdir(exist_ok=True)
    for index, name in enumerate(FILES):
        if name in missing:
            continue
        blob = repo / "blobs" / f"{commit}{index}"
        blob.write_bytes(b"x" * 8)
        if links:
            try:
                os.symlink(os.path.relpath(blob, snapshot), snapshot / name)
            except OSError:
                pytest.skip("لا صلاحية لإنشاء روابط رمزية")
        else:
            (snapshot / name).write_bytes(blob.read_bytes())
    if main:
        (repo / "refs").mkdir(exist_ok=True)
        (repo / "refs" / "main").write_text(commit, encoding="utf-8")
    return snapshot


def test_the_model_folder_is_found_through_the_cache_links(tmp_path):
    snapshot = _hf_cache(tmp_path)
    assert ModelManager(tmp_path).local_path("large-v3-turbo") == snapshot


def test_a_plain_copy_without_links_is_found_too(tmp_path):
    snapshot = _hf_cache(tmp_path, links=False)
    assert ModelManager(tmp_path).local_path("large-v3-turbo") == snapshot


def test_large_v3_is_not_mistaken_for_turbo(tmp_path):
    _hf_cache(tmp_path)
    assert ModelManager(tmp_path).local_path("large-v3") is None


def test_an_incomplete_folder_is_not_used(tmp_path):
    _hf_cache(tmp_path, missing=("tokenizer.json",))
    assert ModelManager(tmp_path).local_path("large-v3-turbo") is None


def test_the_snapshot_main_points_to_wins(tmp_path):
    _hf_cache(tmp_path, commit="old", main=False)
    current = _hf_cache(tmp_path, commit="new")
    assert ModelManager(tmp_path).local_path("large-v3-turbo") == current


def test_the_transcriber_loads_the_folder_not_the_name(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    from audio import transcriber as module
    from config.settings import TranscriptionConfig

    snapshot = _hf_cache(tmp_path)
    seen = {}

    class FakeModel:
        def __init__(self, reference, **kwargs):
            seen["reference"] = reference
            seen["local_files_only"] = kwargs.get("local_files_only")

    monkeypatch.setattr(module, "WhisperModel", FakeModel)
    config = TranscriptionConfig(model_size="large-v3-turbo", download_root=tmp_path)
    module.TranscriptionEngine(config)._load_model("cpu", "int8")
    assert Path(seen["reference"]) == snapshot
    assert seen["local_files_only"] is True


# ---------------------------------------------------------------------
# 1.22.10: لقطة ناقصة = غير متوفّرة. تعريفٌ واحدٌ لـ«متوفّر»
# ---------------------------------------------------------------------
# العطل كما وقع عند المالك (18 نوفمبر 2026): ``is_available`` كانت تكتفي
# بوجود ``model.bin``، و``local_path`` تشترط ``config.json`` و
# ``tokenizer.json`` معه. فحين نقصت اللقطة قالت الأولى «متوفّر محليًا»
# فلم يُنزَّل شيء، ثم عادت الثانية بـ ``None`` فسقط التحميل إلى الاسم،
# فرمى huggingface_hub بالإنجليزية:
#   E4001 … The cached snapshot … is incomplete: 5 file(s) are missing
# بعد أن استُخرج الصوت وضاع وقت المستخدم.

def test_an_incomplete_snapshot_is_not_available(tmp_path):
    _hf_cache(tmp_path, links=False, missing=("config.json", "tokenizer.json"))
    manager = ModelManager(tmp_path)
    assert manager.is_available("large-v3-turbo") is False
    assert manager.local_path("large-v3-turbo") is None


def test_an_incomplete_snapshot_is_named_as_such_not_as_missing(tmp_path):
    """«ناقص» و«غير مثبّت» حالتان مختلفتان: إكمالٌ مقابل تنزيلٍ كامل."""
    from core.exceptions import ModelUnavailableError

    snapshot = _hf_cache(tmp_path, links=False,
                         missing=("config.json", "tokenizer.json"))
    manager = ModelManager(tmp_path)
    assert manager.incomplete_path("large-v3-turbo") == snapshot
    assert manager.missing_files(snapshot) == ["config.json", "tokenizer.json"]

    with pytest.raises(ModelUnavailableError) as excinfo:
        manager.ensure_available("large-v3-turbo", allow_download=False)
    text = str(excinfo.value)
    assert "ناقصة" in text and "config.json" in text
    assert str(snapshot) in text                      # أين، لا «في مكان ما»


def test_a_complete_snapshot_stays_available(tmp_path):
    _hf_cache(tmp_path, links=False)
    manager = ModelManager(tmp_path)
    assert manager.is_available("large-v3-turbo") is True
    assert manager.incomplete_path("large-v3-turbo") is None


def test_the_doctor_reports_an_incomplete_model(tmp_path, monkeypatch):
    """‏``diagnose`` يُنادى عند الإقلاع وفي ``--selftest``: يُمسك العطل هناك."""
    import audio.model_manager as mm
    from utils.deps import check_transcription_model

    _hf_cache(tmp_path, links=False, missing=("config.json",))
    monkeypatch.setattr(mm, "default_cache_root", lambda: tmp_path)
    result = check_transcription_model()
    assert result.ok is False
    assert "config.json" in result.detail and str(tmp_path) in result.detail
    assert result.fix                                   # يقول ماذا يفعل


def test_the_doctor_is_quiet_about_a_model_not_downloaded_yet(tmp_path, monkeypatch):
    """الغياب التامّ ليس عطلًا: أوّل تشغيلٍ ينزّله بإذن المستخدم."""
    import audio.model_manager as mm
    from utils.deps import check_transcription_model

    monkeypatch.setattr(mm, "default_cache_root", lambda: tmp_path)
    assert check_transcription_model().ok is True


def test_a_missing_model_is_not_called_incomplete(tmp_path):
    manager = ModelManager(tmp_path)
    assert manager.is_available("large-v3-turbo") is False
    assert manager.incomplete_path("large-v3-turbo") is None


def test_the_pipeline_checks_the_model_before_extracting_audio(tmp_path):
    """فحصُ النموذج في مرحلة التحقّق، لا بعد دقائق من الاستخراج."""
    from core.exceptions import ModelUnavailableError
    from core.pipeline import VideoToDocPipeline

    _hf_cache(tmp_path, links=False, missing=("config.json",))
    pipeline = VideoToDocPipeline.__new__(VideoToDocPipeline)
    pipeline.model_manager = ModelManager(tmp_path)

    class _Whisper:
        engine = "faster-whisper"
        model_size = "large-v3-turbo"

    pipeline.config = type("C", (), {"whisper": _Whisper()})()
    with pytest.raises(ModelUnavailableError):
        pipeline._require_transcription_model(allow_download=False)
    # بإذن التنزيل تمضي المرحلة: الإكمال شأن مرحلة التفريغ بتقدّمها.
    pipeline._require_transcription_model(allow_download=True)


def test_an_injected_model_manager_without_the_check_is_not_a_failure():
    """فحصٌ مبكّر لا بوّابة: البوّابة ``ensure_available`` في مرحلة التفريغ.

    مديرُ نماذج مُحقَن (ADR-008) قد لا يعرض ``is_available``؛ وإسقاطُ عمل
    المستخدم على واجهةٍ ناقصة في مُحقَنٍ بديل خطأ، فيمضي إلى البوّابة.
    """
    from core.pipeline import VideoToDocPipeline

    class _OldStub:
        def ensure_available(self, *a, **k): ...

    pipeline = VideoToDocPipeline.__new__(VideoToDocPipeline)
    pipeline.model_manager = _OldStub()

    class _Whisper:
        engine = "faster-whisper"
        model_size = "large-v3-turbo"

    pipeline.config = type("C", (), {"whisper": _Whisper()})()
    pipeline._require_transcription_model(allow_download=False)   # لا يرفع


def test_without_a_local_folder_the_name_is_used(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    from audio import transcriber as module
    from config.settings import TranscriptionConfig

    seen = {}
    monkeypatch.setattr(module, "WhisperModel",
                        lambda reference, **_: seen.setdefault("reference", reference))
    config = TranscriptionConfig(model_size="large-v3-turbo", download_root=tmp_path)
    module.TranscriptionEngine(config)._load_model("cpu", "int8")
    assert seen["reference"] == "large-v3-turbo"
