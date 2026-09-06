"""الحلقة الهلوسية الممتدّة عبر المقاطع — أوضح عيب يراه القارئ.

‏Whisper على الصمت أو الموسيقى أو التصفيق لا يُكرّر داخل المقطع الواحد،
بل **يُنتج مقاطع**: ثمانية مقاطع متتالية نصّ كلٍّ منها «شكرًا لكم».
و``collapse_hallucinated_repeats`` تعمل داخل المقطع، فلا ترى شيئًا —
فتصل الحلقة إلى المستند صفحةً كاملة.

هذه الاختبارات تُثبّت الطيّ عبر الحدود، وتحرس الحدّين اللذين يمنعانه من
ابتلاع كلام حقيقي.
"""
from __future__ import annotations

import pytest

from audio.text_cleaner import (
    MAX_LOOP_PHRASE_CHARS,
    clean_segment_text,
    find_repeated_runs,
)
from audio.transcriber import collapse_repeated_segments
from config.schemas import AudioSegment, WordTimestamp


def _segment(index: int, text: str) -> AudioSegment:
    words = [WordTimestamp(word=w, start=index * 3.0 + i * 0.4,
                           end=index * 3.0 + i * 0.4 + 0.3)
             for i, w in enumerate(text.split())]
    return AudioSegment(id=index + 1, start=index * 3.0, end=index * 3.0 + 2.5,
                        text_raw=text, text_clean=clean_segment_text(text),
                        words=words)


def _texts(*items: str) -> list[AudioSegment]:
    return [_segment(i, t) for i, t in enumerate(items)]


# ── كشف النطاقات ─────────────────────────────────────────────────────

def test_a_run_of_identical_segments_is_found():
    runs = find_repeated_runs(["مقدمة"] + ["شكرا لكم"] * 8 + ["نعود"])
    assert runs == [(1, 9)]


def test_two_repeats_are_left_alone():
    """التكرار البلاغي المقصود («لا لا») ليس هلوسة."""
    assert find_repeated_runs(["أ", "نعم نعم", "نعم نعم", "ب"]) == []


def test_punctuation_and_diacritics_do_not_hide_a_loop():
    """‏Whisper يُنتج الحلقة بصياغات متفاوتة قليلًا — وهي الحلقة نفسها."""
    runs = find_repeated_runs(["شكرا لكم", "شكرًا لكم.", "شُكرا لكم!"])
    assert runs == [(0, 3)]


def test_a_long_phrase_is_never_collapsed():
    """فقرة طويلة تتكرّر حرفيًا شيء آخر لا نجرؤ على طيّه."""
    long_text = "كلمة " * 30
    assert len(long_text) > MAX_LOOP_PHRASE_CHARS
    assert find_repeated_runs([long_text] * 5) == []


def test_empty_segments_are_ignored():
    assert find_repeated_runs(["", "", "", ""]) == []


# ── الطيّ الفعلي ─────────────────────────────────────────────────────

def test_eight_repeated_segments_become_one():
    segments = _texts("مقدمة المحاضرة", *["شكرا لكم"] * 8, "نعود الآن")
    result = collapse_repeated_segments(segments)

    assert len(result) == 3
    assert [s.text_clean for s in result] == [
        "مقدمة المحاضرة", "شكرا لكم", "نعود الآن"]


def test_the_collapsed_segment_spans_the_whole_loop():
    """المدّة تبقى صحيحة: الصور تُوضَع بالطوابع الزمنية."""
    segments = _texts("مقدمة", *["شكرا لكم"] * 5, "نعود")
    loop = collapse_repeated_segments(segments)[1]

    assert loop.start == segments[1].start
    assert loop.end == segments[5].end


def test_raw_text_is_never_touched():
    """‏ADR-005: ما قاله المتحدّث محفوظ كما سُمع في transcription.json.

    المطويّ هو ``text_clean`` وحده — المستند ينظّف، والسجلّ لا يكذب.
    """
    segments = _texts("مقدمة", *["شكرا لكم"] * 6, "نعود")
    loop = collapse_repeated_segments(segments)[1]

    assert loop.text_raw.count("شكرا") == 6
    assert loop.text_clean == "شكرا لكم"


def test_word_timings_survive_the_collapse():
    """توقيت الكلمات يحدّد مواضع الصور — حذفه يُزيح اللقطات."""
    segments = _texts("مقدمة", *["شكرا لكم"] * 4, "نعود")
    before = sum(len(s.words) for s in segments)
    assert sum(len(s.words) for s in collapse_repeated_segments(segments)) == before


def test_ids_are_renumbered_contiguously():
    result = collapse_repeated_segments(_texts("أ", *["ب ج"] * 4, "د"))
    assert [s.id for s in result] == list(range(1, len(result) + 1))


def test_clean_audio_is_returned_untouched():
    """الشرط الأهمّ: صفر أثر على ملف بلا حلقات."""
    segments = _texts("الجملة الأولى", "الجملة الثانية", "الجملة الثالثة")
    assert collapse_repeated_segments(segments) == segments


def test_two_separate_loops_are_both_collapsed():
    segments = _texts("مقدمة", *["شكرا لكم"] * 3,
                      "المتن هنا", *["الحمد لله"] * 4, "خاتمة")
    result = collapse_repeated_segments(segments)
    assert [s.text_clean for s in result] == [
        "مقدمة", "شكرا لكم", "المتن هنا", "الحمد لله", "خاتمة"]


def test_a_loop_at_the_very_end_is_collapsed():
    result = collapse_repeated_segments(_texts("مقدمة", *["شكرا لكم"] * 5))
    assert len(result) == 2


def test_too_few_segments_short_circuits():
    segments = _texts("أ", "ب")
    assert collapse_repeated_segments(segments) is segments


# ── معاملات VAD ──────────────────────────────────────────────────────

def _engine(**over):
    pytest.importorskip("faster_whisper")
    from audio.transcriber import TranscriptionEngine
    from config.settings import AppConfig

    return TranscriptionEngine(AppConfig().whisper.model_copy(update=over))


def test_vad_options_match_the_library_defaults():
    """الترقية لا تُغيّر سلوكًا: القيم مطابقة لافتراضيات المكتبة.

    أثر هذه المعاملات على معدّل الخطأ لا يُخمَّن، والقيمة الخاطئة
    تُسوّئ النتيجة — فتُضبط بالقياس على مادّة حقيقية لا بالحدس.
    """
    from faster_whisper.vad import VadOptions

    options = _engine()._vad_options()
    defaults = VadOptions()
    assert options["threshold"] == defaults.threshold
    assert options["speech_pad_ms"] == defaults.speech_pad_ms
    assert options["min_speech_duration_ms"] == defaults.min_speech_duration_ms
    assert options["min_silence_duration_ms"] == defaults.min_silence_duration_ms
    # صفر = بلا حدّ؛ لا يُمرَّر إطلاقًا فتبقى ``inf`` الافتراضية
    assert "max_speech_duration_s" not in options


def test_vad_options_are_tunable():
    options = _engine(vad_speech_pad_ms=700,
                      vad_min_speech_duration_ms=250,
                      vad_max_speech_duration_s=30.0)._vad_options()
    assert options["speech_pad_ms"] == 700
    assert options["min_speech_duration_ms"] == 250
    assert options["max_speech_duration_s"] == 30.0


def test_no_vad_options_when_vad_is_disabled():
    assert _engine(vad_filter=False)._vad_options() is None
