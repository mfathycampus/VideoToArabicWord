"""الاستئناف والإلغاء والإيقاف المؤقت على الـ pipeline الحقيقي."""
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from config.settings import AppConfig
from core.exceptions import PipelineCancelledError
from core.job_state import JobManager, Stage
from core.pipeline import VideoToDocPipeline
from tests.test_corpus_matrix import StubModelManager, StubTranscriber
from utils.cancellation import CancellationToken

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
VIDEO = CORPUS / "h264_mp4.mp4"


def make(out: Path, transcriber=None) -> VideoToDocPipeline:
    return VideoToDocPipeline(out, AppConfig(),
                              transcriber=transcriber or StubTranscriber(),
                              model_manager=StubModelManager())


def test_second_run_reuses_artifacts(tmp_path):
    """التشغيل الثاني يجب أن يتخطى المراحل المكتملة، لا أن يعيدها."""
    pipeline = make(tmp_path / "out")
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)

    scenes_before = (job_dir / "scenes.json").read_text(encoding="utf-8")
    mtime_before = (job_dir / "scenes.json").stat().st_mtime_ns

    time.sleep(0.01)
    make(tmp_path / "out").run(VIDEO)

    assert (job_dir / "scenes.json").stat().st_mtime_ns == mtime_before, \
        "أُعيد حساب المشاهد رغم وجود ناتج صالح"
    assert (job_dir / "scenes.json").read_text(encoding="utf-8") == scenes_before


def test_resume_after_deleting_late_artifact(tmp_path):
    """حذف ناتج متأخر يجب أن يعيد حسابه وحده."""
    pipeline = make(tmp_path / "out")
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)

    transcription_mtime = (job_dir / "transcription.json").stat().st_mtime_ns
    (job_dir / "plan.json").unlink()

    result = make(tmp_path / "out").run(VIDEO)
    assert result.exists()
    assert (job_dir / "plan.json").exists(), "لم يُعَد إنشاء الناتج المحذوف"
    assert (job_dir / "transcription.json").stat().st_mtime_ns == transcription_mtime, \
        "أُعيد التفريغ رغم أنه غير متأثر"


def test_corrupted_early_artifact_invalidates_downstream(tmp_path):
    """العبث بناتج مبكر يجب أن يُبطل كل ما بعده."""
    pipeline = make(tmp_path / "out")
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)

    payload = json.loads((job_dir / "transcription.json").read_text(encoding="utf-8"))
    payload["language"] = "en"
    (job_dir / "transcription.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    job = JobManager.create_or_resume(job_dir, VIDEO)
    assert not job.is_stage_complete(Stage.TRANSCRIPTION)
    assert not job.is_stage_complete(Stage.SCENE_DETECTION), "التبعية لم تتسلسل"
    assert not job.is_stage_complete(Stage.MATCHING)


def test_cancellation_stops_and_leaves_resumable_state(tmp_path):
    """الإلغاء يجب أن يوقف بسرعة ويترك حالة قابلة للاستئناف."""
    token = CancellationToken()
    pipeline = make(tmp_path / "out")

    def cancel_soon():
        time.sleep(0.4)
        token.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(PipelineCancelledError):
        pipeline.run(CORPUS / "uhd_4k.mp4", cancel_token=token)

    job_dir = pipeline.job_dir_for(CORPUS / "uhd_4k.mp4")
    state = json.loads((job_dir / "job_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "cancelled"

    # الاستئناف بعد الإلغاء يجب أن ينجح
    result = make(tmp_path / "out").run(CORPUS / "uhd_4k.mp4")
    assert result.exists()


def test_pause_actually_blocks_then_resumes(tmp_path):
    token = CancellationToken()
    token.pause()
    assert token.is_paused()

    released = threading.Event()

    def waiter():
        token.wait_if_paused()
        released.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    assert not released.wait(timeout=0.5), "wait_if_paused لم يحجب التنفيذ"
    token.resume()
    assert released.wait(timeout=2.0), "لم يُستأنف بعد resume"


def test_cancel_while_paused_raises(tmp_path):
    token = CancellationToken()
    token.pause()
    raised = threading.Event()

    def waiter():
        try:
            token.wait_if_paused()
        except PipelineCancelledError:
            raised.set()

    threading.Thread(target=waiter, daemon=True).start()
    time.sleep(0.2)
    token.cancel()
    assert raised.wait(timeout=2.0), "الإلغاء أثناء الإيقاف المؤقت لم يُحرِّر الخيط"


def test_failed_job_records_error_and_is_recoverable(tmp_path):
    pipeline = make(tmp_path / "out")
    with pytest.raises(Exception):
        pipeline.run(CORPUS / "corrupt.mp4")
    job_dir = pipeline.job_dir_for(CORPUS / "corrupt.mp4")
    state = json.loads((job_dir / "job_state.json").read_text(encoding="utf-8"))
    assert state["status"] in ("recoverable", "failed")
    assert state["last_error"], "لم يُسجَّل الخطأ في حالة المهمة"
    assert state["retry_count"] >= 1


def test_temp_is_cleaned_on_success(tmp_path):
    pipeline = make(tmp_path / "out")
    pipeline.run(VIDEO)
    job_dir = pipeline.job_dir_for(VIDEO)
    assert not (job_dir / "temp").exists(), "لم يُنظَّف مجلد temp بعد النجاح"


def test_output_document_is_regenerated_if_deleted(tmp_path):
    pipeline = make(tmp_path / "out")
    result = pipeline.run(VIDEO)
    result.unlink()
    again = make(tmp_path / "out").run(VIDEO)
    assert again.exists() and again.stat().st_size > 0
