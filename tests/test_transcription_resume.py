"""الاستئناف **داخل** التفريغ — النجاة من انقطاعٍ بعد ساعات.

سبب وجود هذا الملف رقمٌ مقيس: التفريغ 85٪ من زمن التشغيل، ومحاضرة ثلاث
ساعات تستغرق نحو أربع ساعات ونصف على جهاز المستخدم. وكان الاستئناف على
مستوى **المرحلة** وحدها: انقطاع كهرباء أو إعادة تشغيل ويندوز في الساعة
الرابعة يُضيع الأربع كلّها ويبدأ من الصفر.

والخطر الحقيقي في هذه الميزة ليس ألّا تستأنف، بل أن تستأنف **خطأً**:
مقطعٌ يتكرّر على حدّ القطع، أو مقطعٌ يسقط، أو توقيتاتٌ تُزاح فتصير
الصور والترجمة على أزمنة لا تطابق الكلام. ولذلك أكثر ما هنا حرّاسٌ على
ذلك لا على «هل استأنف».
"""
from __future__ import annotations

import json

import pytest

from audio.transcriber import TranscriptionEngine
from config.schemas import (AudioSegment, TranscriptionCheckpoint,
                            WordTimestamp)
from config.settings import TranscriptionConfig


class _Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end
        self.probability = 0.9


class _Seg:
    """مقطعٌ بالشكل الذي يسلّمه مولّد faster-whisper."""

    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
        self.words = [_Word(text, start, end)]


class _Info:
    def __init__(self, duration):
        self.duration = duration
        self.language = "ar"


class _FakeModel:
    """نموذج يسلّم مقاطع معلومة — القياس هنا على المنطق لا على الأوزان."""

    def __init__(self, segments, duration):
        self.segments, self.duration = segments, duration
        self.seen_options = None

    def transcribe(self, _audio, **options):
        self.seen_options = options
        return iter(self.segments), _Info(self.duration)


def _engine(monkeypatch, segments, duration) -> TranscriptionEngine:
    engine = TranscriptionEngine(TranscriptionConfig(word_timestamps=True))
    model = _FakeModel(segments, duration)
    monkeypatch.setattr(engine, "_load_model", lambda *a, **k: model)
    engine.keep_model_loaded = True
    return engine


def _saved(resume_at: float) -> TranscriptionCheckpoint:
    """نقطة حفظ لمقطعين انتهيا عند ``resume_at``."""
    words = [WordTimestamp(word="الأول", start=0.0, end=1.0),
             WordTimestamp(word="الثاني", start=1.0, end=resume_at)]
    return TranscriptionCheckpoint(
        processing_fingerprint="fp",
        audio_seconds=100.0,
        resume_at=resume_at,
        segments=[
            AudioSegment(id=1, start=0.0, end=1.0, text_raw="الأول",
                         text_clean="الأول", words=words[:1]),
            AudioSegment(id=2, start=1.0, end=resume_at, text_raw="الثاني",
                         text_clean="الثاني", words=words[1:]),
        ],
        words=words,
    )


# ── الإزاحة: أخطر ما في الميزة ────────────────────────────────────────

def test_resumed_segments_keep_absolute_time(monkeypatch, tmp_path):
    """الصوت المُمرَّر بعد الاستئناف بقيّةٌ تبدأ من الصفر.

    بلا الإزاحة تخرج كل المقاطع التالية بتوقيتٍ أصغر من الحقيقي، فتُطابَق
    الصور على لحظاتٍ خاطئة وتظهر الترجمة قبل الكلام — عطلٌ صامت لا يرفع
    استثناءً ويُفسد المستند كلّه.
    """
    engine = _engine(monkeypatch, [_Seg(0.0, 5.0, "الثالث")], 95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=_saved(5.0))

    fresh = [s for s in result.segments if s.text_raw == "الثالث"]
    assert fresh, "لم يظهر المقطع الجديد"
    assert fresh[0].start == pytest.approx(5.0)
    assert fresh[0].end == pytest.approx(10.0)


def test_resumed_word_timestamps_are_shifted_too(monkeypatch, tmp_path):
    """توقيت الكلمات هو ما تُبنى عليه الترجمة وقسمة الفقرات."""
    engine = _engine(monkeypatch, [_Seg(0.0, 5.0, "الثالث")], 95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=_saved(5.0))

    late = [w for w in result.words if w.word == "الثالث"]
    assert late and late[0].start == pytest.approx(5.0)


