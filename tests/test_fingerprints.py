from __future__ import annotations

import json
import time
from pathlib import Path

from config.settings import AppConfig, ClipRange
from core.job_state import JobManager, Stage
from core.pipeline import VideoToDocPipeline
from utils.fingerprints import processing_fingerprint, source_fingerprint


def test_source_fingerprint_changes_when_content_changes(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"A" * 4096)
    first = source_fingerprint(video)
    video.write_bytes(b"A" * 2048 + b"B" * 2048)
    second = source_fingerprint(video)
    assert first != second


def test_source_fingerprint_is_stable_without_changes(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"sample")
    assert source_fingerprint(video) == source_fingerprint(video)


def test_job_state_records_source_fingerprint(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"sample")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    assert job.state.source_fingerprint == source_fingerprint(video)


def test_changed_source_does_not_resume_old_state(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"old")
    job_dir = tmp_path / "job"
    job = JobManager.create_or_resume(job_dir, video)
    job.save_artifact("transcription", "transcription.json", json.dumps({"ok": True}))
    job.complete_stage(Stage.TRANSCRIPTION)

    video.write_bytes(b"new-content")
    resumed = JobManager.create_or_resume(job_dir, video)
    assert not resumed.is_stage_complete(Stage.TRANSCRIPTION)
    assert resumed.state.source_fingerprint == source_fingerprint(video)


def test_artifact_processing_fingerprint_invalidates_when_config_changes(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"sample")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    old_fp = processing_fingerprint("transcription", "old-config")
    new_fp = processing_fingerprint("transcription", "new-config")
    job.save_artifact("transcription", "transcription.json", json.dumps({"ok": True}), old_fp)
    job.complete_stage(Stage.TRANSCRIPTION)

    assert job.has_artifact("transcription", old_fp)
    assert not job.has_artifact("transcription", new_fp)


def test_pipeline_stage_fingerprint_changes_with_transcription_config(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"sample")
    config = AppConfig()
    pipeline = VideoToDocPipeline(tmp_path / "out", config=config,
                                  transcriber=object(), model_manager=object())
    clip = ClipRange()
    first = pipeline._stage_fp(Stage.TRANSCRIPTION, video, clip)
    config.whisper.beam_size += 1
    second = pipeline._stage_fp(Stage.TRANSCRIPTION, video, clip)
    assert first != second


def test_display_name_strips_a_real_source_fingerprint(tmp_path):
    """الاسم المعروض ينظّف بصمة بالطول الفعلي، لا بطول مكتوب بخط اليد.

    العطل: ``strip_source_fingerprint`` كان يطابق ``__[0-9a-f]{8}``
    بينما صارت البصمة 20 حرفًا، فتوقّف التنظيف صامتًا وسُرّبت أسماء
    مثل ``Lecture__46b8dd1239bce8b8c7db`` إلى قائمة المهام وعناوين
    المستندات. الاختبار يولّد بصمة حقيقية بدل قيمة ثابتة، فلو تغيّر
    الطول ثانيةً يسقط هنا لا عند المستخدم.
    """
    from utils.fingerprints import FINGERPRINT_LENGTH, source_fingerprint
    from utils.timestamps import humanize_title, strip_source_fingerprint

    video = tmp_path / "Lecture.mp4"
    video.write_bytes(b"\x00\x11\x22" * 64)
    fingerprint = source_fingerprint(video)

    assert len(fingerprint) == FINGERPRINT_LENGTH
    assert strip_source_fingerprint(f"Lecture__{fingerprint}") == "Lecture"
    assert fingerprint not in humanize_title(f"Lecture__{fingerprint}")
    # نطاق زمني ملحق بعد البصمة لا يمنع التنظيف
    assert strip_source_fingerprint(
        f"Lecture__{fingerprint} [00_05_00-00_10_00]"
    ) == "Lecture [00_05_00-00_10_00]"


def test_legacy_eight_character_fingerprints_are_still_stripped():
    """مهام أُنشئت قبل إطالة البصمة تبقى معروضة بأسماء نظيفة."""
    from utils.timestamps import strip_source_fingerprint

    assert strip_source_fingerprint("Lecture__a91f4c2d") == "Lecture"
