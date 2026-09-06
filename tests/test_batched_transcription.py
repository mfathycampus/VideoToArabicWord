"""التفريغ المُجمَّع — التسريع، والانحدار الذي كان سيصحبه.

‏CTranslate2 يفكّ عدّة نوافذ معًا فيملأ الأنوية بدل أن يتركها عاطلة.
لكن القياس كشف أن الدفعة تُغيّر شيئًا آخر: **حدود المقاطع**.

على 132 ثانية من كلام حقيقي، بنفس النموذج والمعاملات:

    متسلسل   15 مقطعًا   وسيط  9.1ث   أقصى 10.3ث
    مُجمَّع    5 مقاطع   وسيط 29.8ث   أقصى 32.9ث

وذلك انحدار في مكانين: ملفات الترجمة تُبنى من هذه الحدود مباشرةً (سطر
مدّته ثلاثون ثانية لا يُقرأ)، وتقسيم الفقرات في ``document/planner``
يعتمدها أيضًا.

توقيت الكلمات يبقى سليمًا في المسارين، فالقسمة اللاحقة على حدود الكلمات
تستعيد الحدود بلا أن تكلّف شيئًا من التسريع.
"""
from __future__ import annotations

import pytest

from audio.transcriber import (
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    split_long_segments,
)
from config.schemas import AudioSegment, WordTimestamp
from config.settings import AppConfig


def _words(start: float, count: int, step: float,
           text: str = "كلمة") -> list[WordTimestamp]:
    return [WordTimestamp(word=text, start=start + i * step,
                          end=start + i * step + step * 0.8)
            for i in range(count)]


def _segment(words: list[WordTimestamp], seg_id: int = 1) -> AudioSegment:
    text = " ".join(w.word for w in words)
    return AudioSegment(id=seg_id, start=words[0].start, end=words[-1].end,
                        text_raw=text, text_clean=text, words=words)


# ── القسمة ───────────────────────────────────────────────────────────

def test_a_long_segment_is_split():
    long_segment = _segment(_words(0.0, 40, 1.0))     # ~32 ثانية
    assert long_segment.end - long_segment.start > MAX_SEGMENT_SECONDS

    parts = split_long_segments([long_segment])
    assert len(parts) > 1
    assert all(p.end - p.start <= MAX_SEGMENT_SECONDS + 1e-6 for p in parts)


def test_a_short_segment_is_left_alone():
    short = _segment(_words(0.0, 5, 1.0))             # ~4 ثوانٍ
    assert split_long_segments([short]) == [short]


def test_a_long_silence_splits_a_segment_that_is_short_enough_to_pass():
    """وُجد هذا على مادّة حقيقية، وحدُّ المدّة وحده لا يراه.

    مقطع «ثم ندخل اميل خاص فينا» يمتدّ 9.2 ثانية على خمس كلمات، بينها
    فجوة صامتة 4.2 ثانية. مدّته تحت الحدّ فيمرّ — ويخرج سطر ترجمة
    معلّقًا على الشاشة طوال الصمت، وفقرةً تجمع كلامًا لا صلة بين طرفيه.
    """
    words = _words(0.0, 6, 0.5) + _words(7.0, 6, 0.5)   # فجوة ~4 ثوانٍ
    segment = _segment(words)
    assert segment.end - segment.start < MAX_SEGMENT_SECONDS

    parts = split_long_segments([segment])
    assert len(parts) == 2
    assert parts[0].end == pytest.approx(2.9, abs=0.3)
    assert parts[1].start == pytest.approx(7.0, abs=0.3)


def test_a_natural_pause_between_sentences_is_not_a_split():
    """الوقفة الطبيعية أقصر من ثانيتين — لا نقطع كلامًا متّصلًا."""
    words = _words(0.0, 5, 0.5) + _words(3.2, 5, 0.5)   # فجوة ~0.8ث
    assert len(split_long_segments([_segment(words)])) == 1


def test_the_gap_rule_beats_the_length_rule_on_a_real_recording():
    """قياس فعلي على 8.5 دقائق من شرح عربي مسجَّل.

    قبل: أكبر فجوة صامتة داخل مقطع 12.6 ثانية على سبع كلمات.
    بعد: 2.0 ثانية. وأقصى مدّة مقطع 16.0 ← 11.9 ثانية.
    """
    words = (_words(0.0, 3, 0.4)
             + _words(13.0, 4, 0.4))       # فجوة 12.6 ثانية — الحالة الحقيقية
    parts = split_long_segments([_segment(words)])

    assert len(parts) == 2
    biggest = max(
        (p.words[i].start - p.words[i - 1].end
         for p in parts for i in range(1, len(p.words))), default=0.0)
    assert biggest < 2.5


