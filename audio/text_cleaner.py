"""طبقة تنظيف النص العربي — الطبقة التي وعد بها ADR-005 ولم تُنفَّذ.

في الحزمة الأصلية ``text_clean = text_raw`` في كل موضع، أي أن العقد
موجود والتنفيذ غائب. النص الخام يبقى محفوظًا كما هو (ADR-005)، والتنظيف
يُنتج حقلًا موازيًا.
"""

from __future__ import annotations

import re
import unicodedata

_TATWEEL = "ـ"
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٟۖ-ۭ]")
_MULTISPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،.؛؟!:])")
#: الحدّ الأخير ``(?!\w)`` لا ``\b``: عبارةٌ تنتهي بعلامة ترقيم
#: («ونشونا، ونشونا، …») لا حدَّ كلمةٍ بعدها — فنجت حلقة من 37 تكرارًا
#: إلى المخرج في تشغيلٍ حقيقي.
_REPEATED_PHRASE = re.compile(r"\b(.{4,40}?)(?:\s+\1(?!\w)){2,}")


def normalize_arabic(text: str, strip_diacritics: bool = False) -> str:
    """تطبيع يونيكودي محافظ: لا يغيّر كلام المتحدث، يوحّد الترميز فقط.

    لا نوحّد الألف (أ/إ/آ→ا) ولا التاء المربوطة، لأن ذلك **يغيّر النص
    المعروض** ويخالف "لا تعيد صياغة كلام المتحدث". التوحيد الاختزالي
    مكانه فهرس البحث، لا المستند.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_TATWEEL, "")
    if strip_diacritics:
        text = _DIACRITICS.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return _MULTISPACE.sub(" ", text).strip()


def collapse_hallucinated_repeats(text: str) -> str:
    """يطوي التكرار الهلوسي الذي ينتجه Whisper على فترات الصمت.

    مثال حقيقي شائع في العربية:
        "شكرا لكم شكرا لكم شكرا لكم شكرا لكم" → "شكرا لكم"
    يُطبَّق فقط على 3 تكرارات فأكثر لتجنّب حذف تكرار بلاغي مقصود.
    """
    previous = None
    while previous != text:
        previous = text
        text = _REPEATED_PHRASE.sub(r"\1", text)
    return text


# ── عبارات يختلقها Whisper العربي من بيانات تدريبه ──────────────────
#
# ليست كلامًا في التسجيل بل «ترويسات» مقاطع يوتيوب التي تدرّب عليها
# النموذج، تظهر على صمتٍ أو نقرٍ أو موسيقى. رُصدت حرفيًّا على تسجيل
# شاشة حقيقي («متابعة تحضير المعلمين»): «اشتركوا في القناة» في منتصف
# شرح نظام، و«ترجمة نانسي قنقر» بين خطوتين — وطُبعتا في المستند.
#
# القائمة ضيّقة عمدًا: عبارات لا يقولها معلّمٌ يشرح نظامًا أبدًا بهذه
# الصيغة الحرفية. التكرار والعبارات العامّة («شكرًا لكم») لها طيّها
# الخاصّ أعلاه، ولا تُحذف هنا لأنها قد تكون كلامًا حقيقيًّا.
_H = "[ًٌٍَُِّْ]*"          # تشكيل اختياري بين الحروف لا يغيّر العبارة
#: ‏«القناة» قد تكون قناة Teams فعلًا («اشتركوا في القناة العامة») —
#: فالعبارة تُحذف فقط حين لا يليها ما يعرّف القناة.
_NOT_QUALIFIED = r"(?!\s+(?:ال[\u0621-\u064A]+|الخاص|التي|اللي|بتاع|دي|هذه))"
_KNOWN_HALLUCINATIONS = [re.compile(p) for p in (
    r"ترجمة\s+نانسي\s+قنقر",
    r"(?:لا\s+تنس(?:وا|ى)\s+)?(?:ال)?اشترا?ك(?:وا)?\s+(?:في|ب)\s*القناة"
    + _NOT_QUALIFIED
    + r"(?:\s+(?:و\s*)?(?:تفعيل\s+)?(?:زر\s+)?الجرس)?",
    r"شكر" + _H + r"ا?" + _H + r"\s+(?:لكم\s+)?(?:على|ل)\s*(?:ال|ل)?مشاهدة",
    r"(?i)subtitles\s+by\s+the\s+amara\.org\s+community",
    #: رُصدت حرفيًّا على ‏clip_02 من تسجيلات التدريب (17 نوفمبر 2026):
    #: «المترجم الى صفحتنا ليصلك كل جديد». ترويسة اشتراك لا يقولها
    #: معلّم. تُبتلع الكلمات التي قبلها في الجملة نفسها («المترجم الى»)
    #: لأنها جزء من الترويسة لا من الكلام، والحدّ عند علامة الترقيم.
    r"[^.،؛!؟]*صفحتنا\s+ليصل(?:ك|كم)\s+كل\s+جديد",
)]
_ORPHAN_PUNCT = re.compile(r"(^|\s)[،.؛!؟,]+(?=\s|$)")


def strip_known_hallucinations(text: str) -> str:
    """يحذف عبارات Whisper المختلَقة المعروفة من ``text_clean`` وحده."""
    original = text
    for pattern in _KNOWN_HALLUCINATIONS:
        text = pattern.sub(" ", text)
    if text == original:
        return text
    text = _ORPHAN_PUNCT.sub(r"\1", text)
    return _MULTISPACE.sub(" ", text).strip(" ،,")


# ── صدى موجّه البداية ────────────────────────────────────────────────
#
# ‏Whisper يقرأ ``initial_prompt`` بوصفه «ما قيل قبل هذه النافذة»، فحين
# يضعف الصوت يُكمل الموجّه بدل أن يسمع. فيخرج نصّ **موجّهنا نحن** إلى
# مستند المعلّم كأنه كلامه.
#
# مقيس على تسجيلات حقيقية (17 نوفمبر 2026، 22 مقطعًا):
#   ‏clip_19 بـ‏large-v3 ← «محاضرة تعليمية باللغة العربية الفصحى.»
#   ‏clip_02 بـ‏large-v3 ← «… نص مكتوب بعلامات ترقيم صحيحة.»
# وكلاهما من الموجّه الافتراضي حرفًا بحرف. وعلى ``turbo`` وقع مرّة في
# ‏clip_18. القائمة الثابتة أعلاه لا تمسّ هذا: الموجّه قابل للتغيير من
# الواجهة، فلا تُكتب عباراته في قائمة.
#
# **لا يُطبَّق على «مصطلحات المادة»** (``hotwords``): تلك أسماء قوائم
# وأعلام يقولها المتحدّث فعلًا، وحذفها حذفٌ لكلام حقيقي.
#
# المقايضة مذكورة صراحةً: جملةٌ يقولها المعلّم مطابقةً لجملة الموجّه
# تُحذف. الحدّان أدناه يجعلان ذلك بعيدًا — ولا يضيع شيء على كلّ حال،
# فالنصّ الخام محفوظ كما هو (ADR-005).

#: أقلّ عدد كلمات متتالية من الموجّه يُعدّ صدى. ثلاث كلمات («هذه محاضرة
#: تعليمية») جملةٌ قد تُقال فعلًا؛ أربعٌ متتالية حرفيًّا لا.
MIN_PROMPT_ECHO_WORDS = 4

#: نسبة الجملة التي يجب أن يغطّيها الصدى قبل حذفها كاملة. دون ذلك تُترك:
#: جملةٌ فيها كلام حقيقي وبعضُ الموجّه كلامٌ حقيقي ناقص، لا صدى.
PROMPT_ECHO_COVERAGE = 0.7

_SENTENCE_SPLIT = re.compile(r"(?<=[.،؛!؟])\s+")
_ALEF = re.compile(r"[أإآٱ]")


def _match_words(text: str) -> list[str]:
    """صورة للمقارنة وحدها — لا تُكتب في المستند إطلاقًا."""
    text = normalize_arabic(text, strip_diacritics=True)
    text = _COMPARISON_STRIP.sub(" ", text)
    text = _ALEF.sub("ا", text).replace("ة", "ه").replace("ى", "ي")
    return text.split()


def _contains_run(haystack: list[str], needle: list[str]) -> bool:
    span = len(needle)
    return any(haystack[i:i + span] == needle
               for i in range(len(haystack) - span + 1))


def _longest_prompt_run(words: list[str], prompt_words: list[str]) -> int:
    """أطول تتابع من ``words`` يرد حرفيًّا داخل كلمات الموجّه."""
    best = 0
    for start in range(len(words)):
        end = start
        while end < len(words) and _contains_run(prompt_words, words[start:end + 1]):
            end += 1
        best = max(best, end - start)
    return best


def strip_prompt_echo(text: str, prompt: str) -> str:
    """يحذف الجمل التي أعاد فيها النموذج موجّه البداية بدل الكلام."""
    prompt_words = _match_words(prompt or "")
    if len(prompt_words) < MIN_PROMPT_ECHO_WORDS:
        return text
    kept = []
    for sentence in _SENTENCE_SPLIT.split(text):
        words = _match_words(sentence)
        if len(words) >= MIN_PROMPT_ECHO_WORDS:
            run = _longest_prompt_run(words, prompt_words)
            if run >= MIN_PROMPT_ECHO_WORDS and run >= PROMPT_ECHO_COVERAGE * len(words):
                continue
        kept.append(sentence.strip())
    return _MULTISPACE.sub(" ", " ".join(k for k in kept if k)).strip()


def clean_segment_text(text: str, prompt: str = "") -> str:
    text = strip_known_hallucinations(
        collapse_hallucinated_repeats(normalize_arabic(text)))
    return strip_prompt_echo(text, prompt) if prompt else text


# ── التكرار الممتدّ عبر المقاطع ───────────────────────────────────────
#
# ``collapse_hallucinated_repeats`` أعلاه تعمل داخل المقطع الواحد، وهذا
# لا يكفي: حلقة Whisper على الصمت لا تقع داخل مقطع، بل **تُنتج مقاطع**.
# النمط الحقيقي ثمانية مقاطع متتالية نصّ كلٍّ منها «شكرًا لكم» — فلا
# تكرار داخل أيٍّ منها، فينجو كلّه إلى المستند صفحةً كاملة.
#
# لا أثر على صوت نظيف إطلاقًا؛ وعلى ملف فيه موسيقى أو تصفيق أو صمت طويل
# يزيل أوضح عيب يراه القارئ.

#: أقصى طول للعبارة المكرّرة. الحلقات الهلوسية قصيرة («شكرًا لكم»،
#: «الحمد لله»)؛ فقرة طويلة تتكرّر حرفيًا شيء آخر لا نجرؤ على طيّه.
MAX_LOOP_PHRASE_CHARS = 60

#: أقلّ عدد مقاطع متتالية يُعدّ حلقة. ثلاثة تطابق سياسة الطيّ داخل
#: المقطع، وتترك التكرار البلاغي المقصود (مرّتان) سليمًا.
MIN_LOOP_SEGMENTS = 3

_COMPARISON_STRIP = re.compile(r"[^\w\s]", re.UNICODE)


def _loop_key(text: str) -> str:
    """صورة مبسّطة للمقارنة: تتجاهل الترقيم والتشكيل والمسافات.

    ‏Whisper يُنتج الحلقة بصياغات متفاوتة قليلًا («شكرا لكم» و«شكرًا
    لكم.») — المقارنة الحرفية تفوّتها، وهي الحلقة نفسها.
    """
    text = _DIACRITICS.sub("", unicodedata.normalize("NFC", text))
    return _MULTISPACE.sub(" ", _COMPARISON_STRIP.sub("", text)).strip()


def find_repeated_runs(texts: list[str],
                       min_repeats: int = MIN_LOOP_SEGMENTS,
                       max_chars: int = MAX_LOOP_PHRASE_CHARS
                       ) -> list[tuple[int, int]]:
    """يعيد نطاقات ``[بداية, نهاية)`` لمقاطع متتالية نصّها واحد.

    منفصلة عن التطبيق لتُختبَر وحدها، ولأن الـpipeline قد يحتاج التبليغ
    عن الحلقة دون طيّها.
    """
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(texts):
        key = _loop_key(texts[index])
        if not key or len(key) > max_chars:
            index += 1
            continue
        end = index + 1
        while end < len(texts) and _loop_key(texts[end]) == key:
            end += 1
        if end - index >= min_repeats:
            runs.append((index, end))
        index = max(end, index + 1)
    return runs
