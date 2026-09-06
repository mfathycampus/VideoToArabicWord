"""بناء خطة المستند من الخط الزمني — بلا ذكاء اصطناعي.

هذا هو المسار الافتراضي: بنية معقولة تُشتق من الزمن والصور وحدها. وحدة
إعادة الصياغة (اختيارية) تنتج خطة أغنى بنفس الشكل، فيصيّرهما المولّد
بالطريقة نفسها.

ضمانة أساسية محفوظة من ADR-010: كل ``AudioSegment`` يظهر **مرة واحدة
بالضبط**، وكل صورة كذلك. يُفحص في ``assert_lossless``.
"""
from __future__ import annotations

import re
from typing import List, Optional

from config.schemas import (
    DocumentBlock,
    DocumentPlan,
    DocumentSection,
    KeyframeMetadata,
    TranscriptionResult,
)
from document.titles import clean_title_candidate
from utils.timestamps import seconds_to_display

# نهايات جُمل عربية — لتجميع المقاطع في فقرات مقروءة
# نهاية جملة عربية أو لاتينية. النسخة السابقة كتبت ``\\s`` داخل سلسلة
# خام، أي شرطة مائلة حرفية متبوعة بحرف s — فكان أحد بديلَي التعبير ميتًا.
_SENTENCE_END = re.compile(r"[.!?؟…]\s*$")

_ORDINALS = ("الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس",
             "السابع", "الثامن", "التاسع", "العاشر", "الحادي عشر",
             "الثاني عشر")


def _gaps_after(transcript: TranscriptionResult) -> dict:
    """‏{معرّف المقطع: طول السكتة التي تليه}. الأخير سكتته لا نهائية."""
    segments = transcript.segments
    gaps = {}
    for i, segment in enumerate(segments):
        if i + 1 < len(segments):
            gaps[segment.id] = max(0.0, segments[i + 1].start - segment.end)
        else:
            gaps[segment.id] = float("inf")
    return gaps


def _buffer_chars(buffer: List) -> int:
    return sum(len(s.text_clean or s.text_raw) for s in buffer)


def _is_break_point(segment, gap_after: dict) -> bool:
    """هل يصلح هذا المقطع نهايةً لفقرة؟ ترقيمٌ أو سكتة."""
    text = (segment.text_clean or segment.text_raw).strip()
    if _SENTENCE_END.search(text):
        return True
    return gap_after.get(segment.id, 0.0) >= PAUSE_BREAK_SECONDS


def _ordinal_title(index: int, timestamp: float) -> str:
    """عنوان قسم مقروء بدل طابع زمني خام.

    «المقطع الزمني 00:00:00» ليس عنوانًا: لا يقول شيئًا عن المحتوى ويجعل
    الفهرس بلا فائدة. الترتيب مع نقطة البداية أوضح، وإعادة الصياغة
    بالذكاء الاصطناعي تستبدله بعنوان حقيقي من المضمون.
    """
    name = _ORDINALS[index - 1] if index <= len(_ORDINALS) else str(index)
    return f"القسم {name} — من {seconds_to_display(timestamp)}"


#: سكتةٌ بهذا الطول تُعدّ نهاية جملة ولو خلا النصّ من نقطة.
#:
#: **لماذا نحتاجها أصلًا:** نموذج التفريغ لا يكاد يضع ترقيمًا على
#: العربية. قياس على 8.5 دقائق حقيقية: 766 كلمة فيها **13 علامة نهاية
#: جملة فقط** — واحدة كل تسع وخمسين كلمة — و8 مقاطع من 97 تنتهي
#: بعلامة. والمُخطِّط يحتاج نحو عشرين نقطة قطع (فقرات + مواضع صور)،
#: فكان يقطع في منتصف الكلام لأن لا خيار له.
#:
#: **ولماذا السكتة صالحة بديلًا:** توزيع الفجوات في التسجيل نفسه
#: ثنائيّ القمّة — الوسيط صفر (المقاطع ملتصقة) ثم 29 فجوة تتجاوز
#: الثانية و22 تتجاوز الثانية والنصف. ليست ضوضاء قياس: هي سكتات
#: المتحدّث الفعلية بين الأفكار. القطع عندها يقرأ طبيعيًّا.
#:
#: القيمة **مقيسة على تسجيل واحد**؛ تسجيل بطيء الإلقاء قد يحتاج أكبر.
PAUSE_BREAK_SECONDS = 1.0

