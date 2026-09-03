"""اختبارات ميزات الجولة السابعة.

كل قسم يقابل ميزة مطلوبة، ومكتوب ليفشل على الشيفرة قبلها.
"""
import json
from pathlib import Path

import pytest

from config.schemas import (
    AudioSegment,
    DocumentBlock,
    DocumentPlan,
    DocumentSection,
    TranscriptionResult,
    WordTimestamp,
)
from config.settings import AppConfig, ClipRange


# ---------------------------------------------------------------------
# 1 · قصّ المقطع
# ---------------------------------------------------------------------
@pytest.mark.parametrize("start,end,expected_start,expected_end", [
    (None, None, 0.0, None),
    (30, 90, 30.0, 90.0),
    ("00:30", "01:30", 30.0, 90.0),
    ("00:01:00", "00:02:30", 60.0, 150.0),
    ("", "05:00", 0.0, 300.0),
])
def test_clip_range_accepts_seconds_and_timestamps(start, end, expected_start,
                                                   expected_end):
    clip = ClipRange.parse(start, end)
    assert clip.start_seconds == expected_start
    assert clip.end_seconds == expected_end


def test_clip_range_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        ClipRange.parse("02:00", "01:00")


def test_empty_range_is_not_partial():
    assert ClipRange.parse(None, None).is_partial is False
    assert ClipRange.parse("00:10", None).is_partial is True


def test_job_directory_separates_ranges(tmp_path):
    """نطاق مختلف = مهمة مختلفة، وإلا أفسدت التجربة المعالجة الكاملة."""
    from core.pipeline import VideoToDocPipeline

    pipeline = VideoToDocPipeline(tmp_path, AppConfig())
    source = tmp_path / "محاضرة.mp4"

    full = pipeline.job_dir_for(source)
    partial = pipeline.job_dir_for(source, ClipRange.parse("00:00", "05:00"))
    other = pipeline.job_dir_for(source, ClipRange.parse("05:00", "10:00"))

    assert full != partial != other
    assert full == pipeline.job_dir_for(source, ClipRange())


def test_transcript_timestamps_shift_by_clip_start():
    """مقطع يبدأ عند الدقيقة 30 يجب أن تبقى توقيتاته مطلقة."""
    from core.pipeline import _shift_transcript

    words = [WordTimestamp(word="كلمة", start=1.0, end=1.5)]
    transcript = TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[AudioSegment(id=1, start=1.0, end=4.0, text_raw="نص",
                               text_clean="نص", words=words)],
        words=list(words))

    shifted = _shift_transcript(transcript, 1800.0)
    assert shifted.segments[0].start == 1801.0
    assert shifted.segments[0].end == 1804.0
    assert shifted.segments[0].words[0].start == 1801.0
    assert shifted.words[0].end == 1801.5
    # الأصل لم يُمسّ
    assert transcript.segments[0].start == 1.0


def test_no_shift_when_range_starts_at_zero():
    from core.pipeline import _shift_transcript

    transcript = TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[AudioSegment(id=1, start=1.0, end=2.0,
                               text_raw="ن", text_clean="ن")])
    assert _shift_transcript(transcript, 0.0) is transcript


