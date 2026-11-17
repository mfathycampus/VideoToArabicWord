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
from audio.transcriber import (
    MAX_SEGMENT_SECONDS,
    collapse_repeated_segments,
    split_long_segments,
)
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


# ---------------------------------------------------------------------
# 1.22.9: صدى موجّه البداية — نصّ موجّهنا يصل مستند المعلّم
# ---------------------------------------------------------------------
# ‏Whisper يقرأ ``initial_prompt`` بوصفه «ما قيل قبل هذه النافذة»، فحين
# يضعف الصوت يُكمل الموجّه بدل أن يسمع. قيس على تسجيلات حقيقية
# (17 نوفمبر 2026): ‏large-v3 أخرج نصّ الموجّه حرفيًّا في مقطعين من
# ثلاثة فُحصت، و``turbo`` في مقطع من 22.

_PROMPT = ("هذه محاضرة تعليمية باللغة العربية الفصحى. "
           "النص مكتوب بعلامات ترقيم صحيحة.")


@pytest.mark.parametrize("echoed", [
    "محاضرة تعليمية باللغة العربية الفصحى.",      # clip_19 حرفيًّا
    "هذه محاضرة تعليمية باللغة العربية الفصحى.",  # الجملة كاملة
    "النص مكتوب بعلامات ترقيم صحيحة.",            # الجملة الثانية
    "نص مكتوب بعلامات ترقيم صحيحة.",              # clip_02 حرفيًّا
])
def test_the_model_echoing_our_prompt_does_not_reach_the_document(echoed):
    assert clean_segment_text(echoed, _PROMPT) == ""


def test_the_echo_is_removed_sentence_by_sentence_not_wholesale():
    """جملةٌ صدى بجوار كلامٍ حقيقي: تُحذف هي وحدها."""
    text = "خلينا ننتقل على التقارير. النص مكتوب بعلامات ترقيم صحيحة."
    assert clean_segment_text(text, _PROMPT) == "خلينا ننتقل على التقارير."


@pytest.mark.parametrize("speech", [
    "هذه محاضرة تعليمية",                       # ثلاث كلمات: دون الحدّ
    "خلينا ننتقل على التقارير ونشوف النسب",
    "النص مكتوب على السبورة بخط واضح",          # يشارك الموجّه كلمتين
    "هنا بنختار الترم وبعدين نضغط التالي",
])
def test_real_speech_survives_the_prompt_echo_filter(speech):
    assert clean_segment_text(speech, _PROMPT) == speech


def test_without_a_prompt_nothing_changes():
    """السلوك القديم محفوظ: بلا موجّه لا يُحذف شيء بهذا السبب."""
    echoed = "محاضرة تعليمية باللغة العربية الفصحى."
    assert clean_segment_text(echoed) == echoed


def test_a_short_prompt_disables_the_filter():
    """موجّه أقصر من الحدّ لا يُقاس عليه: كلماته شائعة جدًّا."""
    assert clean_segment_text("نعم تمام كده", "نعم تمام") == "نعم تمام كده"


def test_the_glossary_is_never_treated_as_an_echo():
    """المصطلحات تمرّ عبر ``hotwords`` لا ``initial_prompt``.

    هي أسماء قوائم يقولها المتحدّث فعلًا؛ لو عوملت معاملة الموجّه
    لحُذف كلام حقيقي من المستند.
    """
    said = "نفتح Analytics Insights ونشوف Gradebook والتقارير"
    assert clean_segment_text(said, _PROMPT) == said


def test_the_subscribe_header_seen_on_a_real_recording_is_removed():
    """‏clip_02: «المترجم الى صفحتنا ليصلك كل جديد» — ترويسة اشتراك."""
    assert clean_segment_text("المترجم الى صفحتنا ليصلك كل جديد.", "") == ""
    # وكلمة «صفحتنا» وحدها كلامٌ عاديّ لا تُمسّ.
    kept = "نشرح هنا صفحتنا في النظام ونفتحها"
    assert clean_segment_text(kept, "") == kept


# ── القسمة لا تُحيي صدى الموجّه ──────────────────────────────────────
#
# العطل: ``split_long_segments`` تُعيد بناء ``text_clean`` من الكلمات
# **الخام**. فما حذفه المسار الرئيسي من صدى الموجّه كان يعود حيًّا كلّما
# قُسم مقطع — ومقطعٌ نُظّف جزئيًّا يمرّ من حارس ``if segment.text_clean``
# لأن نصَّه النظيف ليس فارغًا.
#
# قيس على تسجيلات حقيقية (17 نوفمبر 2026، large-v3): خرج «محاضرة تعليمية
# باللغة العربية الفصحى.» و«نص مكتوب بعلامات ترقيم صحيحة.» في مواضع
# الكلام — كلماتُ موجّهنا تُنسب إلى المعلّم في مستنده.

def _long_segment(text: str, prompt: str) -> AudioSegment:
    """مقطع أطول من الحدّ بتوقيت كلمات، فيستوجب القسمة."""
    words = text.split()
    step = (MAX_SEGMENT_SECONDS + 10.0) / len(words)
    stamps = [WordTimestamp(word=w, start=i * step, end=(i + 1) * step - 0.01)
              for i, w in enumerate(words)]
    return AudioSegment(id=1, start=0.0, end=stamps[-1].end, text_raw=text,
                        text_clean=clean_segment_text(text, prompt),
                        words=stamps)


_SPEECH = ("خلينا ننتقل على التقارير ونشوف النسب اللي طلعت دي. "
           "وبعدها نفتح الصفحة الثانية ونكمل الشرح مع بعض.")
_ECHO = "محاضرة تعليمية باللغة العربية الفصحى."


def test_splitting_does_not_resurrect_a_prompt_echo():
    segment = _long_segment(f"{_SPEECH} {_ECHO}", _PROMPT)
    assert "محاضرة تعليمية" not in segment.text_clean      # المسار الرئيسي حذفه
    assert "ننتقل على التقارير" in segment.text_clean      # وبقي كلامٌ حقيقي

    pieces = split_long_segments([segment], prompt=_PROMPT)
    assert len(pieces) > 1
    joined = " ".join(p.text_clean for p in pieces)
    assert "محاضرة تعليمية" not in joined
    assert "ننتقل على التقارير" in joined


def test_splitting_keeps_the_raw_text_whole():
    """الخام لا يُمسّ مهما حُذف من النظيف — ``assert_lossless`` يعتمد عليه."""
    text = f"{_SPEECH} {_ECHO}"
    pieces = split_long_segments([_long_segment(text, _PROMPT)], prompt=_PROMPT)
    assert " ".join(p.text_raw for p in pieces).split() == text.split()


def test_splitting_without_a_prompt_still_cleans_known_hallucinations():
    """غياب الموجّه لا يُعطّل بقيّة المنظّف."""
    pieces = split_long_segments(
        [_long_segment(f"{_SPEECH} اشتركوا في القناة", "")], prompt="")
    assert "اشتركوا في القناة" not in " ".join(p.text_clean for p in pieces)
    assert "ننتقل على التقارير" in " ".join(p.text_clean for p in pieces)