#: أدنى طول فقرة بالحروف. السكتة وحدها لا تكفي للقطع: المتحدّث يسكت
#: بين الجملة والجملة، فالقطع عند كل سكتة يفتّت المستند إلى شظايا.
#: قياس على التسجيل نفسه: القطع عند كل سكتة أعطى 13 فقرة **خمسٌ منها
#: دون هذا الحدّ**، فهبط ``paragraph_balance`` من 85.7٪ إلى 61.5٪.
#: يشترك فيه المُخطِّط والمقياس كي لا يُعاقَب أحدهما بحدٍّ لا يعرفه.
MIN_PARAGRAPH_CHARS = 180

#: كم يُسمح للقسم أن يتجاوز طوله المقرّر انتظارًا لنهاية جملة.
#: بلا سقف، يبتلع نصٌّ بلا علامة ترقيم — وهو وارد جدًّا من نموذج
#: التفريغ — المستندَ كلّه في قسم واحد.
_SECTION_OVERRUN = 1.25

#: كم كتلة نفحص قبل التسليم. القسم الأول قد يفتتحه المحاضر بتحية
#: وبسملة، فالاكتفاء بالكتلة الأولى يفقد العنوان. والتوغّل بعيدًا
#: يأتي بعنوان من وسط القسم لا من مطلعه.
_TITLE_SEARCH_BLOCKS = 3


def _title_from_screen(section: DocumentSection) -> str:
    """أول سطر شاشة يصلح عنوانًا — من أوائل صور القسم."""
    seen = 0
    for block in section.blocks:
        if block.kind != "figure" or not block.ocr_text.strip():
            continue
        seen += 1
        if seen > _TITLE_SEARCH_BLOCKS:
            break
        for line in block.ocr_text.splitlines():
            candidate = clean_title_candidate(line)
            if candidate:
                return candidate
    return ""


def _section_title(index: int, section: DocumentSection,
                   screen_text_titles: bool = True) -> str:
    """عنوان من نصّ الشاشة، وإلا فالترتيبي.

    كل مستند ينتجه البرنامج كانت عناوينه «القسم الأول — من 00:00:00»:
    تملأ خانة العنوان ولا تقول شيئًا، فالفهرس المبني عليها بلا وظيفة.

    **جرّبتُ مصدرين وأسقطتُ أحدهما، وهذا سبب الإسقاط.**

    *رواية المحاضر*: تبدو المصدر الطبيعي — هي تصف ما يُشرح. وعلى تسجيل
    حقيقي أعطت عناوين مثل «هنتعرف على كيف نخطط وكيف ننزل واجبات»، وهي
    وصفٌ صادق للمحاضرة. لكن العنوان حينئذٍ **منقولٌ حرفيًا من أول
    الفقرة التي تليه مباشرةً**: يقرأ المعلّم الجملة عنوانًا ثم يقرأها
    ثانيةً نصًّا. تلعثمٌ مرئي، ومضاعفةٌ لنصّ التفريغ في المستند —
    كشفها اختبار المصفوفة الذي يفرض ظهور كل جملة **مرّة واحدة**. عنوانٌ
    غير منقول يحتاج صياغةً لا اقتطاعًا، وذلك عمل وحدة إعادة الصياغة لا
    هذا المسار. فأُسقطت الرواية.

    *نصّ الشاشة*: لا يضاعف شيئًا — الـOCR ليس في متن المستند. وعلى
    الشرائح أعلى الشريحة عنوانها حرفيًا. لكنه على تسجيل شاشة يقرأ شريط
    تبويبات المتصفّح وأسماء الإضافات: «Bi Lessons | Chalk - Googke
    Chrome»، «Cy analte Reverso |Free tans». ولذلك يُطفَأ في ملفّ شرح
    البرنامج عبر ``screen_text_titles``.

    فيبقى الترتيبي حيث لا مصدر — وهو ما يقوله المقياس صراحةً الآن بدل
    أن يرفع 100٪ على عناوين مضلِّلة. عنوانٌ ضعيف خير من عنوان مضلِّل.
    """
    if screen_text_titles:
        candidate = _title_from_screen(section)
        if candidate:
            return candidate
    return _ordinal_title(index, section.start_timestamp or 0.0)


