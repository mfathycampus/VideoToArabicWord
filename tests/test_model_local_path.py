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
