import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.job_state import JobManager, JobStatus, Stage, STAGE_PROGRESS


def test_resume_skips_completed_stages(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    job.begin_stage(Stage.TRANSCRIPTION)
    job.save_artifact("transcription", "transcription.json", json.dumps({"language": "ar"}))
    job.complete_stage(Stage.TRANSCRIPTION)

    resumed = JobManager.create_or_resume(tmp_path / "job", video)
    assert resumed.is_stage_complete(Stage.TRANSCRIPTION)
    assert resumed.load_artifact("transcription")["language"] == "ar"


def test_deleted_artifact_invalidates_stage(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    job.save_artifact("transcription", "transcription.json", json.dumps({"language": "ar"}))
    job.complete_stage(Stage.TRANSCRIPTION)

    (tmp_path / "job" / "transcription.json").unlink()
    resumed = JobManager.create_or_resume(tmp_path / "job", video)
    assert not resumed.is_stage_complete(Stage.TRANSCRIPTION), \
        "مرحلة اختفى ناتجها يجب ألا تُعتبر مكتملة"


def test_tampered_artifact_invalidates_stage(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    job.save_artifact("transcription", "transcription.json", json.dumps({"language": "ar"}))
    job.complete_stage(Stage.TRANSCRIPTION)

    (tmp_path / "job" / "transcription.json").write_text('{"language":"en"}', encoding="utf-8")
    resumed = JobManager.create_or_resume(tmp_path / "job", video)
    assert not resumed.is_stage_complete(Stage.TRANSCRIPTION), \
        "checksum مختلف يجب أن يُبطل المرحلة"


def test_invalidation_cascades_to_later_stages(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    for stage, key, name in [
        (Stage.TRANSCRIPTION, "transcription", "t.json"),
        (Stage.SCENE_DETECTION, "scenes", "s.json"),
        (Stage.MATCHING, "matches", "m.json"),
    ]:
        job.save_artifact(key, name, json.dumps({}))
        job.complete_stage(stage)

    (tmp_path / "job" / "t.json").unlink()
    resumed = JobManager.create_or_resume(tmp_path / "job", video)
    assert not resumed.is_stage_complete(Stage.TRANSCRIPTION)
    assert not resumed.is_stage_complete(Stage.SCENE_DETECTION), "التبعية يجب أن تتسلسل"
    assert not resumed.is_stage_complete(Stage.MATCHING)


def test_corrupt_state_file_starts_fresh(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job_dir = tmp_path / "job"; job_dir.mkdir()
    (job_dir / "job_state.json").write_text("{ this is not json", encoding="utf-8")
    job = JobManager.create_or_resume(job_dir, video)
    assert job.state.status == JobStatus.QUEUED


def test_different_video_starts_fresh(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"x"); b.write_bytes(b"y")
    job = JobManager.create_or_resume(tmp_path / "job", a)
    job.complete_stage(Stage.TRANSCRIPTION)
    other = JobManager.create_or_resume(tmp_path / "job", b)
    assert not other.is_stage_complete(Stage.TRANSCRIPTION)


def test_progress_ranges_are_contiguous_and_monotonic():
    ranges = list(STAGE_PROGRESS.values())
    assert ranges[0][0] == 0.0 and ranges[-1][1] == 100.0
    for (_, end), (start, _) in zip(ranges, ranges[1:]):
        assert end == start, "نطاقات التقدّم يجب أن تكون متصلة بلا فجوات"


def test_stage_progress_mapping(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    assert job.stage_progress(Stage.TRANSCRIPTION, 0.0) == 20.0
    assert job.stage_progress(Stage.TRANSCRIPTION, 1.0) == 55.0
    assert job.stage_progress(Stage.TRANSCRIPTION, 0.5) == 37.5
    assert job.stage_progress(Stage.TRANSCRIPTION, 5.0) == 55.0  # مقصوص


def test_retry_then_permanent_failure(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    job = JobManager.create_or_resume(tmp_path / "job", video)
    for _ in range(3):
        job.fail("boom")
        assert job.state.status == JobStatus.RECOVERABLE
    job.fail("boom")
    assert job.state.status == JobStatus.FAILED