def _figure_caption(payload) -> str:
    """تعليق يصف الصورة، وإلا فطابعها الزمني.

    «لقطة عند 00:12:30» يشغل مكان التعليق ولا يقول عن الصورة شيئًا: على
    محاضرة بستين شريحة يعني ستين سطرًا تُحذف يدويًا. ونصّ الشاشة —
    حين يكون OCR مُفعَّلًا — موجود وقت بناء الخطة ولا يُستعمل.
    """
    stamp = seconds_to_display(payload.timestamp)
    for line in (payload.ocr_text or "").splitlines():
        candidate = clean_title_candidate(line)
        if candidate:
            return f"{candidate} — {stamp}"
    return f"لقطة عند {stamp}"


class TimelinePlanner:
    """يبني خطة مستند من التفريغ والصور.

    ``section_minutes``: طول القسم الزمني. الافتراضي 5 دقائق يعطي مستندًا
    قابلًا للتصفّح بفهرس مفيد بدل جدار نصّي واحد.
    """

    # عدد الأقسام المستهدف: فهرس مفيد بلا تفتيت
    TARGET_SECTIONS = 6
    MIN_SECTION_SECONDS = 40.0
    MAX_SECTION_SECONDS = 600.0

    def __init__(self, section_minutes: float = 5.0,
                 paragraph_max_chars: int = 700,
                 adaptive_sections: bool = True,
                 screen_text_titles: bool = True) -> None:
        # نصّ الشاشة عنوانٌ ممتاز على شريحة، وزينةُ متصفّح على تسجيل
        # شاشة. يضبطه ملفّ المحتوى — انظر ``_section_title``.
        self.screen_text_titles = screen_text_titles
        self.configured_section_seconds = max(30.0, section_minutes * 60.0)
        self.paragraph_max_chars = paragraph_max_chars
        self.adaptive_sections = adaptive_sections

    def _section_seconds(self, duration: float) -> float:
        """طول القسم مشتقّ من طول الفيديو.

        قيمة ثابتة (5 دقائق) تعطي قسمًا واحدًا لفيديو 90 ثانية — أي بلا
        بنية ولا فهرس — وعشرات الأقسام لمحاضرة ثلاث ساعات. الاشتقاق من
        المدة يبقي عدد الأقسام في نطاق مفيد دائمًا.
        """
        if not self.adaptive_sections or duration <= 0:
            return self.configured_section_seconds
        target = duration / self.TARGET_SECTIONS
        return max(self.MIN_SECTION_SECONDS,
                   min(self.MAX_SECTION_SECONDS, target))

    # ------------------------------------------------------------------
    def build(
        self,
        transcript: TranscriptionResult,
        keyframes: List[KeyframeMetadata],
        title: str,
        subtitle: str = "",
    ) -> DocumentPlan:
        events = self._merge_timeline(transcript, keyframes)
        duration = max((e[0] for e in events), default=0.0)
        sections = self._split_into_sections(
            events, self._section_seconds(duration),
            _gaps_after(transcript))

        plan = DocumentPlan(
            title=title,
            subtitle=subtitle,
            sections=sections,
            generated_by="timeline",
            total_segments_in=len(transcript.segments),
            total_figures_in=len(keyframes),
        )
        self._count_outputs(plan)
        return plan

    # ------------------------------------------------------------------
    def _merge_timeline(self, transcript, keyframes) -> List[tuple]:
        """يدمج النص والصور في تسلسل زمني واحد.

        عند تساوي الزمن تُقدَّم الصورة على النص: المتحدث يعرض الشاشة ثم
        يشرحها.
        """
        events: List[tuple] = []
        for kf in keyframes:
            events.append((kf.timestamp, 0, "figure", kf))
        for segment in transcript.segments:
            events.append((segment.start, 1, "text", segment))
        events.sort(key=lambda e: (e[0], e[1]))
        return events

    def _flush_paragraph(self, buffer: List, blocks: List[DocumentBlock]) -> None:
        """يحوّل مقاطع متراكمة إلى فقرة واحدة مقروءة.

        مقاطع Whisper قصيرة (3–10 ثوانٍ). عرض كل مقطع كفقرة مستقلة ينتج
        مستندًا مفتّتًا يصعب قراءته.
        """
        if not buffer:
            return
        text = " ".join((s.text_clean or s.text_raw).strip() for s in buffer).strip()
        if text:
            blocks.append(DocumentBlock(
                kind="paragraph", text=text,
                timestamp=buffer[0].start,
                source_start=buffer[0].start,
                source_end=buffer[-1].end,
                segment_ids=[s.id for s in buffer]))
        buffer.clear()

    def _split_into_sections(self, events: List[tuple],
                             section_seconds: float,
                             gap_after: Optional[dict] = None,
                             ) -> List[DocumentSection]:
        sections: List[DocumentSection] = []
        current: Optional[DocumentSection] = None
        buffer: List = []
        pending_figures: List = []
        section_start = 0.0
        gap_after = gap_after or {}

        for timestamp, _, kind, payload in events:
            # حدّ القسم ينتظر نهاية الجملة كما تنتظرها الصورة.
            #
            # كان الحدّ يقع على الساعة بالضبط، فيُغلق القسم والجملة في
            # منتصفها: على تسجيل حقيقي كانت **6 فقرات من 8** مقطوعة،
            # وأغلبها عند حدود الأقسام لا عند الصور. القسم ليس وحدة
            # زمنية مقدّسة — طوله تقدير، ونهاية الجملة حقيقة في النصّ.
            due = timestamp - section_start >= section_seconds
            overrun = timestamp - section_start >= (section_seconds
                                                    * _SECTION_OVERRUN)
            if current is None or (
                    due and (self._buffer_ends_a_sentence(buffer, gap_after)
                             or overrun)):
                if current is not None:
                    self._flush_paragraph(buffer, current.blocks)
                    self._flush_figures(pending_figures, current.blocks)
                section_start = timestamp
                current = DocumentSection(
                    title=_ordinal_title(len(sections) + 1, timestamp),
                    level=1, start_timestamp=timestamp, blocks=[])
                sections.append(current)

            if kind == "figure":
                # الصورة تنتظر نهاية الجملة بدل أن تقطعها.
                #
                # كان كل شكل يُفرغ الفقرة بلا شرط، فيقطع الجملة الجارية
                # أيًّا كان موضعه منها. قياس على تسجيل حقيقي: **30 فقرة
                # من 31 مقطوعة في منتصف الجملة**. والصورة لا تخسر شيئًا
                # بالانتظار: طابعها الزمني محفوظ في الكتلة وفي التعليق،
                # وكل ما يتغيّر موضعها في التدفّق بمقدار جملة واحدة.
                # ولا تخرج قبل أن تكتمل فقرة: السكتة تأتي بين الجملة
                # والجملة، فالإفراغ عند أول سكتة يترك شظايا من ثلاثة
                # أسطر بين كل صورتين. قياس: القطع بلا هذا الشرط أعطى 13
                # فقرة **خمسٌ منها دون 180 حرفًا**.
                pending_figures.append(payload)
                ready = (not buffer
                         or _buffer_chars(buffer) >= MIN_PARAGRAPH_CHARS)
                if ready and self._buffer_ends_a_sentence(buffer, gap_after):
                    self._flush_paragraph(buffer, current.blocks)
                    self._flush_figures(pending_figures, current.blocks)
            else:
                buffer.append(payload)
                joined = sum(len(s.text_clean or s.text_raw) for s in buffer)
                ends_sentence = _is_break_point(payload, gap_after)
                substantial = joined >= MIN_PARAGRAPH_CHARS

                if pending_figures and ends_sentence and substantial:
                    # صورة منتظرة تخرج فور اكتمال الجملة
                    self._flush_paragraph(buffer, current.blocks)
                    self._flush_figures(pending_figures, current.blocks)
                elif joined >= self.paragraph_max_chars and ends_sentence:
                    self._flush_paragraph(buffer, current.blocks)
                elif joined >= self.paragraph_max_chars * 2:
                    # صمّام أمان: نصّ بلا علامة ترقيم إطلاقًا — وهو وارد
                    # جدًا من نموذج التفريغ — كان يُنتج فقرة واحدة طولها
                    # آلاف الحروف. الانتظار له حدّ.
                    self._flush_paragraph(buffer, current.blocks)
                    self._flush_figures(pending_figures, current.blocks)

        if current is not None:
            self._flush_paragraph(buffer, current.blocks)
            self._flush_figures(pending_figures, current.blocks)

        # أقسام فارغة تمامًا لا قيمة لها في الفهرس
        sections = [s for s in sections if s.blocks]
        for index, section in enumerate(sections, start=1):
            section.title = _section_title(
                index, section, self.screen_text_titles)
        return sections

    @staticmethod
    def _buffer_ends_a_sentence(buffer: List,
                                gap_after: Optional[dict] = None) -> bool:
        """هل يقف النصّ المتراكم عند نهاية جملة؟ (والفارغ يقف دائمًا)."""
        if not buffer:
            return True
        return _is_break_point(buffer[-1], gap_after or {})

    @staticmethod
    def _flush_figures(pending: List, blocks: List[DocumentBlock]) -> None:
        for payload in pending:
            blocks.append(DocumentBlock(
                kind="figure",
                timestamp=payload.timestamp,
                image_id=payload.image_id,
                image_filename=payload.filename,
                caption=_figure_caption(payload),
                ocr_text=payload.ocr_text,
                source_start=payload.timestamp,
                source_end=payload.timestamp))
        pending.clear()

    # ------------------------------------------------------------------
    @staticmethod
    def _count_outputs(plan: DocumentPlan) -> None:
        segments = set()
        figures = set()
        for section in plan.sections:
            for block in section.blocks:
                segments.update(block.segment_ids)
                if block.image_id is not None:
                    figures.add(block.image_id)
        plan.total_segments_out = len(segments)
        plan.total_figures_out = len(figures)


def assert_lossless(plan: DocumentPlan) -> None:
    """بوابة جودة: لا مقطع ضاع ولا تكرّر، ولا صورة كذلك (ADR-010)."""
    if plan.total_segments_in != plan.total_segments_out:
        raise AssertionError(
            f"ضياع/تكرار نص: دخل {plan.total_segments_in} مقطعًا "
            f"وخرج {plan.total_segments_out}")
    if plan.total_figures_in != plan.total_figures_out:
        raise AssertionError(
            f"ضياع/تكرار صور: دخل {plan.total_figures_in} "
            f"وخرج {plan.total_figures_out}")

    seen_segments: List[int] = []
    seen_figures: List[int] = []
    for section in plan.sections:
        for block in section.blocks:
            seen_segments.extend(block.segment_ids)
            if block.image_id is not None:
                seen_figures.append(block.image_id)
    if len(seen_segments) != len(set(seen_segments)):
        raise AssertionError("تكرار segment_id داخل الخطة.")
    if len(seen_figures) != len(set(seen_figures)):
        raise AssertionError("تكرار image_id داخل الخطة.")
