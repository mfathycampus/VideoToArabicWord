"""إعادة صياغة التفريغ إلى نص وثيقة احترافي.

المشكلة التي تعالجها: مخرج Whisper كلام منطوق — جُمل ناقصة، تكرار،
حشو («يعني»، «طيب»)، بلا فقرات ولا عناوين. طباعته كما هو تنتج نصًا
يصعب قراءته مهما كان القالب جميلًا.

ما تفعله هذه الوحدة: تقسّم النص إلى دفعات، وتطلب من نموذج لغوي إعادة
صياغته نثرًا مكتوبًا **دون إضافة معلومات**، وتستخرج عناوين أقسامًا
وملخّصًا.

الضمانات المفروضة برمجيًا:
    * النص الخام يبقى كما هو في ``transcription.json`` (ADR-005).
    * الناتج يُحفظ في ملف منفصل ``rewritten.json``.
    * كل قسم يحمل ``segment_ids`` المصدر — التتبّع ممكن دائمًا.
    * لا صورة تُفقد: الصور تُحقن بالزمن بعد إعادة الصياغة.
    * عند فشل النموذج في أي دفعة، تُستخدم الدفعة الخام بدلًا منها
      بدل إسقاطها.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from ai.providers import LLMProvider, RewriteUnavailableError
from config.schemas import (
    AudioSegment,
    DocumentBlock,
    DocumentPlan,
    DocumentSection,
    KeyframeMetadata,
    TranscriptionResult,
)
from utils.logger import logger
from utils.timestamps import seconds_to_display, timestamp_to_seconds


def _figure_caption(keyframe):
    """يعيد استعمال تعليق المُخطِّط الزمنيّ المبنيّ على نصّ الشاشة.

    الاستيراد داخل الدالّة لا في رأس الملفّ: ``document.planner`` يستورد
    ``document.titles`` الذي يستورد… والحلقة تقع عند الإقلاع لا عند
    الاستعمال.
    """
    from document.planner import _figure_caption as build

    return build(keyframe)

SYSTEM_PROMPT = """أنت محرّر عربي محترف. مهمتك تحويل تفريغ صوتي خام إلى نص وثيقة مكتوبة.

القواعد الملزمة:
1. لا تضف أي معلومة غير موجودة في النص الأصلي. لا تخترع أرقامًا ولا أسماء ولا نتائج.
2. لا تحذف أي معنى. احذف الحشو فقط: التكرار، «يعني»، «طيب»، «آه»، التلعثم.
3. حوّل الكلام المنطوق إلى نثر مكتوب سليم بعلامات ترقيم صحيحة.
4. اكتب بالعربية الفصحى المعاصرة، بأسلوب تقريري محايد.
5. قسّم إلى فقرات منطقية. الفقرة 3-6 جمل.
6. إن ورد مصطلح أجنبي، أبقِه كما هو.
7. لا تخاطب القارئ ولا تعلّق على المهمة.

التفريغ **آلي** ومن كلام قد يكون عاميًّا، وفيه أخطاء سمعية متوقَّعة
(كلمة تُسمع بكلمة قريبة اللفظ بعيدة المعنى: «تحضير» ← «تخدير»،
«المدارس» ← «المتاجر»). لذلك:
9. استعمل «السياق» المُعطى (عنوان التسجيل، نصوص الشاشة، المصطلحات)
   لفهم الموضوع. إن تعارضت كلمة في التفريغ معه تعارضًا واضحًا فهي على
   الأرجح خطأ سمعي: صحّحها إلى ما يدلّ عليه السياق.
10. ما لا يُفهم من التفريغ ولا يحسمه السياق: احذفه أو اكتب [غير واضح].
    لا تخمّن له معنى، ولا تبنِ عليه جملة أو فقرة أو عنوانًا.
11. إن كان الكلام شرحًا لخطوات على شاشة نظام أو برنامج، فاكتب الخطوات
    بترتيبها بصيغة إجرائية واضحة، وأبقِ أسماء الأزرار والقوائم كما تظهر.

