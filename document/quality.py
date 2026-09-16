"""بوابة جودة تقيس **قابلية القراءة** — لا اكتمال النقل.

سبب إعادة الكتابة: النسخة السابقة كانت مثبَّتة رياضيًا. أربعة من
مقاييسها الخمسة لا تستطيع أن تنزل عن 100:

* ``text_completeness`` و``figure_coverage`` — يفرضهما
  ``TimelineMatcher.assert_lossless`` **قبل** القياس (ADR-010). قياس ما
  يضمنه فحصٌ سابق ليس قياسًا.
* ``section_quality`` — يعاقب الأقسام الفارغة، والمُخطِّط يحذفها قبل
  البناء أصلًا.
* ``provenance_coverage`` — كل كتلة تحمل ``segment_ids`` أو طابعًا
  زمنيًا بحكم بنائها.

والخامس ``ocr_coverage`` يقيس **وجود Tesseract على الجهاز**، لا جودة
المستند، ولا ينظر إلى الخطة إطلاقًا.

النتيجة: 95.0 أو 100.0 في كل تشغيلة من 27، وعتبة الرسوب 85 لا تُبلَغ
أبدًا. والأسوأ أن ``_ratio(0, 0)`` كان يُعيد 100، فمستندٌ بلا صورة
**واحدة** يحصل على 100 — أعلى من مستند فيه خمس صور (95). المقياس كان
يكافئ غياب المحتوى.

──────────────────────────────────────────────────────────────────────

المقاييس هنا مبنية على قاعدتين:

1. **كل مقياس يجب أن يستطيع الفشل.** لا مقياس يفرضه فحصٌ سابق.
2. **غير المنطبق يُستبعَد، لا يُمنَح 100.** مستند بلا صور لا يُقاس
   تعليقُ صوره — ولا يُكافأ على غيابها.

وما يفرضه ``assert_lossless`` لم يُحذف، بل عاد إلى موضعه الصحيح: فحصٌ
بنيوي في ``violations``، لا درجة.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from config.schemas import DocumentPlan, KeyframeMetadata
from core.exceptions import QualityGateError
from document.planner import MIN_PARAGRAPH_CHARS, PAUSE_BREAK_SECONDS
from document.titles import title_defects

#: نهايات الجملة العربية واللاتينية، مع علامات الاقتباس المغلقة.
_SENTENCE_END = re.compile(r'[.!?؟…:»"\')\]]\s*$')

#: تعريف «العنوان المفيد» يعيش في ``document/titles.py`` ويشترك فيه
#: المُخطِّط والمقياس. كان كلٌّ منهما يحمل تعريفه فافترقا: المُخطِّط
#: يُخرج «أخطر القلب وأخطر القلب وأخطر القلب» والمقياس يمنحه 100٪.

#: تعليق تلقائي لا يصف الصورة. الشكل الذي يولّده المُخطِّط فعلًا هو
#: «لقطة عند 00:00:03» (``document/planner.py:158``)؛ النمطان الآخران
#: احتياط لصيغ سابقة ولمخرجات إعادة الصياغة.
#:
#: ضُبط هذا النمط بعد أن أعطى المقياس **100٪ كاذبة** على كل مستند في
#: مصفوفة القبول: النمط الأول كان يطابق «صورة N» وحدها، وهي صيغة لا
#: يولّدها البرنامج إطلاقًا. مقياس لا يطابق المخرج الحقيقي لا يقيس
#: شيئًا — وهو العيب نفسه الذي أُعيدت كتابة هذا الملف من أجله.
_GENERIC_CAPTION = re.compile(
    r"^\s*(?:لقطة\s+عند|صورة|شكل)\s*[\d:—\-\s]*$")

#: حشو الكلام المنطوق. لا يدخل في الدرجة — ADR-010 يمنع حذفه أصلًا —
#: بل يُبلَّغ كقياس لحجم الفرصة، وليكون خطّ أساس إن فُتح باب الحذف.
_FILLER = re.compile(
    r"\b(?:يعني|طيب|أه+|امم+|اه+|اللي\s+هو|بقى|كده|ماشي|okay|ok)\b",
    re.IGNORECASE)

#: الحدّ الأعلى لطول الفقرة المقروءة. يمنع الجدار: قياس فعلي على محاضرة
#: ساعة بلا صور أعطى فقرة واحدة طولها 5,261 حرفًا، لأن القطع كان يشترط
#: علامة ترقيم قد لا يُخرجها النموذج.
#:
#: والحدّ الأدنى ``MIN_PARAGRAPH_CHARS`` يعيش في المُخطِّط: هو نفسه
#: العتبة التي يشترطها قبل أن يقطع عند سكتة. لو افترق التعريفان لعاقب
#: المقياسُ المُخطِّطَ على حدٍّ لا يعرفه.
MAX_PARAGRAPH_CHARS = 1400


@dataclass(frozen=True)
class Metric:
    """مقياس واحد — أو سبب عدم انطباقه.

    ``value is None`` تعني «غير منطبق»: يُستبعَد من الدرجة بدل أن
    يُمنَح 100. هذا هو الفرق الذي جعل مستندًا بلا صور يتفوّق على مستند
    فيه صور.
    """
    name: str
    value: Optional[float]
    weight: float
    detail: str = ""

    @property
    def applicable(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class QualityReport:
    """مؤشرات قابلية القراءة، مستقلة عن صيغة DOCX/PDF."""
    overall: Optional[float]
    metrics: tuple[Metric, ...]
    diagnostics: dict = field(default_factory=dict)
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def get(self, name: str) -> Optional[float]:
        for metric in self.metrics:
            if metric.name == name:
                return metric.value
        raise KeyError(name)

    def as_dict(self) -> dict:
        return {
            "overall": None if self.overall is None else round(self.overall, 2),
            "metrics": {
                m.name: {
                    "value": None if m.value is None else round(m.value, 2),
                    "weight": m.weight,
                    "detail": m.detail,
                }
                for m in self.metrics
            },
            "diagnostics": self.diagnostics,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
        }


def _pct(part: int, whole: int) -> Optional[float]:
    """نسبة مئوية، أو ``None`` حين لا يوجد ما يُقاس.

    ``None`` لا ``100`` — الفرق كله هنا: لا مكافأة على غياب المحتوى.
    """
    if whole <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * part / whole))


def _text_of(block) -> str:
    if block.kind == "bullets":
        return " ".join(block.bullets)
    return block.text


# ── المقاييس ─────────────────────────────────────────────────────────

def _paragraph_flow(text_blocks: list) -> Metric:
    """أي نسبة من الفقرات تقف عند حدّ طبيعي في الكلام؟

    الفقرة المقطوعة في منتصف الفكرة أوضح عيب في المخرج الحالي: كل صورة
    تقطع الفقرة الجارية أيًّا كان موضعها من الجملة.

    **الحدّ الطبيعي ترقيمٌ أو سكتة، لا ترقيمٌ وحده.** النسخة الأولى
    اشترطت علامة نهاية جملة، وذلك يقيس ترقيمَ نموذج التفريغ لا عملَ
    المُخطِّط: على 8.5 دقائق عربية حقيقية وضع النموذج **13 علامة في
    766 كلمة**، فسقفُ المقياس محكومٌ بثلاث عشرة نقطة مهما أحسن
    المُخطِّط. والفقرة التي تقف عند سكتة من ثانية ليست مقطوعة في منتصف
    جملة — هي تقف حيث وقف المتحدّث.

    ولا تساهل بلا دليل: يُعتدّ بالسكتة **بين هذه الفقرة والتي تليها**
    محسوبةً من الطوابع الزمنية المحفوظة في الكتل نفسها، لا بتقدير.
    """
    finished = 0
    for i, block in enumerate(text_blocks):
        if _SENTENCE_END.search(_text_of(block).strip()):
            finished += 1
            continue
        if i + 1 == len(text_blocks):
            # لا إعفاء للفقرة الأخيرة. جرّبتُ اعتبار نهاية المستند وقفةً
            # بطبيعتها، فأعطى ذلك نصفَ الدرجة لمستندٍ **كلتا فقرتيه
            # مقطوعتان**. الإعفاء يجعل المقياس أعجزَ عن الفشل كلّما قلّ
            # عدد الفقرات — وهو عكس ما نريد.
            continue
        end = getattr(block, "source_end", None)
        nxt = getattr(text_blocks[i + 1], "source_start", None)
        if end is not None and nxt is not None and nxt - end >= PAUSE_BREAK_SECONDS:
            finished += 1

    broken = len(text_blocks) - finished
    return Metric(
        "paragraph_flow", _pct(finished, len(text_blocks)), 0.30,
        f"{broken} فقرة مقطوعة في منتصف الجملة من {len(text_blocks)}")


def _paragraph_balance(text_blocks: list) -> Metric:
    """أي نسبة من الفقرات ضمن طول مقروء؟

    الطرفان كلاهما عيب: الشظية بلا سياق، والجدار لا يُقرأ.
    """
    lengths = [len(_text_of(b).strip()) for b in text_blocks]
    within = sum(1 for n in lengths
                 if MIN_PARAGRAPH_CHARS <= n <= MAX_PARAGRAPH_CHARS)
    short = sum(1 for n in lengths if n < MIN_PARAGRAPH_CHARS)
    long_ = sum(1 for n in lengths if n > MAX_PARAGRAPH_CHARS)
    # التفصيل يسمّي سبب الرسوب الفعلي. النسخة السابقة كانت تذكر أطول فقرة
    # وحدها، فمستندٌ كلّ فقراته شظايا نال 0٪ برسالة «أطول فقرة 139 حرفًا
    # (الحدّ 1400)» — توحي بأن كل شيء سليم.
    return Metric(
        "paragraph_balance", _pct(within, len(lengths)), 0.20,
        f"{short} فقرة أقصر من {MIN_PARAGRAPH_CHARS} حرفًا · "
        f"{long_} أطول من {MAX_PARAGRAPH_CHARS} — من {len(lengths)}")


def _section_titling(plan: DocumentPlan) -> Metric:
    """أي نسبة من الأقسام تحمل عنوانًا يقول شيئًا؟

    «القسم الأول — من 00:00:00» يملأ خانة العنوان ولا يفيد قارئًا. مستند
    كل عناوينه كذلك فهرسُه بلا وظيفة.

    ولا يكفي أن يكون العنوان **غير** ترتيبي. النسخة الأولى اكتفت بذلك،
    فأعطت 100٪ لمستند عناوينه «أخطر القلب وأخطر القلب وأخطر القلب» —
    حلقة هلوسة من التفريغ. المقياس كان يكشف غياب العنوان لا رداءته،
    فيمرّ عليه الفشلُ نجاحًا. الآن يُحتسب العنوان المعيب رسوبًا،
    و``title_defects`` تسمّي السبب في تفصيل المقياس.
    """
    titles = [s.title for s in plan.sections]
    defective = [(t, title_defects(t)) for t in titles]
    meaningful = sum(1 for t, d in defective if t.strip() and not d)

    reasons = Counter(d for _, ds in defective for d in ds)
    detail = "، ".join(f"{n} {name}" for name, n in reasons.most_common(3))
    return Metric(
        "section_titling", _pct(meaningful, len(titles)), 0.20,
        f"{len(titles) - meaningful} عنوانًا بلا فائدة من {len(titles)}"
        + (f" ({detail})" if detail else ""))


def _section_balance(plan: DocumentPlan) -> Metric:
    """هل الأقسام متقاربة الحجم؟

    قسم يبتلع نصف المحاضرة وآخر من فقرتين يعنيان أن الحدود وقعت على
    الساعة لا على المعنى.
    """
    sizes = [sum(len(_text_of(b)) for b in s.blocks) for s in plan.sections]
    sizes = [n for n in sizes if n]
    if len(sizes) < 2:
        return Metric("section_balance", None, 0.10, "قسم واحد أو لا شيء")

    mean = statistics.fmean(sizes)
    # معامل الاختلاف: 0 تطابق تام، ≥1 تفاوت شديد. نحوّله إلى درجة.
    spread = statistics.pstdev(sizes) / mean if mean else 0.0
    return Metric(
        "section_balance", max(0.0, 100.0 * (1.0 - min(spread, 1.0))), 0.10,
        f"معامل اختلاف الأحجام {spread:.2f}")


#: التعليق يُبنى «نصّ — 00:05:59». الطابع يُنزع قبل الحكم على النصّ.
_CAPTION_STAMP = re.compile(r"\s*[—-]\s*\d{1,2}:\d{2}(?::\d{2})?\s*$")


def _figure_captioning(figure_blocks: list) -> Metric:
    """أي نسبة من الصور تحمل تعليقًا يصفها؟

    غير منطبق على مستند بلا صور — لا يُكافأ ولا يُعاقب.

    **يُحكَم على التعليق نفسه، لا على وجود نصّ شاشة.** الشرط السابق كان
    ``b.ocr_text.strip()`` — أي «هل قرأ Tesseract شيئًا؟» — فأعطى
    ‏100٪ لمستند تعليقاته «timetable?semesterid=1186991» و«Bi Lessons |
    Chalk - Googke Chrome» و«ston. © Freee (ease text ner Al stern».
    خمس عشرة صورة، ولا تعليق واحد يصف صورته. وقد أعلنتُ تلك المئة
    نجاحًا في تسليم سابق، وكانت خطأً: المقياس كان يقيس **تشغيل
    Tesseract** لا جودة التعليق.

    والآن يمرّ التعليق بفحص ``title_defects`` نفسه: مسار إنترنت أو
    شريط متصفّح أو قراءة فاشلة = صورة بلا تعليق.
    """
    if not figure_blocks:
        return Metric("figure_captioning", None, 0.15, "لا صور في المستند")

    described = 0
    for block in figure_blocks:
        caption = (block.caption or "").strip()
        if not caption or _GENERIC_CAPTION.match(caption):
            continue
        if title_defects(_CAPTION_STAMP.sub("", caption)):
            continue
        described += 1
    return Metric(
        "figure_captioning", _pct(described, len(figure_blocks)), 0.15,
        f"{len(figure_blocks) - described} صورة بلا تعليق يصفها")


def _figure_distinctness(figure_blocks: list) -> Metric:
    """أي نسبة من الصور تحمل محتوى شاشة مختلفًا عن غيرها؟

    شريحة تُبنى تدريجيًا (نقطة تُضاف) تنجو من فحص التكرار البصري وتظهر
    ثلاث مرات. نصّ الشاشة يكشفها. غير منطبق بلا OCR.
    """
    texts = [b.ocr_text.strip() for b in figure_blocks if b.ocr_text.strip()]
    if len(texts) < 2:
        return Metric("figure_distinctness", None, 0.05,
                      "نصّ شاشة غير متاح (OCR معطّل أو صور أقل من اثنتين)")

    duplicates = 0
    for index, text in enumerate(texts):
        others = texts[:index] + texts[index + 1:]
        if any(text in other for other in others):
            duplicates += 1
    return Metric(
        "figure_distinctness", _pct(len(texts) - duplicates, len(texts)), 0.05,
        f"{duplicates} صورة نصّها مُحتوى داخل صورة أخرى")


# ── التقييم ──────────────────────────────────────────────────────────

def evaluate(plan: DocumentPlan,
             keyframes: Iterable[KeyframeMetadata] = ()) -> QualityReport:
    """يقيس قابلية قراءة الخطة، ويفصل الخلل البنيوي عن الدرجة."""
    text_blocks: list = []
    figure_blocks: list = []
    seen_segments: list[int] = []
    seen_figures: list[int] = []
    filler_hits = words = 0

    for section in plan.sections:
        for block in section.blocks:
            seen_segments.extend(block.segment_ids)
            if block.image_id is not None:
                seen_figures.append(block.image_id)
                figure_blocks.append(block)
            else:
                text = _text_of(block)
                if text.strip():
                    text_blocks.append(block)
                    words += len(text.split())
                    filler_hits += len(_FILLER.findall(text))

    metrics = (
        _paragraph_flow(text_blocks),
        _paragraph_balance(text_blocks),
        _section_titling(plan),
        _section_balance(plan),
        _figure_captioning(figure_blocks),
        _figure_distinctness(figure_blocks),
    )

    # المتوسط على المنطبق وحده، بأوزان مُعاد تطبيعها. مقياس غير منطبق
    # لا يرفع الدرجة ولا يخفضها — يختفي.
    applicable = [m for m in metrics if m.applicable]
    total_weight = sum(m.weight for m in applicable)
    overall = (sum(m.value * m.weight for m in applicable) / total_weight
               if total_weight else None)

    # ── الخلل البنيوي: فحص لا درجة ───────────────────────────────────
    violations: list[str] = []
    duplicate_segments = len(seen_segments) - len(set(seen_segments))
    duplicate_figures = len(seen_figures) - len(set(seen_figures))
    empty_sections = sum(1 for s in plan.sections if not s.blocks)

    if duplicate_segments:
        violations.append(f"{duplicate_segments} مقطعًا مكرّرًا داخل الخطة")
    if duplicate_figures:
        violations.append(f"{duplicate_figures} صورة مكرّرة داخل الخطة")
    if plan.total_segments_in and len(set(seen_segments)) < plan.total_segments_in:
        missing = plan.total_segments_in - len(set(seen_segments))
        violations.append(f"{missing} مقطعًا لم يصل إلى الخطة (ADR-010)")
    if not plan.sections and (plan.total_segments_in or plan.total_figures_in):
        violations.append("الخطة بلا أقسام رغم وجود محتوى مصدر")

    warnings: list[str] = []
    if empty_sections:
        warnings.append(f"{empty_sections} قسمًا فارغًا")
    for metric in metrics:
        if metric.applicable and metric.value < 50.0:
            warnings.append(f"{metric.name}: {metric.value:.0f}% — {metric.detail}")

    keyframe_list = list(keyframes)
    diagnostics = {
        "text_blocks": len(text_blocks),
        "figure_blocks": len(figure_blocks),
        "sections": len(plan.sections),
        "empty_sections": empty_sections,
        "duplicate_segments": duplicate_segments,
        "duplicate_figures": duplicate_figures,
        "words": words,
        # ليس مقياسًا: ADR-010 يمنع حذف الحشو أصلًا. يُبلَّغ لقياس حجم
        # الفرصة، وليكون خطّ أساس إن فُتح باب الحذف.
        "filler_per_1000_words": (round(1000.0 * filler_hits / words, 1)
                                  if words else 0.0),
        "keyframes_available": len(keyframe_list),
        "keyframes_with_screen_text": sum(
            1 for k in keyframe_list if k.ocr_text.strip()),
    }

    return QualityReport(
        overall=overall,
        metrics=metrics,
        diagnostics=diagnostics,
        violations=tuple(violations),
        warnings=tuple(warnings),
    )


def assert_quality_gate(report: QualityReport,
                        minimum: Optional[float] = None) -> None:
    """يفشل على الخلل **البنيوي** وحده. الدرجة تُتابَع ولا تُفرَض بعد.

    ``minimum=None`` افتراضًا، وهو قرار مقصود: المقاييس الجديدة تقيس
    قابلية القراءة، والمخرج الحالي ضعيف فيها بحكم تصميمه — كل صورة
    تقطع فقرة، وكل عنوان ترتيبي. فرض عتبة اليوم يُسقط كل مهمة على جهاز
    كل معلّم، عقابًا على عيب لم يُصلَح بعد.

    العتبة تُضبط بعد أن نعرف خطّ الأساس، ثم تُرفع كلّما تحسّن المخرج —
    فتصير البوابة سقفًا للانحدار لا حكمًا تعسّفيًا.

    يرفع ``QualityGateError`` (وهي أيضًا ``AssertionError``) بدل
    ``AssertionError`` مجرّدة، ليشارك في نظام ``error_code``/``category``
    الذي يعتمده بقية التطبيق — انظر ``utils/error_reporting.py``.
    """
    if report.violations:
        raise QualityGateError(
            "فشل بوابة الجودة — خلل بنيوي: " + "؛ ".join(report.violations))
    if (minimum is not None and report.overall is not None
            and report.overall < minimum):
        raise QualityGateError(
            f"درجة قابلية القراءة {report.overall:.1f}% "
            f"أقل من الحدّ {minimum:.1f}%.")
