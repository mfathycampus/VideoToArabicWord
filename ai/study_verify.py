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

import hashlib
import random
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

#: خيارات «كل ما سبق» تبقى أخيرة: خلطها يجعل «كل ما سبق» أوّل الخيارات
#: فيفقد معناه.
_ANCHORED_LAST = ("كل ما سبق", "جميع ما سبق", "جميع الاجابات", "كل الاجابات",
                  "لا شيء مما سبق", "لا شيء مما ذكر", "all of the above",
                  "none of the above")

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


def _texts(transcript: TranscriptionResult) -> Dict[int, str]:
    return {s.id: (s.text_clean or s.text_raw or "") for s in transcript.segments}


def _content_tokens(text: str) -> set:
    """كلمات المضمون بعد التطبيع: ثلاثة أحرف فأكثر، بلا «ال» التعريف.

    تجريدٌ خفيف لا تحليل صرفيّ: يكفي ليجعل «الواجبات» و«واجبات» كلمةً
    واحدة، ولا يدّعي فهمًا أعمق.
    """
    tokens = set()
    for word in fold(text).split():
        if word.startswith("ال") and len(word) >= 5:
            word = word[2:]
        if len(word) >= 3:
            tokens.add(word)
    return tokens


def answer_is_supported(answer: str, cited: Sequence[str],
                        screen_text: str = "") -> bool:
    """هل في المقاطع المستشهد بها (أو على الشاشة) أثرٌ للإجابة؟

    الفحص القديم يثبت أن **رقم** المقطع موجود، لا أن الإجابة منه. ونموذجٌ
    يستشهد بمقطع صحيح ويكتب إجابةً لا علاقة لها به يمرّ من كل بوابة.

    **والفحص متساهلٌ عمدًا:** يكفي أن تشارك الإجابة كلمة مضمونٍ واحدة مع
    ما استشهد به. الإجابة إعادة صياغة غالبًا، وإسقاط سؤالٍ سليمٍ لأن
    صياغته غير حرفية أسوأ من قبول سؤالٍ ضعيف. وما دون كلمتين لا يُحكم
    عليه («TCP»، «صح»).
    """
    wanted = _content_tokens(answer)
    if len(wanted) < 2:
        return True
    available = _content_tokens(" ".join(cited) + " " + screen_text)
    return bool(wanted & available)


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


def shuffled_options(question: str, options: List[str]) -> List[str]:
    """خلطٌ **حتميّ** لخيارات الاختيار من متعدد.

    النماذج تضع الإجابة الصحيحة في موضعٍ ثابتٍ تقريبًا (الأول أو الثاني)،
    فيتعلّم الطالب تخمين الموضع لا الجواب. والخلط بنواةٍ مشتقّةٍ من نصّ
    السؤال لا ``random`` عامّة: إعادة التوليد (``--rebuild``) تُخرج الترتيب
    نفسه، فلا يتغيّر ما رآه المعلّم ولا تتباين اختبارات الحزمة.
    """
    movable = [o for o in options if fold(o) not in _ANCHORED_LAST]
    anchored = [o for o in options if fold(o) in _ANCHORED_LAST]
    seed = int(hashlib.sha1(fold(question).encode("utf-8")).hexdigest()[:12], 16)
    random.Random(seed).shuffle(movable)
    return movable + anchored


# ---------------------------------------------------------------------
def verify(pack: StudyPack, transcript: TranscriptionResult,
           screen_texts: Sequence[str] = ()) -> StudyPack:
    """يعيد نسخة من الحزمة بلا العناصر التي لا تُثبت مصدرها."""
    spans = _spans(transcript)
    haystack = _haystack(transcript, screen_texts)
    dropped: List[str] = list(pack.dropped)

    pack.objectives = _verify_objectives(pack.objectives, spans, dropped)
    pack.glossary = _verify_glossary(pack.glossary, spans, haystack, dropped)
    pack.questions = _verify_questions(
        pack.questions, spans, dropped, _texts(transcript),
        " ".join(screen_texts))
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


def term_in_source(term: str, haystack: str) -> bool:
    """هل ورد المصطلح في المصدر؟ — حرفيًّا أو ككلمات مضمونه كلّها.

    المطابقة الحرفية وحدها أسقطت «الفئة (لم يتم التصحيح)» من مسردٍ حقيقيّ
    لأن النموذج وضع القوس تفسيرًا والمنطوق قال «الفئة… لم يتم التصحيح» بينهما
    كلام، فبقيت البطاقة المبنيّة عليه في الدليل نفسه والمصطلح مرفوض. فإن لم
    يرد حرفيًّا يُقبل إن وردت **كلُّ** كلماته المضمونية (الثلاثة أحرف فأكثر)
    في المصدر؛ والمصطلح بكلمةٍ واحدة يبقى حرفيًّا — لا تساهل فيه.
    """
    key = fold(term)
    if key and key in haystack:
        return True
    words = _content_tokens(term)
    if len(words) < 2:
        return False
    return words <= _content_tokens(haystack)


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
        if not term_in_source(term, haystack):
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
                      dropped: List[str], texts: Dict[int, str] = None,
                      screen_text: str = "") -> List[QuizQuestion]:
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
            item.options, item.answer = shuffled_options(question, options), match

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

        # الصح/الخطأ يُحكم على العبارة نفسها لا على «إجابة»؛ والاختيار
        # والقصير تُفحص إجابتهما مقابل المقاطع المستشهد بها.
        if item.kind != "true_false" and texts is not None:
            cited = [texts.get(i, "") for i in item.segment_ids]
            if not answer_is_supported(item.answer, cited, screen_text):
                dropped.append(f"إجابة لا يدعمها المقطع المستشهد به: «{label}»")
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
