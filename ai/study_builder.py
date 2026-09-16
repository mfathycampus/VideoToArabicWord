"""توليد الحزمة التعليمية من التفريغ — أسئلة وأهداف ومسرد وبطاقات.

**الفكرة كلّها في سطر واحد من المُوجِّه:** النموذج لا يُسأل عن التوقيت
ولا يُترك ليتحدّث عن «موضوع المحاضرة». يُعطى مقاطع **مرقّمة** بأرقامها
الحقيقية من ``transcription.json``، ويُطالَب بأن يُرفق مع كل عنصر
يكتبه أرقام المقاطع التي بناه منها. ثم تفحص ``ai/study_verify`` تلك
الأرقام فعلًا وتُسقط ما لا يصحّ.

فالنتيجة ليست «أسئلة عن الشبكات» بل أسئلة عن **ما قاله هذا المحاضر في
هذه الدقيقة**، ولكل سؤال مرجعٌ يقفز إليه المعلّم ليتأكّد بنفسه. وهذا
هو الفرق الذي يجعل الأداة تُستعمل مرّتين.

**عقد الفشل منقول من ``ai/rewriter``:** فشلُ دفعة لا يُسقط البقية،
وفشلُ الحزمة كلّها لا يُفشل المهمة — يخرج المستند كما يخرج اليوم
بالضبط. والمسار بلا نموذج (``ai/study_terms``) يظلّ يُنتج قائمة
مصطلحات لمن لا يملك مزوّدًا أصلًا.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from ai.providers import LLMProvider, RewriteUnavailableError, complete_with_retry
from ai.rewriter import _extract_json
from ai.study_terms import build_glossary
from ai.study_verify import verify
from config.schemas import (
    AudioSegment,
    DocumentPlan,
    Flashcard,
    GlossaryTerm,
    KeyframeMetadata,
    LearningObjective,
    QuizQuestion,
    StudyPack,
    TranscriptionResult,
)
from utils.logger import logger

SYSTEM_PROMPT = """أنت مصمّم مواد تعليمية عربي. أمامك مقاطع مرقّمة من تفريغ محاضرة.

القواعد الملزمة:
1. لا تكتب شيئًا ليس في المقاطع المعطاة. لا معلومة من معرفتك العامة عن الموضوع.
2. لكل عنصر تكتبه أرفق "s": قائمة أرقام المقاطع التي بنيته منها، من الأرقام
   المعطاة لك حرفيًا. أي عنصر بلا مصدر صحيح يُرفض ويُهمل.
3. أسئلة الاختيار من متعدد: أربعة خيارات، و"answer" نصٌّ **مطابق حرفيًا**
   لأحد الخيارات. لا تكتب حرفًا ولا رقمًا مكان الإجابة.
4. الخيارات الخاطئة معقولة ومن مجال المحاضرة نفسها — لا خيارات سخيفة
   يستبعدها الطالب بلا تفكير.
5. لا تسأل عن الحشو ولا عن ترتيب الكلام («ماذا ذكر المحاضر أولًا؟»)،
   ولا عن ألفاظ بعينها. اسأل عن المعنى والمفهوم.
6. الهدف التعليمي يبدأ بفعل قابل للقياس: «يشرح»، «يميّز بين»، «يحسب».
7. التعريف في المسرد جملة واحدة مما ورد في المقاطع لا من عندك.
8. اكتب بالعربية الفصحى. أبقِ المصطلح الأجنبي كما ورد حرفيًا.