8. إن أُعطيت أزمنة لقطات شاشة، اكتب لكل زمن تعليقًا وصفيًا (6-14 كلمة)
   مأخوذًا مما يقوله النص عند ذلك الوقت — لا تصف ما لا يذكره النص.
   وإن لم تُعطَ أزمنة، اجعل "figures" قائمة فارغة.

أعِد **JSON فقط** بهذا الشكل، بلا أي نص خارجه:
{"title": "عنوان هذا القسم في 3-7 كلمات — يصف ما يُشرح فيه تحديدًا، ولا يكرّر عنوان التسجيل",
 "summary": "جملة واحدة تلخّص القسم",
 "paragraphs": ["الفقرة الأولى", "الفقرة الثانية"],
 "figures": [{"t": "00:00:17", "caption": "تعليق وصفي قصير للقطة"}]}"""

OUTLINE_PROMPT = """أنت محرّر عربي محترف. أمامك عناوين أقسام وثيقة.

أعِد **JSON فقط**:
{"title": "عنوان الوثيقة كاملة في 3-8 كلمات",
 "abstract": "ملخّص تنفيذي في 2-4 جمل",
 "key_points": ["نقطة", "نقطة", "نقطة"]}

لا تضف معلومات غير موجودة. 3-6 نقاط رئيسية."""

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


#: عبارات تدلّ على أن النموذج لم يجد ما يعنونه. عنوانٌ كهذا على غلاف
#: مستندٍ يُسلَّم لمعلّم أسوأ من اسم الملفّ الخام بكثير.
_DEGENERATE_TITLE_HINTS = (
    "بدون محتوى", "بلا محتوى", "لا يوجد محتوى", "غير محدد", "غير واضح",
    "فارغ", "بدون عنوان", "بلا عنوان", "مقطع زمني", "no content",
    "untitled", "unknown",
)


def _is_degenerate_title(title: str) -> bool:
    folded = " ".join(title.split()).lower()
    return any(hint in folded for hint in _DEGENERATE_TITLE_HINTS)


@dataclass
class RewriteConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    # حجم الدفعة بالحروف — يوازن بين سياق كافٍ وحدود النموذج
    batch_chars: int = 3500
    make_outline: bool = True
    timeout_seconds: int = 180
    #: مصطلحات المادة — تُمرَّر سياقًا يساعد على تصحيح الأخطاء السمعية.
    glossary: str = ""
    #: يُرسل نصّ الشاشة (OCR) سياقًا مع كل دفعة. أقوى دليل على موضوع
    #: المحاضرة، لكنه قد يحمل أسماء أشخاص ظاهرة على الشاشة — فيُعطَّل
    #: حيث تمنع السياسة إرسالها إلى مزوّد سحابي.
    include_screen_text: bool = True


def _extract_json(raw: str) -> Optional[dict]:
    """يستخرج JSON من رد النموذج حتى لو أحاطه بنص أو أسوار شيفرة."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