def test_nothing_is_lost_and_nothing_is_duplicated(monkeypatch, tmp_path):
    """‏ADR-010: كل مقطع صوتي يظهر مرّة واحدة بالضبط."""
    engine = _engine(monkeypatch, [_Seg(0.0, 5.0, "الثالث"),
                                   _Seg(5.0, 9.0, "الرابع")], 95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=_saved(5.0))

    texts = [s.text_raw for s in result.segments]
    assert texts == ["الأول", "الثاني", "الثالث", "الرابع"]
    assert len(set(texts)) == len(texts), "تكرار على حدّ الاستئناف"

    # لا فجوة زمنية ولا تراكب
    for earlier, later in zip(result.segments, result.segments[1:]):
        assert later.start >= earlier.start


def test_ids_stay_unique_across_the_seam(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, [_Seg(0.0, 5.0, "الثالث")], 95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=_saved(5.0))
    ids = [s.id for s in result.segments]
    assert len(set(ids)) == len(ids), f"معرّفات متكرّرة: {ids}"


def test_the_joined_text_includes_both_halves(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, [_Seg(0.0, 5.0, "الثالث")], 95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=_saved(5.0))
    assert "الأول" in result.full_text_raw
    assert "الثالث" in result.full_text_raw


def test_reshaping_runs_over_the_joined_list(monkeypatch, tmp_path):
    """الطيّ والقسمة على القائمة المجموعة لا على كل نصفٍ وحده.

    حلقةُ تكرارٍ تبدأ قبل حدّ الاستئناف وتكمل بعده لا يراها إلا الطيّ
    على القائمة كاملة — وهي بالضبط الحالة التي يُنتجها الصمت الطويل
    الذي يقع عنده الانقطاع عادةً.
    """
    repeated = "شكرًا لكم"
    saved = _saved(5.0)
    saved.segments[1].text_raw = saved.segments[1].text_clean = repeated
    engine = _engine(monkeypatch,
                     [_Seg(0.0, 2.0, repeated), _Seg(2.0, 4.0, repeated)],
                     95.0)
    result = engine.transcribe(tmp_path / "rest.wav", resume=saved)
    assert result.full_text_clean.count(repeated) < 3, (
        "حلقة التكرار عبَرت حدّ الاستئناف بلا طيّ")


# ── التقدّم ───────────────────────────────────────────────────────────

def test_progress_counts_from_the_whole_audio(monkeypatch, tmp_path):
    """استئنافٌ عند 80٪ يجب أن يبدأ الشريط من 80٪ لا من الصفر.

    شريطٌ يعود إلى الصفر بعد أربع ساعات يقول للمستخدم إن عمله ضاع —
    وهو ما وُجدت الميزة لتمنعه.
    """
    saved = _saved(80.0)
    saved.audio_seconds = 100.0
    engine = _engine(monkeypatch, [_Seg(0.0, 10.0, "بقية")], 20.0)

    seen: list[float] = []
    engine.transcribe(tmp_path / "rest.wav",
                      progress_callback=lambda f, _m: seen.append(f),
                      resume=saved)
    assert seen and seen[0] > 0.8


def test_progress_without_a_resume_is_unchanged(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, [_Seg(0.0, 10.0, "أول")], 100.0)
    seen: list[float] = []
    engine.transcribe(tmp_path / "a.wav",
                      progress_callback=lambda f, _m: seen.append(f))
    assert seen and seen[0] == pytest.approx(0.1, abs=0.01)


# ── كتابة نقطة الحفظ ──────────────────────────────────────────────────

def test_a_checkpoint_is_written_while_transcribing(monkeypatch, tmp_path):
    import audio.transcriber as transcriber

    # الحدّ على زمن المعالجة، والاختبار لا ينتظر دقيقة.
    monkeypatch.setattr(transcriber, "CHECKPOINT_SECONDS", 0.0)
    engine = _engine(monkeypatch, [_Seg(0.0, 3.0, "أ"), _Seg(3.0, 6.0, "ب")],
                     100.0)

    calls = []
    engine.transcribe(tmp_path / "a.wav",
                      checkpoint=lambda segs, words, at:
                          calls.append((len(segs), at)))
    assert calls, "لم تُكتب أي نقطة حفظ"
    assert calls[-1][1] == pytest.approx(6.0), "حدّ الاستئناف ليس نهاية مقطع"