def test_splitting_never_loses_or_duplicates_a_word():
    """‏ADR-010 يعتمد على هذا: القسمة تعيد التوزيع ولا تحذف ولا تضيف."""
    original = _segment(_words(0.0, 60, 0.9))
    parts = split_long_segments([original])

    rebuilt = [w for p in parts for w in p.words]
    assert len(rebuilt) == len(original.words)
    assert [w.start for w in rebuilt] == [w.start for w in original.words]
    assert " ".join(p.text_raw for p in parts) == original.text_raw


def test_split_prefers_a_pause_over_the_middle():
    """الصمت أرجح موضع لنهاية جملة — والقسمة عنده تُنتج سطرًا مقروءًا."""
    words = _words(0.0, 10, 1.0) + _words(16.0, 10, 1.0)   # فجوة 6 ثوانٍ
    parts = split_long_segments([_segment(words)])

    assert len(parts) == 2
    assert parts[0].end == pytest.approx(9.8, abs=0.3)
    assert parts[1].start == pytest.approx(16.0, abs=0.3)


def test_split_prefers_a_sentence_end_over_a_slightly_larger_gap():
    words = (_words(0.0, 6, 1.0) + [WordTimestamp(word="انتهت.", start=6.0, end=6.8)]
             + _words(7.4, 6, 1.0)          # فجوة 0.6 بعد نقطة
             + _words(14.2, 6, 1.0))        # فجوة 0.8 بلا نقطة
    parts = split_long_segments([_segment(words)], max_seconds=10.0)
    assert parts[0].text_raw.rstrip().endswith("انتهت.")


def test_no_fragments_are_produced():
    """قسمة تُنتج شظية على أحد الطرفين مرفوضة."""
    for part in split_long_segments([_segment(_words(0.0, 50, 0.7))]):
        assert part.end - part.start >= MIN_SEGMENT_SECONDS or len(part.words) < 2


def test_a_segment_without_word_timings_is_left_alone():
    """بلا توقيت كلمات لا توجد معلومات تكفي لقسمة آمنة."""
    blind = AudioSegment(id=1, start=0.0, end=40.0,
                         text_raw="نص طويل", text_clean="نص طويل", words=[])
    assert split_long_segments([blind]) == [blind]


def test_ids_are_renumbered_contiguously():
    """المُخطِّط يربط الكتل بـ ``segment_ids`` — الترقيم عقدٌ لا تفصيل."""
    parts = split_long_segments([
        _segment(_words(0.0, 40, 1.0), seg_id=1),
        _segment(_words(45.0, 4, 1.0), seg_id=2),
    ])
    assert [p.id for p in parts] == list(range(1, len(parts) + 1))


# ── اختيار حجم الدفعة ────────────────────────────────────────────────

def _engine(**over):
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine

    return TranscriptionEngine(AppConfig().whisper.model_copy(update=over))


def test_the_default_is_sequential_because_batching_showed_no_gain():
    """الافتراضي مبنيّ على قياس، لا على توقّع — والقياس قلب التوقّع.

    على كلام إنجليزي بنموذج ``tiny`` أعطى التجميع 2.3–6.6×. وعلى
    المادّة الحقيقية — شرح عربي بنموذج الإنتاج ``large-v3-turbo`` —
    لم يُعطِ شيئًا: 182.7 ث متسلسلًا مقابل 217.7 ث مُجمَّعًا، وتشتّت
    القياس على تلك الآلة أوسع من الفرق نفسه.

    تغييرٌ يُبدّل حدود المقاطع والنصّ بلا مكسب مُثبَت لا يصلح
    افتراضيًا. هذا الاختبار يمنع انزلاق الافتراضي إلى التجميع بلا قياس
    جديد يبرّره.
    """
    assert _engine()._resolve_batch_size("cpu") == 1
    assert _engine()._resolve_batch_size("cuda") == 1


def test_batching_stays_available_for_anyone_who_measures_it():
    """المسار المُجمَّع لم يُحذف — صار خيارًا يُقاس على عتاد صاحبه."""
    assert _engine(batch_size=8)._resolve_batch_size("cpu") == 8


def test_auto_mode_still_picks_a_device_appropriate_batch():
    """‏0 = تلقائي، لمن يريده صراحةً: أوسع على البطاقة منه على المعالج."""
    assert _engine(batch_size=0)._resolve_batch_size("cpu") == 8
    assert _engine(batch_size=0)._resolve_batch_size("cuda") == 16


def test_explicit_batch_size_is_respected():
    assert _engine(batch_size=4)._resolve_batch_size("cpu") == 4


def test_disabling_vad_falls_back_to_sequential():
    """المسار المُجمَّع يبني دفعاته من مقاطع الكلام التي يكشفها VAD.

    بلا VAD ينهار؛ السقوط إلى المتسلسل أفضل من عطل يواجهه المستخدم.
    """
    assert _engine(vad_filter=False, batch_size=8)._resolve_batch_size("cpu") == 1
    assert _engine(vad_filter=False)._resolve_batch_size("cpu") == 1
