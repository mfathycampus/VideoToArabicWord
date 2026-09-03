"""انحدار على تصادم هوية المهمة (Job Identity).

القصة: ``job_dir_for`` كان يبني مجلد المهمة من اسم الملف (``stem``) فقط.
فيديوهان بنفس الاسم من مجلدين مختلفين — أو بامتدادين مختلفين — كانا
ينتهيان إلى نفس مجلد المهمة، فيُفسد أحدهما نواتج الآخر صامتًا: لا خطأ،
لا تحذير، فقط استئناف أو دمج خاطئ. هذا خطر مضاعف لأن الاستئناف والدمج
والمعالجة الدفعية كلها مبنية على هذا المعرّف.

العلاج: بصمة مصدر قصيرة (مسار مطلق + حجم + mtime) تُلحَق باسم مجلد
المهمة. مهمة قديمة (أُنشئت قبل هذا الإصلاح، اسمها بلا بصمة) تبقى قابلة
للاستئناف من مكانها **فقط** إن كانت لنفس الملف بالضبط.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config.settings import AppConfig
from core.job_state import JobManager
from core.pipeline import VideoToDocPipeline
from tests.test_corpus_matrix import StubModelManager, StubTranscriber

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
VIDEO = CORPUS / "h264_mp4.mp4"


def make(out: Path) -> VideoToDocPipeline:
    return VideoToDocPipeline(out, AppConfig(),
                              transcriber=StubTranscriber(),
                              model_manager=StubModelManager())


def test_same_stem_different_directories_get_different_job_dirs(tmp_path):
    """السيناريو المُبلَّغ بالضبط: رياضيات/Lecture.mp4 وفيزياء/Lecture.mp4."""
    math_dir = tmp_path / "رياضيات"
    physics_dir = tmp_path / "فيزياء"
    math_dir.mkdir()
    physics_dir.mkdir()

    math_video = math_dir / "Lecture.mp4"
    physics_video = physics_dir / "Lecture.mp4"
    math_video.write_bytes(VIDEO.read_bytes())
    time.sleep(0.05)
    physics_video.write_bytes(VIDEO.read_bytes() + b"\x00")  # حجم مختلف يكفي لبصمة مختلفة

    pipeline = make(tmp_path / "out")
    dir_a = pipeline.job_dir_for(math_video)
    dir_b = pipeline.job_dir_for(physics_video)

    assert dir_a != dir_b, "ملفان مختلفان بنفس الاسم انتهيا إلى مجلد مهمة واحد"
    assert dir_a.parent == dir_b.parent == tmp_path / "out"


def test_same_stem_different_extensions_get_different_job_dirs(tmp_path):
    mp4 = tmp_path / "Lecture.mp4"
    mkv = tmp_path / "Lecture.mkv"
    mp4.write_bytes(VIDEO.read_bytes())
    mkv.write_bytes(VIDEO.read_bytes() + b"\x00\x00")

    pipeline = make(tmp_path / "out")
    assert pipeline.job_dir_for(mp4) != pipeline.job_dir_for(mkv)


def test_job_dir_for_is_deterministic_across_calls(tmp_path):
    """نفس الملف، بلا تغيير — يجب أن يُعيد نفس المجلد في كل استدعاء."""
    video = tmp_path / "Lecture.mp4"
    video.write_bytes(VIDEO.read_bytes())

    pipeline = make(tmp_path / "out")
    first = pipeline.job_dir_for(video)
    second = pipeline.job_dir_for(video)
    assert first == second


def test_legacy_job_dir_is_reused_for_the_exact_same_source(tmp_path):
    """مهمة أُنشئت بالاسم القديم (بلا بصمة) لنفس الملف تُستأنف من مكانها."""
    video = tmp_path / "Lecture.mp4"
    video.write_bytes(VIDEO.read_bytes())

    out = tmp_path / "out"
    legacy_dir = out / "Lecture"
    legacy_dir.mkdir(parents=True)
    JobManager.create_or_resume(legacy_dir, video)  # يكتب job_state.json بالاسم القديم

    pipeline = make(out)
    assert pipeline.job_dir_for(video) == legacy_dir


def test_legacy_job_dir_is_not_reused_for_a_different_source(tmp_path):
    """مجلد قديم بنفس الاسم لكن لملف مصدر مختلف يجب ألا يُستأنف — هذا التصادم نفسه."""
    old_video = tmp_path / "old_location" / "Lecture.mp4"
    old_video.parent.mkdir()
    old_video.write_bytes(VIDEO.read_bytes())

    out = tmp_path / "out"
    legacy_dir = out / "Lecture"
    legacy_dir.mkdir(parents=True)
    JobManager.create_or_resume(legacy_dir, old_video)

    new_video = tmp_path / "new_location" / "Lecture.mp4"
    new_video.parent.mkdir()
    new_video.write_bytes(VIDEO.read_bytes() + b"\x00")

    pipeline = make(out)
    assert pipeline.job_dir_for(new_video) != legacy_dir


def test_job_registry_display_name_hides_the_fingerprint(tmp_path):
    from core.job_registry import read_job

    video = tmp_path / "Lecture.mp4"
    video.write_bytes(VIDEO.read_bytes())

    pipeline = make(tmp_path / "out")
    job_dir = pipeline.job_dir_for(video)
    JobManager.create_or_resume(job_dir, video)

    summary = read_job(job_dir)
    assert summary is not None
    assert summary.name == "Lecture", f"البصمة تسرّبت إلى الاسم المعروض: {summary.name}"
    assert "__" not in summary.name


def test_humanize_title_strips_fingerprint_for_merged_documents():
    from utils.timestamps import humanize_title

    assert humanize_title("Lecture__a91f4c2d") == "Lecture"
    title = humanize_title("Lecture__a91f4c2d [00_05_00-00_10_00]")
    assert "a91f4c2d" not in title
    assert title.startswith("Lecture")