def test_the_checkpoint_carries_raw_segments_not_reshaped(monkeypatch,
                                                          tmp_path):
    """ما يُحفظ خامٌ قبل الطيّ والقسمة — وإلا طُبّقا مرّتين على النصف
    الأول عند الاستئناف."""
    import audio.transcriber as transcriber

    monkeypatch.setattr(transcriber, "CHECKPOINT_SECONDS", 0.0)
    # مقطع طويل جدًّا: القسمة النهائية ستكسره، ونقطة الحفظ لا.
    engine = _engine(monkeypatch, [_Seg(0.0, 40.0, "كلام طويل")], 100.0)

    saved = []
    result = engine.transcribe(tmp_path / "a.wav",
                               checkpoint=lambda segs, w, at:
                                   saved.append(len(segs)))
    assert saved and saved[-1] == 1
    assert len(result.segments) >= 1


def test_no_checkpoint_callback_is_still_fine(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, [_Seg(0.0, 3.0, "أ")], 100.0)
    assert engine.transcribe(tmp_path / "a.wav").segments


# ── دورة حياة الملف على مستوى الـ pipeline ────────────────────────────

class _StubJob:
    def __init__(self, job_dir):
        self.job_dir = job_dir


def _pipeline(tmp_path, supports_resume: bool = True):
    from core.pipeline import VideoToDocPipeline

    class _Engine:
        supports_resume = False

    pipeline = VideoToDocPipeline.__new__(VideoToDocPipeline)
    pipeline.transcriber = _Engine()
    type(pipeline.transcriber).supports_resume = supports_resume
    return pipeline


def test_a_checkpoint_for_another_configuration_is_ignored(tmp_path):
    """بصمة مختلفة تعني مقاطع من إعدادٍ آخر — خلطُها إفسادٌ صامت."""
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    (tmp_path / "transcription.partial.json").write_text(
        _saved(5.0).model_dump_json(), encoding="utf-8")

    assert pipeline._load_checkpoint(job, "بصمة-أخرى") is None
    assert not (tmp_path / "transcription.partial.json").exists()


def test_a_matching_checkpoint_is_loaded(tmp_path):
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    (tmp_path / "transcription.partial.json").write_text(
        _saved(5.0).model_dump_json(), encoding="utf-8")

    loaded = pipeline._load_checkpoint(job, "fp")
    assert loaded is not None and loaded.resume_at == pytest.approx(5.0)


def test_a_truncated_checkpoint_is_ignored_not_fatal(tmp_path):
    """الملف المقطوع هو ما يُنتجه الانقطاع نفسه — وُجدت الكتابة الذرّية
    لهذا، وهذا حارسها الثاني."""
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    (tmp_path / "transcription.partial.json").write_text(
        '{"segments": [{"id": 1,', encoding="utf-8")

    assert pipeline._load_checkpoint(job, "fp") is None


def test_an_empty_checkpoint_is_ignored(tmp_path):
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    empty = TranscriptionCheckpoint(processing_fingerprint="fp")
    (tmp_path / "transcription.partial.json").write_text(
        empty.model_dump_json(), encoding="utf-8")

    assert pipeline._load_checkpoint(job, "fp") is None


def test_an_engine_that_cannot_resume_gets_no_checkpoint(tmp_path):
    """راية صريحة لا استنتاج: كتابة نقاطٍ لا يقرؤها أحد تُوهم بأمانٍ
    لا وجود له."""
    pipeline = _pipeline(tmp_path, supports_resume=False)
    job = _StubJob(tmp_path)
    (tmp_path / "transcription.partial.json").write_text(
        _saved(5.0).model_dump_json(), encoding="utf-8")

    assert pipeline._load_checkpoint(job, "fp") is None
    assert pipeline._checkpoint_writer(job, "fp", 100.0) is None


def test_the_writer_produces_a_readable_checkpoint(tmp_path):
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    write = pipeline._checkpoint_writer(job, "fp", 100.0)

    saved = _saved(5.0)
    write(saved.segments, saved.words, 5.0)

    data = json.loads(
        (tmp_path / "transcription.partial.json").read_text(encoding="utf-8"))
    assert data["resume_at"] == pytest.approx(5.0)
    assert data["audio_seconds"] == pytest.approx(100.0)
    assert len(data["segments"]) == 2
    assert pipeline._load_checkpoint(job, "fp") is not None


def test_a_write_failure_does_not_stop_a_running_transcription(tmp_path,
                                                               monkeypatch):
    """قرصٌ ممتلئ بعد ثلاث ساعات عمل: أسوأ ما يُفقد هو الاستئناف."""
    pipeline = _pipeline(tmp_path)
    job = _StubJob(tmp_path)
    write = pipeline._checkpoint_writer(job, "fp", 100.0)

    import core.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ممتلئ")))

    saved = _saved(5.0)
    write(saved.segments, saved.words, 5.0)     # لا استثناء
