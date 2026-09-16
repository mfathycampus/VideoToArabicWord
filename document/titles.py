"""ما الذي يجعل سطرًا صالحًا عنوانًا — تعريف واحد يشترك فيه المُخطِّط والمقياس.

سبب وجود هذا الملف: المُخطِّط كان يشتقّ العناوين، والمقياس يحكم عليها،
وكلٌّ يحمل تعريفه الخاص لما هو «عنوان». فأنتج المُخطِّط على تسجيل حقيقي
عناوين مثل «أخطر القلب وأخطر القلب وأخطر القلب بعد أن أخطر القلب» —
حلقة هلوسة من التفريغ — ومنحها المقياس **100٪**، لأنه لا يفحص إلا أن
العنوان ليس ترتيبيًا. مقياسٌ يمرّ عليه المخرجُ الفاشل يُخفي العطب بدل
أن يُظهره، وهو العيب نفسه الذي أُعيدت كتابة بوابة الجودة من أجله.

فالتعريف هنا واحد، ويستعمله الطرفان: المُخطِّط ليرفض المرشّح الرديء
ويسقط إلى العنوان الترتيبي، والمقياس ليحسب العنوان الرديء **رسوبًا**
لا نجاحًا. عنوانٌ ضعيف خير من عنوان مضلِّل.

ما يُكشف هنا عيوبٌ **شكلية** فقط: الطول، وحلقات التكرار، والافتتاحيات
الجوفاء. لا يُدَّعى أنه يقيس المعنى — عنوان مفهوم لكنه غير معبّر يمرّ،
وذلك حدّ هذه الطبقة المعروف.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterator, List

#: أطوال العنوان المقبولة. الأقصر لا يقول شيئًا، والأطول يلتفّ في
#: الفهرس فيصير جدارًا بدل دليل.
TITLE_MIN_CHARS = 12
TITLE_MAX_CHARS = 70

#: أدنى طول عند **الحكم** على عنوان جاهز — لا عند اشتقاقه. عنوان يكتبه
#: إنسان أو تنتجه إعادة الصياغة قد يكون كلمة واحدة («خاتمة»، «مراجعة»)
#: وهو سليم؛ ورسوبه في المقياس خطأٌ في المقياس لا في المستند.
JUDGE_MIN_CHARS = 3

#: عنوان ترتيبي يولّده المُخطِّط: «القسم الأول — من 00:00:00».
#: كان معرَّفًا في ``document/quality.py`` وحدها؛ نُقل هنا كي لا يفترق
#: تعريفُ ما يبنيه المُخطِّط عن تعريف ما يقيسه المقياس.
ORDINAL_TITLE = re.compile(r"^\s*القسم\s+\S+\s*—\s*من\s+\d")

#: افتتاحيات لا تصلح عنوانًا مهما طالت: بسملة، تحية، حشو الكلام.
#: تُطبَّق **مرارًا حتى الثبات** لا مرّة واحدة: نصّ حقيقي بدأ بـ«بسم الله
#: الرحمن الرحيم، السلام عليكم…» فأزال التطبيقُ الواحد البسملةَ وترك
#: التحية عنوانًا للقسم الأول.
_TITLE_NOISE = re.compile(
    r"^\s*(?:و|ف|ثم|طيب|يعني|أه+|امم+|اه+|ال[أا]ن|هنا|كده|ماشي|"
    r"بسم\s+الله[^.،]*|السلام\s+عليكم[^.،]*|"
    r"وعليكم\s+السلام[^.،]*|أهلا?\s+وسهلا?[^.،]*)\b[\s،]*", re.UNICODE)

#: تشكيل وتطويل — يُزالان قبل مقارنة الكلمات ببعضها فقط، لا من العنوان
#: المعروض.
_DIACRITICS = re.compile(r"[ؗ-ًؚ-ْـ]")

#: أدنى نسبة كلمات فريدة. دون ذلك يكون النصّ حلقة لا جملة.
_MIN_UNIQUE_WORD_RATIO = 0.70

#: كم مرّة يتكرّر الثنائي قبل أن يُعدّ حلقة. الحدّ **ثلاث** لا اثنتان،
#: وهذا تصحيح لخطأ وقعتُ فيه: بمرّتين رُفض «هنتعرف على كيف نخطط وكيف
#: ننزل واجبات وكيف ننزل أخبار» — وهو أدقّ وصف للمحاضرة في التسجيل
#: كلّه — لأن «كيف ننزل» تكرّر مرّتين. التوازي البلاغي عربيةٌ سليمة،
#: والحلقة إلحاحٌ ثلاثيّ فأكثر.
_MAX_BIGRAM_REPEATS = 2

#: عدد الكلمات الذي تحته لا معنى لنسبة التفرّد (جملة من ثلاث كلمات قد
#: تكرّر واحدة بلا خلل).
_RATIO_MIN_WORDS = 6

# ── زخرفة الواجهة: ما يقرؤه الـOCR وليس محتوى الشاشة ──────────────────
#
# هذه القواعد الثلاث ليست تخمينًا: كلٌّ منها مأخوذة من **تعليقات ظهرت
# فعلًا** في مستند وُلِّد من تسجيل حقيقي، وكلّها نالت من المقياس القديم
# ‏100٪ لأن شرطه الوحيد كان «نصّ الشاشة غير فارغ»:
#
#   «timetable?semesterid=1186991»            ← مسار
#   «mast S078 chalkcom/lessons/time»         ← مسار
#   «Bi Lessons | Chalk - Googke Chrome»      ← شريط عنوان المتصفّح
#   «ston. © Freee (ease text ner Al stern.»  ← قراءة فاشلة
#
# تعليقٌ كهذا أسوأ من «لقطة عند 00:05:59»: الأخير لا يقول شيئًا،
# وهذا يقول شيئًا خاطئًا ويبدو عطبًا في البرنامج.

#: عنوان موقع أو مسار.
_URL_LIKE = re.compile(
    r"(?:https?://|www\.|\.com|\.org|\.net|\.sa\b|/\w|\?\w+=)", re.IGNORECASE)

#: شريط عنوان نافذة المتصفّح. الأسماء مكتوبة بتسامح لأن الـOCR يخطئ
#: فيها («Googke Chrome» جاءت هكذا مرّتين من التسجيل نفسه).
_BROWSER_CHROME = re.compile(
    r"(?:Chrome|Firefox|Mozilla|Edge|Safari|Opera)\W*$", re.IGNORECASE)

#: رموز لا تظهر في نصّ مقروء إلا نادرًا؛ كثرتها علامة قراءة فاشلة.
_JUNK_CHARS = re.compile(r"[©®™$¥§¶|~^*<>{}\[\]\\_=+`]")

#: أقصى نسبة رموز غريبة قبل أن يُعدّ السطر قراءةً فاشلة.
_MAX_JUNK_RATIO = 0.04

# ── شريط تنقّل التطبيق: ثالث ما يقرؤه الـOCR وليس محتوى ───────────────
#
# رُصد على مخرج حقيقي عنوانان خرجا إلى فهرس المستند:
#
#   «Curriculum & Instruction Lessons v Curriculum Reports »»
#   «GRADE 9AM WEEKLY PLAN - WEEK 4 tte»
#
# الأول شريط القوائم العلوي كاملًا — أسماء تبويبات لا موضوع درس —
# وعلامته سهامُ القوائم (‏»، ›) و«v» المنسدلة بين الكلمات. والثاني
# عنوان سليم تذيّلته شظيّة قراءة («tte»)، وهي أثر نصٍّ مقصوص عند حافة
# اللقطة. وكلاهما مرّ من المقياس بـ100٪.

#: فواصل القوائم وأسهمها. وجودها يعني أن السطر شريط تنقّل لا عنوانًا.
_NAV_SEPARATORS = re.compile(r"[»«›‹▾▼⌄]|\s\|\s|\bv\s(?=[A-Z])")

#: كلمات لاتينية قصيرة **حقيقية** — تُستثنى من قصّ الشظايا. القائمة
#: قصيرة عمدًا: كل إضافة إليها تُبقي شظيّة محتملة، وحذفُ كلمة سليمة من
#: آخر عنوان أهون من إبقاء «tte» فيه.
_SHORT_WORDS = {
    "a", "an", "of", "to", "in", "on", "at", "is", "it", "as", "by", "or",
    "am", "pm", "id", "no", "tv", "hr", "q1", "q2", "q3", "q4", "ai", "hq",
}

#: شظيّة قراءة في آخر السطر: كلمة لاتينية من ثلاثة أحرف فأقلّ ليست من
#: الكلمات القصيرة المعروفة. «tte» و«rn» و«sv» — لا كلمات ولا اختصارات،
#: بل أثر نصٍّ مقصوص عند حافة اللقطة.
_TRAILING_FRAGMENT = re.compile(r"\s+([A-Za-z]{1,3})\s*$")


def _normalize_word(word: str) -> str:
    """صورة الكلمة للمقارنة: بلا تشكيل، وبلا واو عطف بادئة.

    حلقات التفريغ تتخلّلها واو العطف («أخطر القلب وأخطر القلب»)، فبدونها
    تُرى الحلقة على حقيقتها. هذا **استنتاج** من شكل الحلقات التي رأيتُها
    لا قياسٌ على مجموعة موسومة.
    """
    word = _DIACRITICS.sub("", word)
    if len(word) > 3 and word.startswith("و"):
        word = word[1:]
    return word


def _words(text: str) -> List[str]:
    return [_normalize_word(w) for w in re.findall(r"[^\W\d_]+", text, re.UNICODE)]


def looks_like_url(text: str) -> bool:
    """مسار إنترنت أو عنوان موقع."""
    return bool(_URL_LIKE.search(text or ""))


def looks_like_browser_chrome(text: str) -> bool:
    """شريط عنوان نافذة متصفّح — «… - Google Chrome»."""
    return bool(_BROWSER_CHROME.search(text or ""))


def title_defects(candidate: str, min_chars: int = JUDGE_MIN_CHARS) -> List[str]:
    """أسباب رفض هذا النصّ عنوانًا — قائمة فارغة تعني أنه مقبول.

    ``min_chars`` يفرّق بين **الحكم** و**الاشتقاق**، وهو فرق حقيقي:
    «خاتمة» عنوانٌ عربيّ سليم يكتبه إنسان، فلا يجوز للمقياس أن يرسّبه؛
    لكن «خاتمة» مقتطعةً من كلام مرتجل ليست عنوانًا بل شظيّة. فالحكم
    يقبل الثلاثة أحرف، والاشتقاق يشترط اثني عشر.
    """
    candidate = (candidate or "").strip()
    defects: List[str] = []

    if len(candidate) < min_chars:
        defects.append("قصير جدًّا")
    if len(candidate) > TITLE_MAX_CHARS:
        defects.append("طويل جدًّا")
    if ORDINAL_TITLE.match(candidate):
        defects.append("ترتيبي")
    if _URL_LIKE.search(candidate):
        defects.append("مسار إنترنت")
    if _BROWSER_CHROME.search(candidate):
        defects.append("شريط متصفّح")
    if candidate and (len(_JUNK_CHARS.findall(candidate))
                      / len(candidate) > _MAX_JUNK_RATIO):
        defects.append("قراءة فاشلة")
    if _NAV_SEPARATORS.search(candidate):
        defects.append("شريط تنقّل")

    words = _words(candidate)
    bigrams = list(zip(words, words[1:]))
    if bigrams and max(Counter(bigrams).values()) > _MAX_BIGRAM_REPEATS:
        defects.append("حلقة تكرار")
    trigrams = list(zip(words, words[1:], words[2:]))
    # الثلاثي المكرّر مرّتين يكفي: احتمال أن تتكرّر ثلاث كلمات متتالية
    # بعينها في جملة واحدة سليمة ضئيل، بخلاف الثنائي.
    if trigrams and len(set(trigrams)) < len(trigrams):
        defects.append("عبارة مكرّرة")
    if len(words) >= _RATIO_MIN_WORDS:
        if len(set(words)) / len(words) < _MIN_UNIQUE_WORD_RATIO:
            defects.append("تكرار مفرط")

    return defects


def is_acceptable_title(candidate: str) -> bool:
    """هل يصلح هذا النصّ عنوانًا؟"""
    return not title_defects(candidate)


def strip_trailing_fragment(text: str) -> str:
    """يحذف شظيّة القراءة من آخر السطر — تكرارًا حتى تثبت النتيجة.

    مرّة واحدة لا تكفي: «… WEEK 4 tte sv» تحتاج مرّتين، وسطرٌ انتهى
    بثلاث شظايا يحتاج ثلاثًا. والحلقة محدودة كي لا تأكل عنوانًا
    قصيرًا كلماتُه قصيرة أصلًا.
    """
    text = (text or "").strip()
    for _ in range(3):
        match = _TRAILING_FRAGMENT.search(text)
        if not match or match.group(1).lower() in _SHORT_WORDS:
            break
        stripped = text[:match.start()].strip(" -–—،,.")
        if len(stripped) < TITLE_MIN_CHARS:
            break
        text = stripped
    return text


def strip_opening_noise(text: str) -> str:
    """يحذف الافتتاحيات الجوفاء تكرارًا حتى تثبت النتيجة."""
    text = (text or "").strip()
    for _ in range(6):                      # حدّ يمنع دورة لا تنتهي
        stripped = _TITLE_NOISE.sub("", text).strip()
        if stripped == text:
            return text
        text = stripped
    return text


def clean_title_candidate(text: str) -> str:
    """يشذّب **جملة واحدة** ليصير عنوانًا، أو سلسلة فارغة إن لم تصلح."""
    candidate = strip_opening_noise(text)
    # شظيّة القراءة تُحذف **قبل** الحكم: «… WEEK 4 tte» عنوانٌ سليم
    # تذيّلته قراءةٌ فاشلة، ورفضُه كاملًا يُضيّع عنوانًا صالحًا.
    candidate = strip_trailing_fragment(candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" ،؛-—:")
    if not candidate:
        return ""

    # الحكم يقع على الجملة **كاملة** قبل القصّ. القصّ إلى سبعين حرفًا
    # يبتر ذيل الحلقة فيُخفيها: «سنجد هذا الزجاج وصف الجدول سأقوم بتيدي
    # وزع الجدول سأقوم بتيد جدول سأقوم بتيدي وزع» مرّ سليمًا لأن
    # التكرار الثالث سقط مع القصّ. عيبٌ في الجملة عيبٌ في عنوانها.
    if [d for d in title_defects(candidate, TITLE_MIN_CHARS)
            if d != "طويل جدًّا"]:
        return ""

    if len(candidate) > TITLE_MAX_CHARS:
        # نقصّ على حدّ كلمة لا على حرف عشوائي
        candidate = candidate[:TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip(" ،؛-—:")

    return candidate if len(candidate) >= TITLE_MIN_CHARS else ""


def title_candidates(text: str) -> Iterator[str]:
    """يمرّ على جُمل النصّ ويُخرج كل ما يصلح منها عنوانًا.

    المرور على الجُمل — لا على أول جملة وحدها — هو ما أنقذ القسم الأول
    من تسجيل حقيقي: أولى جُمله بسملةٌ وتحية، والثانية «هنتعرف على كيف
    نخطط وكيف ننزل واجبات» — وهي وصف المحاضرة نفسه.
    """
    for sentence in re.split(r"[.!?؟…\n]", text or ""):
        candidate = clean_title_candidate(sentence)
        if candidate:
            yield candidate