أعِد **JSON فقط** بلا أي نص خارجه:
{"objectives": [{"text": "يميّز بين TCP و UDP", "s": [12, 13]}],
 "glossary": [{"term": "TCP", "definition": "بروتوكول يضمن الوصول والترتيب", "s": [12]}],
 "questions": [{"kind": "mcq", "q": "نص السؤال؟",
                "options": ["الأول", "الثاني", "الثالث", "الرابع"],
                "answer": "الثاني", "why": "سبب الإجابة", "level": "medium", "s": [12]},
               {"kind": "true_false", "q": "عبارة تُحكم بصح أو خطأ",
                "answer": "صح", "why": "", "s": [13]},
               {"kind": "short", "q": "سؤال إجابته سطر", "answer": "الإجابة", "s": [13]}],
 "flashcards": [{"front": "المصطلح أو السؤال", "back": "الإجابة المختصرة", "s": [12]}]}"""


@dataclass
class StudyConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    api_key: str = ""
    workspace_id: str = ""
    timeout_seconds: int = 180
    #: حجم الدفعة بالحروف. أصغر من دفعة إعادة الصياغة عمدًا: كتابة سؤال
    #: تحتاج تركيزًا على مقطع بعينه، والسياق الأوسع يُنتج أسئلة عامة.
    batch_chars: int = 2500
    questions_per_batch: int = 3
    max_questions: int = 20
    max_objectives: int = 8
    max_glossary: int = 25
    max_flashcards: int = 30
    #: استكمال المسرد إحصائيًّا بما لم يذكره النموذج. مجّاني ولا يحتاجه.
    augment_glossary: bool = True


ProgressFn = Callable[[int, int], None]


def _batches(segments: Sequence[AudioSegment],
             batch_chars: int) -> List[List[AudioSegment]]:
    batches: List[List[AudioSegment]] = []
    current: List[AudioSegment] = []
    size = 0
    for segment in segments:
        text = segment.text_clean or segment.text_raw
        if current and size + len(text) > batch_chars:
            batches.append(current)
            current, size = [], 0
        current.append(segment)
        size += len(text)
    if current:
        batches.append(current)
    return batches


def _render_batch(batch: Sequence[AudioSegment]) -> str:
    """المقاطع مرقّمة بأرقامها الحقيقية — هي مفتاح التتبّع كلّه."""
    return "\n".join(
        f"[{s.id}] {(s.text_clean or s.text_raw).strip()}"
        for s in batch if (s.text_clean or s.text_raw).strip())


def _ints(raw) -> List[int]:
    out: List[int] = []
    for value in (raw or []):
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


class StudyBuilder:
    """يبني ``StudyPack`` من التفريغ. المزوّد مُحقَن ليكون قابلًا للاختبار."""

    def __init__(self, provider: LLMProvider, config: StudyConfig) -> None:
        self.provider = provider
        self.config = config

    # ------------------------------------------------------------------
    def build(self, plan: DocumentPlan, transcript: TranscriptionResult,
              keyframes: Sequence[KeyframeMetadata] = (),
              progress: Optional[ProgressFn] = None) -> StudyPack:
        config = self.config
        pack = StudyPack(
            title=plan.title or "",
            generated_by=f"ai:{config.provider}"
                         + (f"/{config.model}" if config.model else ""))

        batches = _batches(transcript.segments, config.batch_chars)
        if not batches:
            return pack

        failures = 0
        for index, batch in enumerate(batches, start=1):
            if progress:
                progress(index, len(batches))
            if len(pack.questions) >= config.max_questions:
                # بلغنا السقف: بقية الدفعات نداءاتٌ تُدفع ثمنها ثم
                # تُقتطع. التوقّف هنا يوفّرها كاملةً.
                break
            try:
                raw = complete_with_retry(
                    self.provider, SYSTEM_PROMPT, self._user_prompt(batch),
                    max_tokens=2048, timeout=config.timeout_seconds)
            except RewriteUnavailableError as exc:
                failures += 1
                logger.warning(
                    f"تعذّرت الحزمة التعليمية للدفعة {index}/{len(batches)}: {exc}")
                if not getattr(exc, "retryable", True) and failures == 1:
                    # خطأ نهائي (مفتاح خاطئ، نموذج غير متاح) لن تنجح
                    # معه دفعةٌ تالية. الاستمرار يعني تكرار الفشل نفسه
                    # عشرين مرّة أمام المستخدم.
                    logger.warning("خطأ نهائي من المزوّد — أُوقفت بقية الدفعات.")
                    break
                continue
            except Exception as exc:
                failures += 1
                logger.warning(f"عطل غير متوقّع في الدفعة {index}: {exc}")
                continue

            parsed = _extract_json(raw)
            if not isinstance(parsed, dict):
                failures += 1
                pack.dropped.append(f"ردّ غير صالح في الدفعة {index}")
                continue
            self._absorb(parsed, pack)

        if failures:
            logger.info(
                f"الحزمة التعليمية: {failures} من {len(batches)} دفعة فشلت — "
                "أُكملت البقية.")

        pack = verify(pack, transcript,
                      [k.ocr_text for k in keyframes if (k.ocr_text or "").strip()])
        self._apply_caps(pack)
        if config.augment_glossary:
            _augment_glossary(pack, transcript, keyframes, config.max_glossary)
        _log_summary(pack)
        return pack

    # ------------------------------------------------------------------
    def _user_prompt(self, batch: Sequence[AudioSegment]) -> str:
        config = self.config
        return (
            f"اكتب من المقاطع التالية: هدفًا تعليميًّا أو هدفين، وحتى ثلاثة "
            f"مصطلحات بتعريفاتها، و{config.questions_per_batch} أسئلة متنوّعة "
            f"(اختيار من متعدد، صح/خطأ، سؤال قصير)، وحتى ثلاث بطاقات مراجعة.\n"
            f"إن لم تكفِ المقاطع لعنصرٍ ما، اترك قائمته فارغة — لا تملأها "
            f"بما ليس فيها.\n\n"
            f"المقاطع:\n{_render_batch(batch)}")

    @staticmethod
    def _absorb(parsed: Dict, pack: StudyPack) -> None:
        """يحوّل ردّ النموذج إلى عناصر العقد. التحقّق يأتي لاحقًا."""
        for entry in parsed.get("objectives") or []:
            if isinstance(entry, dict) and entry.get("text"):
                pack.objectives.append(LearningObjective(
                    text=str(entry["text"]), segment_ids=_ints(entry.get("s"))))

        for entry in parsed.get("glossary") or []:
            if isinstance(entry, dict) and entry.get("term"):
                pack.glossary.append(GlossaryTerm(
                    term=str(entry["term"]),
                    definition=str(entry.get("definition") or ""),
                    origin="ai", segment_ids=_ints(entry.get("s"))))

        for entry in parsed.get("questions") or []:
            if not isinstance(entry, dict):
                continue
            question = entry.get("q") or entry.get("question")
            if not question:
                continue
            kind = str(entry.get("kind") or "mcq").strip().lower()
            if kind not in ("mcq", "true_false", "short"):
                kind = "mcq"
            level = str(entry.get("level") or "medium").strip().lower()
            pack.questions.append(QuizQuestion(
                kind=kind, question=str(question),
                options=[str(o) for o in (entry.get("options") or [])],
                answer=str(entry.get("answer") or ""),
                explanation=str(entry.get("why")
                                or entry.get("explanation") or ""),
                difficulty=level if level in ("easy", "medium", "hard")
                else "medium",
                segment_ids=_ints(entry.get("s"))))

        for entry in parsed.get("flashcards") or []:
            if isinstance(entry, dict) and entry.get("front"):
                pack.flashcards.append(Flashcard(
                    front=str(entry["front"]),
                    back=str(entry.get("back") or ""),
                    segment_ids=_ints(entry.get("s"))))

    def _apply_caps(self, pack: StudyPack) -> None:
        config = self.config
        pack.objectives = pack.objectives[:config.max_objectives]
        pack.glossary = pack.glossary[:config.max_glossary]
        pack.questions = pack.questions[:config.max_questions]
        pack.flashcards = pack.flashcards[:config.max_flashcards]


# ---------------------------------------------------------------------
def _augment_glossary(pack: StudyPack, transcript: TranscriptionResult,
                      keyframes: Sequence[KeyframeMetadata],
                      limit: int) -> None:
    """يُكمل المسرد بمصطلحات نصّ الشاشة التي لم يذكرها النموذج.

    المصطلح المكتوب على الشاشة أوثق من المنطوق: لا يحمل خطأ تفريغ
    أصلًا. وإغفال النموذج له شائع لأنه يمرّ في الكلام عَرَضًا.
    """
    from ai.study_verify import fold

    known = {fold(term.term) for term in pack.glossary}
    for candidate in build_glossary(transcript, keyframes, limit=limit):
        if len(pack.glossary) >= limit:
            break
        if fold(candidate.term) not in known:
            known.add(fold(candidate.term))
            pack.glossary.append(candidate)


def build_without_model(plan: DocumentPlan, transcript: TranscriptionResult,
                        keyframes: Sequence[KeyframeMetadata] = (),
                        limit: int = 25) -> StudyPack:
    """المسار بلا مزوّد: قائمة مصطلحات وحدها، بلا ادّعاء تعريفات.

    تُستدعى حين يكون التوليد مطلوبًا والمزوّد غير متاح. تخرج حزمة
    صغيرة صادقة بدل لا شيء — ومصطلحاتها تُلصق في خانة «مصطلحات المادة»
    فترفع دقّة تفريغ المحاضرة التالية.
    """
    pack = StudyPack(title=plan.title or "", generated_by="screen")
    pack.glossary = build_glossary(transcript, keyframes, limit=limit)
    _log_summary(pack)
    return pack


def _log_summary(pack: StudyPack) -> None:
    line = (f"الحزمة التعليمية: {len(pack.objectives)} هدفًا · "
            f"{len(pack.glossary)} مصطلحًا · {len(pack.questions)} سؤالًا · "
            f"{len(pack.flashcards)} بطاقة")
    if pack.dropped:
        # العدد يُذكر دائمًا، والأسباب في دليل المذاكرة. حزمةٌ نصفُها
        # مرفوض بلا هذا السطر تبدو حزمةً فقيرة بلا سبب ظاهر.
        line += f" · أُسقط {len(pack.dropped)} عنصرًا في التحقّق"
    logger.info(line)