class TranscriptRewriter:
    # إعادة المحاولة على الأخطاء العابرة فقط (تحديد معدّل، عطل خدمة،
    # انقطاع شبكة). التراجع الأسّي يمنع إغراق خدمة تشتكي أصلًا.
    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = 3.0

    def __init__(self, provider: LLMProvider, config: RewriteConfig) -> None:
        self.provider = provider
        self.config = config
        #: سبب آخر فشل دفعة — يُرفَع مع الاستثناء النهائي ليصل للمستخدم.
        self._last_failure: str = ""
        #: عنوان التسجيل (من اسم الملفّ) — يُضبط في ``build_plan``.
        self._source_title: str = ""

    def _complete_with_retry(self, system_prompt: str, user_prompt: str,
                             max_tokens: int = 2048) -> str:
        """نداء المزوّد مع إعادة محاولة للأخطاء العابرة.

        المنطق نفسه انتقل إلى ``ai.providers.complete_with_retry`` حين
        احتاجته الحزمة التعليمية أيضًا. الغلاف باقٍ هنا لأن ``MAX_ATTEMPTS``
        و``BACKOFF_SECONDS`` صفتا صنفٍ تستبدلهما الاختبارات.
        """
        from ai import providers

        attempts, backoff = providers.MAX_ATTEMPTS, providers.BACKOFF_SECONDS
        providers.MAX_ATTEMPTS = self.MAX_ATTEMPTS
        providers.BACKOFF_SECONDS = self.BACKOFF_SECONDS
        try:
            return providers.complete_with_retry(
                self.provider, system_prompt, user_prompt,
                max_tokens=max_tokens, timeout=self.config.timeout_seconds)
        finally:
            providers.MAX_ATTEMPTS, providers.BACKOFF_SECONDS = attempts, backoff

    # ------------------------------------------------------------------
    # عدد الأقسام المستهدف — فهرس مفيد بلا تفتيت
    TARGET_SECTIONS = 5
    MIN_BATCH_CHARS = 700
    #: فوق هذه المدّة يُعدّ التسجيل «محاضرة» تستحقّ فهرسًا مهما شحّ نصّها.
    SPARSE_SPAN_SECONDS = 180.0

    def _effective_batch_chars(self, segments: List[AudioSegment]) -> int:
        """حجم الدفعة مشتقّ من طول النص.

        قيمة ثابتة (3500 حرفًا) تبتلع تفريغ فيديو قصير كاملًا في دفعة
        واحدة، فيخرج المستند بقسم وحيد عنوانه هو عنوان الوثيقة نفسه —
        بلا بنية ولا فهرس مفيد.
        """
        total = sum(len((s.text_clean or s.text_raw).strip()) for s in segments)
        if total <= 0:
            return self.config.batch_chars
        # الإعداد المُصرّح به يبقى سقفًا دائمًا (تحكّم المستخدم في الكلفة)،
        # والاشتقاق التلقائي يخفضه فقط — ولا ينزل تحت الحد الأدنى المفيد.
        # الحدّ الأدنى يُخفَّض لتفريغٍ **شحيحٍ على مدى طويل** وحده.
        #
        # رُصد على تسجيل حقيقي مدّته 5:12 خرج بـ597 حرفًا فقط (كلامٌ
        # متقطّع وصوتٌ هادئ): 597 < 700 فصار دفعةً واحدة — أي **قسمًا
        # وحيدًا لمحاضرة كاملة**، بينما المُخطِّط الزمنيّ على البيانات
        # نفسها يُخرج خمسة أقسام.
        #
        # والشرط على **المدّة** لا على عدد الحروف وحده، وهذا هو الفرق
        # الصحيح: مقطعٌ من ثلاثين ثانية فيه ثلاثمئة حرف قسمٌ واحد بحقّ،
        # ومحاضرةٌ من خمس دقائق فيه ثلاثمئة حرف مقرّرٌ متقطّع يستحقّ
        # فهرسًا. الحدّ الأدنى وُضع ليمنع دفعات تافهة لا ليبتلع محاضرة.
        span = 0.0
        if segments:
            span = max(0.0, segments[-1].end - segments[0].start)
        floor = float(self.MIN_BATCH_CHARS)
        if span >= self.SPARSE_SPAN_SECONDS and total < self.MIN_BATCH_CHARS * 2:
            floor = min(floor, max(150.0, total / 3.0))
        target = max(floor, total / self.TARGET_SECTIONS)
        return int(min(self.config.batch_chars, target))

    def _batch(self, segments: List[AudioSegment]) -> List[List[AudioSegment]]:
        batches: List[List[AudioSegment]] = []
        current: List[AudioSegment] = []
        size = 0
        limit = self._effective_batch_chars(segments)
        for segment in segments:
            text = (segment.text_clean or segment.text_raw).strip()
            if not text:
                continue
            if current and size + len(text) > limit:
                batches.append(current)
                current, size = [], 0
            current.append(segment)
            size += len(text)
        if current:
            batches.append(current)
        return batches

    def _rewrite_batch(self, batch: List[AudioSegment],
                       figures: Optional[List[KeyframeMetadata]] = None) -> dict:
        source = " ".join((s.text_clean or s.text_raw).strip() for s in batch)
        start = seconds_to_display(batch[0].start)
        figures = figures or []
        # التعليق الوصفي يُطلب مع النص نفسه: النموذج يرى الكلام المصاحب
        # للحظة اللقطة، فيكتب تسمية تشرحها بدل «لقطة عند 00:00:17».
        note = ""
        if figures:
            stamps = "، ".join(seconds_to_display(f.timestamp) for f in figures)
            note = ("\n\nلقطات الشاشة المرافقة لهذا المقطع عند الأزمنة التالية: "
                    f"{stamps}\nاكتب تعليقًا وصفيًا لكل زمن في حقل figures، "
                    "بنفس صيغة الزمن المعطاة.")
        context = self._context_block(figures)
        failed = False
        try:
            raw = self._complete_with_retry(
                SYSTEM_PROMPT,
                f"{context}التفريغ الخام (يبدأ عند {start}):\n\n{source}{note}")
            parsed = _extract_json(raw)
        except Exception as exc:
            # الفشل معزول عند حدود الدفعة. النسخة السابقة كانت تُعيد رفع
            # ``RewriteUnavailableError`` فتنتشر إلى الـ pipeline: تحديدُ
            # معدّل عند الدفعة الثانية عشرة كان يرمي إحدى عشرة دفعة
            # مدفوعة الثمن ويُخرج مستندًا خامًا بالكامل.
            logger.warning(f"فشل إعادة صياغة دفعة {start}: {exc}")
            parsed = None
            failed = True
            # السبب يُحفظ ليصل إلى المستخدم: تحذيرٌ يقول «تعذّرت» بلا
            # سبب لا يُمكّنه من فعل شيء — مفتاح؟ شبكة؟ نموذج؟
            self._last_failure = f"{type(exc).__name__}: {exc}"

        paragraphs = []
        if parsed:
            paragraphs = [p.strip() for p in parsed.get("paragraphs", [])
                          if isinstance(p, str) and p.strip()]

        if not paragraphs and parsed is not None and not failed:
            # ردٌّ سليم بلا فقرات: رُصد على أوّل دفعة من تسجيلٍ عامّيّ مشوَّه
            # — النموذج أحجم عن الكتابة كليًّا بعد تعليمة «لا تخمّن»، فخرج
            # القسم الأول خامًا بـ«المتاجر». محاولةٌ ثانية تطلب المفهوم وحده.
            try:
                raw = self._complete_with_retry(
                    SYSTEM_PROMPT,
                    f"{context}التفريغ الخام (يبدأ عند {start}):\n\n{source}{note}"
                    "\n\nتنبيه: أعِد فقرةً واحدة على الأقل تلخّص ما يُفهم من هذا "
                    "المقطع بمساعدة السياق، ولو جملة قصيرة، وضع [غير واضح] "
                    "مكان ما لا يُفهم. لا تُعِد paragraphs فارغة.")
                retry = _extract_json(raw)
                if retry:
                    parsed = retry
                    paragraphs = [p.strip() for p in retry.get("paragraphs", [])
                                  if isinstance(p, str) and p.strip()]
            except Exception as exc:
                logger.warning(f"دفعة {start}: فشلت المحاولة الثانية: {exc}")

        if not paragraphs:
            # لا نُسقط المحتوى أبدًا: نرجع إلى النص الخام لهذه الدفعة.
            #
            # **و``failed`` تُرفع هنا أيضًا** — وهذا إصلاح عطلٍ رُصد على
            # مخرج حقيقي: كانت تُرفع عند الاستثناء وحده، فردٌّ سليم
            # الشكل وفارغ المحتوى (``{}`` أو ``paragraphs: []``) يُعدّ
            # نجاحًا. وبدفعةٍ واحدة فاشلة بهذا الشكل كان حارس «فشلت كل
            # الدفعات» لا يُطلق أبدًا، فيخرج المستند خامًا بالكامل
            # موسومًا ``ai:`` — ويُطبع على غلافه «صياغة النص: ai:…».
            logger.warning(
                f"دفعة {start}: ردٌّ بلا فقرات — استُخدم النص الخام.")
            paragraphs = [source]
            parsed = parsed or {}
            failed = True
            self._last_failure = self._last_failure or "ردٌّ بلا فقرات قابلة للاستعمال"

        captions: dict[str, str] = {}
        for item in (parsed.get("figures") or []) if parsed else []:
            if not isinstance(item, dict):
                continue
            stamp = str(item.get("t", "")).strip()
            text = str(item.get("caption", "")).strip()
            if not (stamp and text):
                continue
            try:                       # نطبّع الزمن: قد يعيده النموذج MM:SS
                key = seconds_to_display(timestamp_to_seconds(stamp))
            except (ValueError, IndexError):
                continue
            captions[key] = text

        return {
            "failed": failed,
            "captions": captions,
            "title": (parsed.get("title") or "").strip()
                     or f"المقطع الزمني {start}",
            "summary": (parsed.get("summary") or "").strip(),
            "paragraphs": paragraphs,
            "segment_ids": [s.id for s in batch],
            "start": batch[0].start,
            "end": batch[-1].end,
            # مرجع زمني لتوزيع الفقرات: الصياغة تدمج عدة مقاطع في فقرة،
            # فتفقد الفقرة توقيتها. نحتفظ بتوقيت المقاطع الأصلية لنعيد
            # اشتقاق توقيت كل فقرة لاحقًا.
            "spans": [(s.start, s.end,
                       len((s.text_clean or s.text_raw).strip()))
                      for s in batch],
        }

    @staticmethod
    def _paragraph_times(entry: dict) -> List[float]:
        """يقدّر زمن بداية كل فقرة مُعاد صياغتها.

        الصياغة تعيد فقرات بلا توقيت. توزيعها على مدى الدفعة **بنسبة
        طول كل فقرة** يعطي كل واحدة زمنًا تقريبيًا، فتتداخل مع الصور
        بترتيب زمني صحيح.

        بدون هذا، تُدرَج كل الصور أولًا ثم كل النص — فيظهر المستند
        بصور متتالية بلا شرح، والشرح كله في آخر القسم.
        """
        paragraphs = entry["paragraphs"]
        spans = entry.get("spans") or []
        if not paragraphs:
            return []
        if not spans:
            return [entry["start"]] * len(paragraphs)

        # خريطة: حرف تراكمي في المصدر → زمن بدايته
        total_source = sum(length for _, _, length in spans) or 1
        lengths = [max(1, len(p)) for p in paragraphs]
        total_output = sum(lengths)

        times: List[float] = []
        consumed = 0
        for length in lengths:
            # موضع بداية هذه الفقرة كنسبة من المخرج، مسقطة على المصدر
            target = (consumed / total_output) * total_source
            walked = 0.0
            chosen = spans[0][0]
            for start, end, span_length in spans:
                if walked + span_length >= target:
                    # استيفاء خطّي داخل المقطع: دفعة من مقطع واحد كانت
                    # تمنح كل فقراتها نفس الزمن، فتتكدّس الصور بعدها.
                    ratio = ((target - walked) / span_length
                             if span_length else 0.0)
                    chosen = start + max(0.0, min(1.0, ratio)) * (end - start)
                    break
                walked += span_length
                chosen = end
            times.append(chosen)
            consumed += length
        return times

    @staticmethod
    def _partition_figures(starts: List[float],
                           keyframes: List[KeyframeMetadata],
                           ) -> List[List[KeyframeMetadata]]:
        """توزيع الصور على الدفعات — مرجع واحد يستخدمه الطلب والتجميع معًا.

        الصور السابقة لأول قسم تلحق به، واللاحقة لآخر قسم تلحق بآخره.
        """
        groups: List[List[KeyframeMetadata]] = [[] for _ in starts]
        if not starts:
            return groups
        for keyframe in keyframes:
            index = 0
            for position, start in enumerate(starts):
                if keyframe.timestamp >= start:
                    index = position
            groups[index].append(keyframe)
        return groups

    def _build_outline(self, sections: List[dict], fallback_title: str) -> dict:
        if not self.config.make_outline:
            return {"title": fallback_title, "abstract": "", "key_points": []}
        listing = "\n".join(
            f"- {s['title']}: {s['summary']}" for s in sections)
        try:
            head = (f"عنوان التسجيل: {self._source_title}\n"
                    if self._source_title else "")
            raw = self._complete_with_retry(
                OUTLINE_PROMPT, f"{head}أقسام الوثيقة:\n{listing}", max_tokens=800)
            parsed = _extract_json(raw) or {}
        except Exception as exc:
            # الملخّص التنفيذي تحسين لا شرط: فشله لا يُفشل الصياغة كلها
            logger.warning(f"تعذّر بناء الملخّص التنفيذي: {exc}")
            parsed = {}

        # عنوانٌ يصف **عجز النموذج** لا محتوى المحاضرة يُرفض. رُصد
        # حرفيًّا على مخرج حقيقي: خرج المستند بعنوان «مقطع زمني بدون
        # محتوى» مطبوعًا على غلافه وفي رأس كل صفحة.
        title = (parsed.get("title") or "").strip()
        if title and _is_degenerate_title(title):
            logger.warning(
                f"عنوانٌ يصف الفراغ لا المحتوى («{title}») — "
                f"استُعمل اسم الملفّ بدلًا منه.")
            title = ""
        return {
            "title": title or fallback_title,
            "abstract": (parsed.get("abstract") or "").strip(),
            "key_points": [p.strip() for p in parsed.get("key_points", [])
                           if isinstance(p, str) and p.strip()][:6],
        }

    #: سقف نصّ الشاشة المُرسَل مع الدفعة الواحدة — سياقٌ لا حمولة.
    SCREEN_CONTEXT_CHARS = 700

    def _context_block(self, figures: List[KeyframeMetadata]) -> str:
        """سياق يحسم الأخطاء السمعية: العنوان، ونصّ الشاشة، والمصطلحات.

        رُصد على تسجيل حقيقي («متابعة تحضير المعلمين»): النموذج لم يرَ
        إلا التفريغ المشوَّه، فكتب «لوحة إدارة المتاجر» و«المعلمات
        الإحصائية كالميدل والمتوسط» — بينما نصّ الشاشة المحفوظ يقول
        «High School» و«Lesson Planner» و«Math Education».
        """
        lines: List[str] = []
        if self._source_title:
            lines.append(f"- عنوان التسجيل: {self._source_title}")
        if self.config.include_screen_text:
            seen: set[str] = set()
            screen: List[str] = []
            for figure in figures:
                for line in (figure.ocr_text or "").splitlines():
                    line = " ".join(line.split())
                    if len(line) >= 4 and line.lower() not in seen:
                        seen.add(line.lower())
                        screen.append(line)
            text = " | ".join(screen)[:self.SCREEN_CONTEXT_CHARS]
            if text:
                lines.append(f"- نصوص ظاهرة على الشاشة (قراءة آلية قد تحوي أخطاء): {text}")
        glossary = " ".join((self.config.glossary or "").split())
        if glossary:
            lines.append(f"- مصطلحات محتملة: {glossary[:400]}")
        if not lines:
            return ""
        return "السياق:\n" + "\n".join(lines) + "\n\n"

    # ------------------------------------------------------------------
    def build_plan(
        self,
        transcript: TranscriptionResult,
        keyframes: List[KeyframeMetadata],
        fallback_title: str,
        subtitle: str = "",
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> DocumentPlan:
        self._source_title = fallback_title
        batches = self._batch(transcript.segments)
        if not batches:
            raise RewriteUnavailableError("لا يوجد نص لإعادة صياغته.")

        groups = self._partition_figures([b[0].start for b in batches],
                                         keyframes)
        rewritten: List[dict] = []
        for index, batch in enumerate(batches, start=1):
            rewritten.append(self._rewrite_batch(batch, groups[index - 1]))
            if progress_callback:
                progress_callback(
                    index / (len(batches) + 1),
                    f"إعادة الصياغة: القسم {index} من {len(batches)}")

        failures = sum(1 for entry in rewritten if entry.get("failed"))
        if failures == len(rewritten):
            # لا فائدة من مستند يحمل وسم «ai:» ومحتواه خام بالكامل
            reason = self._last_failure or "لم يُعد النموذج نصًّا قابلًا للاستعمال"
            raise RewriteUnavailableError(
                f"فشلت كل دفعات إعادة الصياغة ({reason}) — "
                "المتابعة بالنص الخام.")
        if failures:
            logger.warning(
                f"{failures} من {len(rewritten)} دفعة استُخدم نصها الخام"
                + (f" ({self._last_failure})" if self._last_failure else "."))

        outline = self._build_outline(rewritten, fallback_title)
        if progress_callback:
            progress_callback(1.0, "اكتملت إعادة الصياغة")

        sections = self._assemble(rewritten, keyframes)
        plan = DocumentPlan(
            title=outline["title"],
            subtitle=subtitle,
            abstract=outline["abstract"],
            key_points=outline["key_points"],
            sections=sections,
            generated_by=(f"ai:{self.provider.info.name}/"
                          f"{getattr(self.provider, 'model', '') or 'default'}"),
            total_segments_in=len(transcript.segments),
            total_figures_in=len(keyframes),
        )
        self._count_outputs(plan)
        return plan

    # ------------------------------------------------------------------
    @staticmethod
    def _assemble(rewritten: List[dict],
                  keyframes: List[KeyframeMetadata]) -> List[DocumentSection]:
        """يبني الأقسام ويحقن الصور في مواضعها الزمنية.

        كل صورة تدخل قسمًا واحدًا بالضبط: القسم الذي يحوي زمنها، وإلا
        فأقرب قسم سابق. الصور المتأخرة عن آخر قسم تلحق به.
        """
        sections: List[DocumentSection] = []
        boundaries: List[float] = [entry["start"] for entry in rewritten]
        groups = TranscriptRewriter._partition_figures(boundaries, keyframes)

        for index, entry in enumerate(rewritten):
            section = DocumentSection(
                title=entry["title"], level=1, summary=entry["summary"],
                start_timestamp=entry["start"], blocks=[])
            # تُجمع الصور والفقرات في قائمة واحدة ثم تُرتَّب زمنيًا، بدل
            # إضافة كل الصور ثم كل النص. الترتيب الثانوي يقدّم الصورة على
            # النص عند تساوي الزمن: المتحدث يعرض الشاشة ثم يشرحها.
            events: List[tuple[float, int, DocumentBlock]] = []

            captions = entry.get("captions") or {}
            for keyframe in groups[index]:
                stamp = seconds_to_display(keyframe.timestamp)
                events.append((keyframe.timestamp, 0, DocumentBlock(
                    kind="figure",
                    timestamp=keyframe.timestamp,
                    image_id=keyframe.image_id,
                    image_filename=keyframe.filename,
                    # تعليقٌ من النموذج إن وُجد، وإلا فمن نصّ الشاشة —
                    # لا فراغ. رُصد على مخرج حقيقي: أربع صور بتعليق
                    # فارغ (‏figure_captioning = 0٪) بينما نصّ الشاشة
                    # لكلٍّ منها محفوظ وبين 34 و229 حرفًا. النموذج
                    # صمت، فسقط المستند إلى لا شيء بدل السقوط إلى
                    # البيانات التي في يده.
                    caption=(captions.get(stamp)
                             or _figure_caption(keyframe)),
                    ocr_text=keyframe.ocr_text)))

            times = TranscriptRewriter._paragraph_times(entry)
            for position, paragraph in enumerate(entry["paragraphs"]):
                events.append((times[position], 1, DocumentBlock(
                    kind="paragraph", text=paragraph,
                    timestamp=times[position],
                    # معرّفات المصدر تُسجَّل على الفقرة الأولى فقط حتى لا
                    # تتكرر، مع بقاء التتبّع ممكنًا للقسم كله
                    segment_ids=entry["segment_ids"] if position == 0 else [])))

            events.sort(key=lambda e: (e[0], e[1]))
            section.blocks = [block for _, _, block in events]
            sections.append(section)
        return sections

    @staticmethod
    def _count_outputs(plan: DocumentPlan) -> None:
        segments, figures = set(), set()
        for section in plan.sections:
            for block in section.blocks:
                segments.update(block.segment_ids)
                if block.image_id is not None:
                    figures.add(block.image_id)
        plan.total_segments_out = len(segments)
        plan.total_figures_out = len(figures)