def test_audio_extraction_passes_range_and_filters(tmp_path):
    from utils.ffmpeg_service import FFmpegService

    service = FFmpegService()
    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(args, timeout=3600):
        captured["args"] = args
        output = Path(args[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x" * 16)
        return Result()

    service._run = fake_run
    service.extract_audio(tmp_path / "v.mp4", tmp_path / "a.wav",
                          start_seconds=30.0, end_seconds=90.0, denoise=True)

    args = captured["args"]
    assert "-ss" in args and args[args.index("-ss") + 1] == "30.000"
    # -to يُحسب من نقطة البدء
    assert "-to" in args and args[args.index("-to") + 1] == "60.000"
    assert "-af" in args and "afftdn" in args[args.index("-af") + 1]
    # -ss يسبق -i للبحث السريع
    assert args.index("-ss") < args.index("-i")


def test_audio_extraction_without_options_is_unchanged(tmp_path):
    from utils.ffmpeg_service import FFmpegService

    service = FFmpegService()
    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(args, timeout=3600):
        captured["args"] = args
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"x" * 16)
        return Result()

    service._run = fake_run
    service.extract_audio(tmp_path / "v.mp4", tmp_path / "a.wav")
    assert "-ss" not in captured["args"]
    assert "-to" not in captured["args"]
    assert "-af" not in captured["args"]


# ---------------------------------------------------------------------
# 2 · قاموس المصطلحات
# ---------------------------------------------------------------------
def test_glossary_reaches_the_initial_prompt():
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine

    config = AppConfig().whisper.model_copy(
        update={"glossary": "بوذا، اليوغا، PowerSchool"})
    prompt = TranscriptionEngine(config)._effective_prompt()
    assert "بوذا" in prompt and "PowerSchool" in prompt
    assert "محاضرة تعليمية" in prompt        # الموجّه الأساسي باقٍ


def test_empty_glossary_leaves_the_prompt_alone():
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine

    engine = TranscriptionEngine(AppConfig().whisper)
    assert engine._effective_prompt() == AppConfig().whisper.initial_prompt


def test_long_glossary_is_truncated():
    """حدّ Whisper 224 رمزًا — قاموس ضخم يزيح الموجّه الأساسي."""
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine

    config = AppConfig().whisper.model_copy(
        update={"glossary": "مصطلح " * 500})
    prompt = TranscriptionEngine(config)._effective_prompt()
    assert len(prompt) < 700


# ---------------------------------------------------------------------
# 3 · تصدير التفريغ نصًّا
# ---------------------------------------------------------------------
def _transcript() -> TranscriptionResult:
    return TranscriptionResult(
        language="ar", full_text_raw="", full_text_clean="",
        segments=[
            AudioSegment(id=1, start=0.5, end=3.0, text_raw="الجملة الأولى.",
                         text_clean="الجملة الأولى."),
            AudioSegment(id=2, start=3.1, end=6.0, text_raw="الجملة الثانية.",
                         text_clean="الجملة الثانية."),
            # فجوة صمت طويلة ⇒ فقرة جديدة
            AudioSegment(id=3, start=20.0, end=24.0, text_raw="بعد الصمت.",
                         text_clean="بعد الصمت."),
        ])


def test_text_export_merges_segments_into_paragraphs():
    from document.transcript_export import render_text

    text = render_text(_transcript())
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    assert len(paragraphs) == 2, "الفجوة الطويلة يجب أن تبدأ فقرة"
    assert "الجملة الأولى. الجملة الثانية." in paragraphs[0]


def test_text_export_can_include_timecodes():
    from document.transcript_export import render_text

    assert "[00:00:00]" in render_text(_transcript(), with_timecodes=True)


def test_markdown_export_has_time_headings():
    from document.transcript_export import render_markdown

    markdown = render_markdown(_transcript(), "محاضرة تجريبية")
    assert markdown.startswith("# محاضرة تجريبية")
    assert "## 00:00:20" in markdown


def test_write_transcript_produces_every_format(tmp_path):
    from document.transcript_export import write_transcript

    written = write_transcript(_transcript(), tmp_path / "درس", "درس")
    assert {p.suffix for p in written} == {".txt", ".md", ".srt", ".vtt"}
    assert all(p.exists() and p.stat().st_size > 0 for p in written)


def test_empty_transcript_exports_nothing(tmp_path):
    from document.transcript_export import write_transcript

    empty = TranscriptionResult(language="ar", full_text_raw="",
                                full_text_clean="", segments=[])
    assert write_transcript(empty, tmp_path / "x", "x") == []


# ---------------------------------------------------------------------
# 4 · المعالجة الدفعية
# ---------------------------------------------------------------------
def test_iter_media_finds_video_and_audio_only(tmp_path):
    from core.batch import iter_media

    for name in ("b.mp4", "a.m4a", "c.wav", "notes.txt", "cover.png"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.mp4").write_bytes(b"x")

    names = [p.name for p in iter_media(tmp_path)]
    assert names == ["a.m4a", "b.mp4", "c.wav"], "مرتّبة ومقصورة على الوسائط"
    assert "deep.mp4" in [p.name for p in iter_media(tmp_path, recursive=True)]


def test_batch_continues_after_a_failing_file(tmp_path, monkeypatch):
    """ملف تالف بين سليمين يجب ألا يُضيّع الدفعة كلها."""
    import core.batch as batch

    sources = [tmp_path / f"{i}.mp4" for i in range(3)]
    for source in sources:
        source.write_bytes(b"x")

    class FakePipeline:
        def __init__(self, *_a, **_k):
            pass

        def job_dir_for(self, source, clip=None):
            return tmp_path / source.stem

        def run(self, source, **_kwargs):
            if source.name == "1.mp4":
                raise RuntimeError("ملف تالف")
            return tmp_path / f"{source.stem}.docx"

    import core.pipeline
    monkeypatch.setattr(core.pipeline, "VideoToDocPipeline", FakePipeline)

    results = batch.process_folder(sources, tmp_path, AppConfig())
    assert [r.ok for r in results] == [True, False, True]
    assert "ملف تالف" in results[1].error


def test_batch_stops_on_cancellation(tmp_path, monkeypatch):
    import core.batch as batch
    from core.exceptions import PipelineCancelledError
    from utils.cancellation import CancellationToken

    sources = [tmp_path / f"{i}.mp4" for i in range(4)]
    for source in sources:
        source.write_bytes(b"x")
    token = CancellationToken()

    class FakePipeline:
        def __init__(self, *_a, **_k):
            pass

        def job_dir_for(self, source, clip=None):
            return tmp_path / source.stem

        def run(self, source, **_kwargs):
            if source.name == "1.mp4":
                raise PipelineCancelledError("أُلغيت")
            return tmp_path / f"{source.stem}.docx"

    import core.pipeline
    monkeypatch.setattr(core.pipeline, "VideoToDocPipeline", FakePipeline)

    results = batch.process_folder(sources, tmp_path, AppConfig(),
                                   cancel_token=token)
    assert len(results) == 2, "الإلغاء يوقف الدفعة فورًا"


# ---------------------------------------------------------------------
# 5 · لوحة المهام
# ---------------------------------------------------------------------
def _write_job(root: Path, name: str, status: str, progress: float,
               source: Path, with_plan: bool = True) -> Path:
    job_dir = root / name
    job_dir.mkdir(parents=True)
    (job_dir / "job_state.json").write_text(json.dumps({
        "schema_version": "1.3", "job_id": name, "video_path": str(source),
        "status": status, "stage": "keyframes", "progress": progress,
        "completed_stages": [], "artifacts": {},
        "updated_at": "2026-09-01T10:00:00+00:00",
    }), encoding="utf-8")
    if with_plan:
        (job_dir / "plan.json").write_text("{}", encoding="utf-8")
        (job_dir / f"{name}.docx").write_bytes(b"PK\x03\x04")
    return job_dir


def test_list_jobs_reads_state_and_sorts(tmp_path):
    from core.job_registry import list_jobs

    source = tmp_path / "محاضرة.mp4"
    source.write_bytes(b"x")
    output = tmp_path / "out"
    _write_job(output, "أولى", "completed", 100.0, source)
    _write_job(output, "ثانية", "recoverable", 60.0, source)
    (output / "ليست مهمة").mkdir()

    jobs = list_jobs(output)
    assert len(jobs) == 2, "المجلدات بلا job_state تُتجاهل"
    by_name = {j.name: j for j in jobs}
    assert by_name["أولى"].is_complete
    assert by_name["أولى"].status_label == "اكتملت"
    assert by_name["ثانية"].can_resume, "مصدرها موجود وغير مكتملة"
    assert all(j.can_rebuild for j in jobs)


def test_job_with_missing_source_cannot_resume(tmp_path):
    from core.job_registry import list_jobs

    output = tmp_path / "out"
    _write_job(output, "مهمة", "recoverable", 40.0, tmp_path / "ذهب.mp4")
    assert list_jobs(output)[0].can_resume is False


def test_corrupt_job_state_is_surfaced_not_hidden(tmp_path):
    from core.job_registry import read_job

    job_dir = tmp_path / "تالفة"
    job_dir.mkdir()
    (job_dir / "job_state.json").write_text("{ ليس JSON", encoding="utf-8")
    summary = read_job(job_dir)
    assert summary is not None and summary.status_label == "حالة تالفة"


# ---------------------------------------------------------------------
# 6 · الدمج في مستند واحد
# ---------------------------------------------------------------------
def _plan(prefix: str) -> DocumentPlan:
    plan = DocumentPlan(
        title=f"جزء {prefix}", sections=[DocumentSection(
            title="القسم الأول", level=1, blocks=[
                DocumentBlock(kind="paragraph", text="نص.", segment_ids=[1, 2]),
                DocumentBlock(kind="figure", timestamp=5.0, image_id=1,
                              image_filename="0001_00-00-05-000.jpg"),
            ])],
        total_segments_in=2, total_segments_out=2,
        total_figures_in=1, total_figures_out=1)
    return plan


def test_merge_shifts_ids_so_nothing_collides(tmp_path):
    from document.merge import MergePart, merge_plans
    from document.planner import assert_lossless

    parts = [MergePart(plan=_plan("أ"), title="الجزء الأول",
                       images_dir=tmp_path, prefix="p01"),
             MergePart(plan=_plan("ب"), title="الجزء الثاني",
                       images_dir=tmp_path, prefix="p02")]
    merged = merge_plans(parts, "المقرّر كاملًا")

    assert merged.total_segments_in == 4
    assert merged.total_segments_out == 4, "معرّفات متصادمة تُسقط عنصرًا"
    assert merged.total_figures_in == 2 and merged.total_figures_out == 2
    assert_lossless(merged)         # لا ضياع ولا تكرار

    filenames = [b.image_filename for s in merged.sections for b in s.blocks
                 if b.image_filename]
    assert filenames == ["p01_0001_00-00-05-000.jpg",
                         "p02_0001_00-00-05-000.jpg"]


def test_merge_nests_each_part_under_its_own_heading(tmp_path):
    from document.merge import MergePart, merge_plans

    parts = [MergePart(plan=_plan("أ"), title="الجزء الأول",
                       images_dir=tmp_path, prefix="p01"),
             MergePart(plan=_plan("ب"), title="الجزء الثاني",
                       images_dir=tmp_path, prefix="p02")]
    merged = merge_plans(parts, "المقرّر")
    titles = [(s.title, s.level) for s in merged.sections]
    assert titles[0] == ("الجزء الأول", 1)
    assert titles[1][1] == 2, "أقسام الجزء تنزل درجة تحت عنوانه"


def test_merge_requires_two_parts(tmp_path):
    from document.merge import merge_plans

    with pytest.raises(ValueError):
        merge_plans([], "بلا أجزاء")


def test_collect_images_prefixes_and_avoids_collisions(tmp_path):
    from document.merge import MergePart, collect_images

    parts = []
    for prefix in ("p01", "p02"):
        source = tmp_path / prefix
        source.mkdir()
        (source / "0001_00-00-05-000.jpg").write_bytes(b"img")
        parts.append(MergePart(plan=_plan(prefix), title=prefix,
                               images_dir=source, prefix=prefix))

    target = tmp_path / "merged"
    assert collect_images(parts, target) == 2
    assert sorted(p.name for p in target.iterdir()) == [
        "p01_0001_00-00-05-000.jpg", "p02_0001_00-00-05-000.jpg"]


# ---------------------------------------------------------------------
# 7 · تكامل: النطاق ووضع التفريغ عبر الـ pipeline كاملًا
# ---------------------------------------------------------------------
CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"


class _StubTranscriber:
    """تفريغ ثابت بتوقيتات نسبية — كما يعيدها المحرّك على صوت مقصوص."""

    info = type("I", (), {"name": "faster-whisper"})()
    allow_download = False

    def transcribe(self, *_a, **_k):
        return TranscriptionResult(
            language="ar", full_text_raw="نص", full_text_clean="نص",
            segments=[
                AudioSegment(id=1, start=0.5, end=3.0,
                             text_raw="الجملة الأولى.",
                             text_clean="الجملة الأولى."),
                AudioSegment(id=2, start=3.5, end=6.0,
                             text_raw="الجملة الثانية.",
                             text_clean="الجملة الثانية."),
            ])


class _StubModels:
    def __init__(self, *_a, **_k):
        pass

    def ensure_available(self, *_a, **_k):
        return None


def _pipeline(out_dir: Path, config=None):
    from core.pipeline import VideoToDocPipeline

    return VideoToDocPipeline(out_dir, config or AppConfig(),
                              transcriber=_StubTranscriber(),
                              model_manager=_StubModels())


@pytest.mark.skipif(not (CORPUS / "h264_mp4.mp4").exists(),
                    reason="مصفوفة الاختبار غير مولَّدة")
def test_clip_range_end_to_end(tmp_path):
    """معالجة جزء من الملف: توقيتات مطلقة، ومشاهد داخل النطاق."""
    video = CORPUS / "h264_mp4.mp4"
    clip = ClipRange.parse("00:05", "00:15")
    docx = _pipeline(tmp_path).run(video, clip=clip)

    assert docx.exists()
    job_dir = docx.parent
    assert "clip-5-15" in job_dir.name, "النطاق يجب أن يميّز مجلد المهمة"

    transcript = json.loads(
        (job_dir / "transcription.json").read_text(encoding="utf-8"))
    # 0.5 داخل المقطع ⇒ 5.5 بزمن المصدر
    assert transcript["segments"][0]["start"] == pytest.approx(5.5)

    scenes = json.loads((job_dir / "scenes.json").read_text(encoding="utf-8"))
    for scene in scenes["scenes"]:
        assert scene["start"] >= 5.0 - 0.5, "مشهد قبل بداية النطاق"
        assert scene["start"] <= 15.5, "مشهد بعد نهاية النطاق"

    keyframes = json.loads(
        (job_dir / "keyframes.json").read_text(encoding="utf-8"))
    for keyframe in keyframes["keyframes"]:
        assert 4.5 <= keyframe["timestamp"] <= 15.5


@pytest.mark.skipif(not (CORPUS / "h264_mp4.mp4").exists(),
                    reason="مصفوفة الاختبار غير مولَّدة")
def test_full_run_and_clipped_run_do_not_collide(tmp_path):
    """تجربة على مقطع يجب ألا تُفسد نواتج المعالجة الكاملة."""
    video = CORPUS / "h264_mp4.mp4"
    pipeline = _pipeline(tmp_path)
    full = pipeline.run(video)
    clipped = pipeline.run(video, clip=ClipRange.parse("00:05", "00:12"))
    assert full.parent != clipped.parent
    assert full.exists() and clipped.exists()


@pytest.mark.skipif(not (CORPUS / "h264_mp4.mp4").exists(),
                    reason="مصفوفة الاختبار غير مولَّدة")
def test_transcript_only_skips_the_document(tmp_path):
    video = CORPUS / "h264_mp4.mp4"
    result = _pipeline(tmp_path).run(video, transcript_only=True)

    assert result.suffix == ".txt"
    job_dir = result.parent
    for suffix in (".txt", ".md", ".srt", ".vtt"):
        assert (job_dir / f"{video.stem}{suffix}").exists(), suffix
    assert not list(job_dir.glob("*.docx")), "لا مستند في وضع التفريغ فقط"
    assert not (job_dir / "keyframes.json").exists()
    assert not (job_dir / "scenes.json").exists()

    text = (job_dir / f"{video.stem}.txt").read_text(encoding="utf-8")
    assert "الجملة الأولى" in text


@pytest.mark.skipif(not (CORPUS / "h264_mp4.mp4").exists(),
                    reason="مصفوفة الاختبار غير مولَّدة")
def test_merge_two_jobs_into_one_document(tmp_path):
    """دمج مهمّتين حقيقيتين: مستند واحد بصور الطرفين."""
    import zipfile

    video = CORPUS / "h264_mp4.mp4"
    pipeline = _pipeline(tmp_path)
    first = pipeline.run(video, clip=ClipRange.parse("00:00", "00:10"))
    second = pipeline.run(video, clip=ClipRange.parse("00:10", "00:20"))

    target = tmp_path / "المقرّر كاملًا.docx"
    merged = pipeline.merge_documents([first.parent, second.parent], target)

    assert merged.exists()
    with zipfile.ZipFile(merged) as archive:
        media = [n for n in archive.namelist() if n.startswith("word/media/")]
    assert len(media) >= 2, "المستند المدموج يجب أن يحمل صور الجزأين"
