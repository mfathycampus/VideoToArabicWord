"""مسرد بلا نموذج لغوي — مسار من لا يملك واحدًا.

**وحدّه مُعلَن: هذه الوحدة تُخرج قائمة مصطلحات لا تعريفات.** التعريف
يحتاج فهمًا؛ والتكرار لا يعرّف شيئًا. ادّعاء غير ذلك يُنتج مسردًا
مملوءًا بجمل مقتطعة من السياق تبدو تعريفات وليست كذلك — وهو أسوأ من
لا مسرد، لأن الخطأ فيه غير مرئي.

**وقائمة المصطلحات وحدها تستحق العمل لسببين مقيسين:**

الأول أنها مادة مراجعة حقيقية: الطالب الذي يراجع محاضرة ساعتين يحتاج
أولًا أن يعرف ما المصطلحات التي وردت فيها.

والثاني أنها تُغذّي خانة «مصطلحات المادة» — وهي مقيسة في
``video/screen_terms``: ملء الخانة رفع استرجاع أسماء القوائم
الإنجليزية من **صفر من 12 إلى 8 من 12** على تسجيل حقيقي. أي أن مسرد
المحاضرة الأولى يرفع دقّة تفريغ المحاضرة الثانية في المقرّر نفسه.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import List, Sequence

from config.schemas import GlossaryTerm, KeyframeMetadata, TranscriptionResult
from video.screen_terms import terms_from_screen_text

MAX_TERMS = 25
#: مرّة واحدة لا تكفي: مصطلح المادة يتكرّر، وما ورد مرّة غالبًا خطأ
#: تفريغ أو اسم عابر.
MIN_OCCURRENCES = 3
MIN_PHRASE_CHARS = 5

_LATIN_PHRASE = re.compile(r"\b[A-Za-z][A-Za-z0-9&/+.-]{2,}(?:\s+[A-Z][A-Za-z0-9&/+.-]{1,})*")
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_ARABIC_WORD = re.compile(r"[ء-ي]{3,}")

#: كلمات وظيفية عربية شائعة. قائمة قصيرة عمدًا: الطويلة تُسقط مصطلحات
#: حقيقية، والغرض هنا ترشيح لا تحليل صرفيّ.
_STOPWORDS = {
    "التي", "الذي", "الذين", "هذا", "هذه", "ذلك", "هناك", "هنا", "كان",
    "كانت", "يكون", "تكون", "على", "عن", "في", "من", "إلى", "الى", "مع",
    "بعد", "قبل", "بين", "عند", "كل", "بعض", "غير", "أيضا", "أيضًا",
    "يعني", "طيب", "تمام", "ممكن", "لازم", "عشان", "علشان", "بس", "دي",
    "ده", "احنا", "إحنا", "انت", "أنت", "نحن", "هو", "هي", "هم", "ما",
    "لا", "نعم", "أن", "إن", "أو", "او", "ثم", "حتى", "لكن", "قد",
    "الآن", "اليوم", "شوية", "كده", "كدا", "خلاص", "برضه", "طب",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = _DIACRITICS.sub("", text).replace("ـ", "")
    return re.sub(r"[أإآٱ]", "ا", text).replace("ة", "ه").replace("ى", "ي")


def _latin_terms(text: str) -> Counter:
    counts: Counter = Counter()
    for match in _LATIN_PHRASE.finditer(text or ""):
        phrase = " ".join(match.group(0).split())
        if len(phrase) >= MIN_PHRASE_CHARS:
            counts[phrase] += 1
    return counts


def _arabic_phrases(text: str) -> Counter:
    """ثنائيات عربية متكرّرة بلا كلمات وظيفية.

    الثنائية لا الكلمة المفردة: «جدول» وحدها كلمة عامة، و«جدول
    المقرّرات» مصطلح. والحدّ عند الثنائية لا الثلاثية لأن العيّنة
    (محاضرة واحدة) أصغر من أن تُكرّر ثلاثيات بما يكفي.
    """
    words = [w for w in _ARABIC_WORD.findall(_fold(text or ""))]
    counts: Counter = Counter()
    for first, second in zip(words, words[1:]):
        if first in _STOPWORDS or second in _STOPWORDS:
            continue
        counts[f"{first} {second}"] += 1
    return counts


def build_glossary(transcript: TranscriptionResult,
                   keyframes: Sequence[KeyframeMetadata] = (),
                   limit: int = MAX_TERMS) -> List[GlossaryTerm]:
    """يبني قائمة مصطلحات مرشّحة — بلا تعريفات، وبلا أي نموذج."""
    body = transcript.full_text_clean or transcript.full_text_raw or ""
    terms: List[GlossaryTerm] = []
    seen: set = set()

    def add(term: str, count: int, origin: str) -> None:
        key = _fold(term).lower()
        if not key or key in seen or len(terms) >= limit:
            return
        seen.add(key)
        terms.append(GlossaryTerm(term=term, definition="", origin=origin,
                                  occurrences=count))

    # نصّ الشاشة أولًا: هو أدقّ مصدر للمصطلح — مكتوبٌ لا منطوق، فلا
    # يحمل خطأ تفريغ أصلًا (انظر video/screen_terms).
    screen_texts = [k.ocr_text for k in keyframes if (k.ocr_text or "").strip()]
    if screen_texts:
        for term, count in terms_from_screen_text(screen_texts):
            add(term, count, "screen")

    for term, count in _latin_terms(body).most_common():
        if count < MIN_OCCURRENCES:
            break
        add(term, count, "frequency")

    for phrase, count in _arabic_phrases(body).most_common():
        if count < MIN_OCCURRENCES:
            break
        add(phrase, count, "frequency")

    return terms
