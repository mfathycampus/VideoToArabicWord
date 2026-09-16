"""طبقة التحقّق — ما لا يُثبت مصدره يُسقَط (ADR-018).

**هذه الوحدة هي الفرق بين هذا البرنامج ومولّد أسئلة عام.** النموذج
اللغوي يكتب سؤالًا معقولًا عن موضوع المحاضرة سواءٌ قيل في التسجيل أم
لا، ويخترع خيارًا رابعًا ليكتمل الشكل، ويعرّف مصطلحًا لم يُذكر إطلاقًا
لأن السياق يوحي به. وكلّها مخرجاتٌ تبدو سليمة حتى يقرأها المعلّم الذي
ألقى المحاضرة.

فالقاعدة هنا صارمة عمدًا: كل عنصر يستشهد بمقاطع من ``transcription.json``
ويُفحص وجودها فعلًا؛ وكل إجابة اختيارٍ من متعدد يجب أن تطابق أحد
خياراتها؛ وكل مصطلح في المسرد يجب أن يرد في النصّ أو على الشاشة. وما
يُسقَط يُسجَّل بسببه في ``StudyPack.dropped`` — لأن حزمةً نصفُها مرفوض
بلا بيان تبدو حزمةً فقيرة بلا سبب ظاهر، فيُلام المولّد بدل المحتوى.

**والإسقاط هو السلوك الصحيح لا الخسارة.** عشرة أسئلة يثق المعلّم بكلّ
واحد منها أنفع من عشرين نصفُها يحتاج مراجعة — لأن المراجعة نفسها هي
العمل الذي جاء يتخلّص منه.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Sequence, Tuple

from config.schemas import (
    Flashcard,
    GlossaryTerm,
    LearningObjective,
    QuizQuestion,
    StudyPack,
    TranscriptionResult,
)

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

_TRUE_WORDS = {"صح", "صحيح", "نعم", "true", "t", "1"}
_FALSE_WORDS = {"خطأ", "خطا", "غير صحيح", "لا", "false", "f", "0"}

MIN_MCQ_OPTIONS = 3
MIN_QUESTION_CHARS = 10


def fold(text: str) -> str:
    """تطبيع للمقارنة وحدها — لا يُكتب الناتج في أي مخرج.

    المقارنة الحرفية بين إجابة النموذج وخياره تفشل على فروق لا يراها
    قارئ: ألف بهمزة وألف بلا همزة، تاء مربوطة وهاء، تشكيل، مسافة
    زائدة، نقطة في آخر أحدهما. وإسقاط سؤالٍ صحيح بسبب همزة خطأٌ بقدر
    قبول سؤال مخترع.
    """
    text = unicodedata.normalize("NFC", text or "").strip().lower()
    text = _DIACRITICS.sub("", text).replace("ـ", "")
    text = re.sub(r"[أإآٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و")
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _spans(transcript: TranscriptionResult) -> Dict[int, Tuple[float, float]]:
    return {s.id: (s.start, s.end) for s in transcript.segments}


def _haystack(transcript: TranscriptionResult,
              extra_texts: Sequence[str] = ()) -> str:
    parts = [transcript.full_text_clean or transcript.full_text_raw or ""]
    parts.extend(extra_texts)
    return fold(" ".join(parts))


def _resolve_sources(item, spans: Dict[int, Tuple[float, float]]) -> bool:
    """يتحقّق من المقاطع ويملأ النطاق الزمني. ``False`` = يُسقَط.

    ملء ``source_start`` هنا لا في المولّد مقصود: النموذج لا يُسأل عن
    التوقيت إطلاقًا — يُسأل عن **أي مقطع** بنى منه، والتوقيت يُشتقّ من
    المقطع نفسه. نموذجٌ يُملي التوقيتات يخترعها.
    """
    ids = [int(i) for i in (item.segment_ids or []) if str(i).lstrip("-").isdigit()]
    known = [i for i in ids if i in spans]
    if not known:
        return False
    item.segment_ids = known
    item.source_start = min(spans[i][0] for i in known)
    item.source_end = max(spans[i][1] for i in known)
    return True


# ---------------------------------------------------------------------
def verify(pack: StudyPack, transcript: TranscriptionResult,
           screen_texts: Sequence[str] = ()) -> StudyPack:
    """يعيد نسخة من الحزمة بلا العناصر التي لا تُثبت مصدرها."""
    spans = _spans(transcript)
    haystack = _haystack(transcript, screen_texts)
    dropped: List[str] = list(pack.dropped)

    pack.objectives = _verify_objectives(pack.objectives, spans, dropped)
    pack.glossary = _verify_glossary(pack.glossary, spans, haystack, dropped)
    pack.questions = _verify_questions(pack.questions, spans, dropped)
    pack.flashcards = _verify_flashcards(pack.flashcards, spans, dropped)
    pack.dropped = dropped
    return pack


def _verify_objectives(items: List[LearningObjective],
                       spans, dropped: List[str]) -> List[LearningObjective]:
    kept: List[LearningObjective] = []
    seen: set = set()
    for item in items:
        text = " ".join((item.text or "").split())
        if len(text) < MIN_QUESTION_CHARS:
            dropped.append(f"هدف قصير أو فارغ: «{text[:40]}»")
            continue
        key = fold(text)
        if key in seen:
            dropped.append(f"هدف مكرّر: «{text[:40]}»")
            continue
        if not _resolve_sources(item, spans):
            dropped.append(f"هدف بلا مقطع مصدر موجود: «{text[:40]}»")
            continue
        item.text = text
        seen.add(key)
        kept.append(item)
    return kept


def _verify_glossary(items: List[GlossaryTerm], spans, haystack: str,
                     dropped: List[str]) -> List[GlossaryTerm]:
    kept: List[GlossaryTerm] = []
    seen: set = set()
    for item in items:
        term = " ".join((item.term or "").split())
        if not term:
            continue
        key = fold(term)
        if not key or key in seen:
            continue
        # المصطلح الذي لا يرد في التفريغ ولا على الشاشة مخترَع — مهما
        # بدا صحيحًا في مادّته. هذا أشيع هلوسات المسرد.
        if key not in haystack:
            dropped.append(f"مصطلح لا يرد في المصدر: «{term}»")
            continue
        # المشتقّ إحصائيًّا مصدره نصّ الشاشة لا مقطع بعينه، فلا يُطالَب
        # بـ``segment_ids`` — وشرطُ وروده في المصدر مفروضٌ عليه أعلاه.
        if item.origin == "ai" and not _resolve_sources(item, spans):
            dropped.append(f"مصطلح بلا مقطع مصدر موجود: «{term}»")
            continue
        item.term = term
        item.definition = " ".join((item.definition or "").split())
        seen.add(key)
        kept.append(item)
    return kept


def _verify_questions(items: List[QuizQuestion], spans,
                      dropped: List[str]) -> List[QuizQuestion]:
    kept: List[QuizQuestion] = []
    seen: set = set()
    for item in items:
        question = " ".join((item.question or "").split())
        answer = " ".join((item.answer or "").split())
        label = question[:45] or "(سؤال بلا نصّ)"

        if len(question) < MIN_QUESTION_CHARS or not answer:
            dropped.append(f"سؤال ناقص: «{label}»")
            continue
        key = fold(question)
        if key in seen:
            dropped.append(f"سؤال مكرّر: «{label}»")
            continue

        if item.kind == "mcq":
            options = []
            for option in item.options or []:
                option = " ".join((option or "").split())
                if option and fold(option) not in {fold(o) for o in options}:
                    options.append(option)
            if len(options) < MIN_MCQ_OPTIONS:
                dropped.append(
                    f"سؤال اختيار بأقل من {MIN_MCQ_OPTIONS} خيارات "
                    f"متمايزة: «{label}»")
                continue
            # الإجابة يجب أن تكون **أحد الخيارات**. نموذجٌ يكتب إجابة
            # خارجها ينتج سؤالًا لا حلّ له، ويظنّ الطالب الخطأ خطأه.
            match = next((o for o in options if fold(o) == fold(answer)), None)
            if match is None:
                dropped.append(f"إجابة خارج الخيارات: «{label}»")
                continue
            item.options, item.answer = options, match

        elif item.kind == "true_false":
            folded = fold(answer)
            if folded in {fold(w) for w in _TRUE_WORDS}:
                item.answer = "صح"
            elif folded in {fold(w) for w in _FALSE_WORDS}:
                item.answer = "خطأ"
            else:
                dropped.append(f"إجابة صح/خطأ غير مفهومة: «{label}»")
                continue
            item.options = []

        else:
            item.options = []
            item.answer = answer

        if not _resolve_sources(item, spans):
            dropped.append(f"سؤال بلا مقطع مصدر موجود: «{label}»")
            continue

        item.question = question
        item.explanation = " ".join((item.explanation or "").split())
        seen.add(key)
        kept.append(item)
    return kept


def _verify_flashcards(items: List[Flashcard], spans,
                       dropped: List[str]) -> List[Flashcard]:
    kept: List[Flashcard] = []
    seen: set = set()
    for item in items:
        front = " ".join((item.front or "").split())
        back = " ".join((item.back or "").split())
        if not front or not back:
            dropped.append(f"بطاقة ناقصة: «{front[:40]}»")
            continue
        key = fold(front)
        if key in seen:
            continue
        if not _resolve_sources(item, spans):
            dropped.append(f"بطاقة بلا مقطع مصدر موجود: «{front[:40]}»")
            continue
        item.front, item.back = front, back
        seen.add(key)
        kept.append(item)
    return kept
