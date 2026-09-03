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
from utils.timestamps import seconds_to_display

# نهايات جُمل عربية — لتجميع المقاطع في فقرات مقروءة
# نهاية جملة عربية أو لاتينية. النسخة السابقة كتبت ``\\s`` داخل سلسلة
# خام، أي شرطة مائلة حرفية متبوعة بحرف s — فكان أحد بديلَي التعبير ميتًا.
_SENTENCE_END = re.compile(r"[.!?؟…]\s*$")

_ORDINALS = ("الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس",
             "السابع", "الثامن", "التاسع", "العاشر", "الحادي عشر",
             "الثاني عشر")


def _ordinal_title(index: int, timestamp: float) -> str:
    """عنوان قسم مقروء بدل طابع زمني خام.

    «المقطع الزمني 00:00:00» ليس عنوانًا: لا يقول شيئًا عن المحتوى ويجعل
    الفهرس بلا فائدة. الترتيب مع نقطة البداية أوضح، وإعادة الصياغة
    بالذكاء الاصطناعي تستبدله بعنوان حقيقي من المضمون.
    """
    name = _ORDINALS[index - 1] if index <= len(_ORDINALS) else str(index)
    return f"القسم {name} — من {seconds_to_display(timestamp)}"


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
                 adaptive_sections: bool = True) -> None:
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
            events, self._section_seconds(duration))

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
                             section_seconds: float) -> List[DocumentSection]:
        sections: List[DocumentSection] = []
        current: Optional[DocumentSection] = None
        buffer: List = []
        section_start = 0.0

        for timestamp, _, kind, payload in events:
            if current is None or timestamp - section_start >= section_seconds:
                if current is not None:
                    self._flush_paragraph(buffer, current.blocks)
                section_start = timestamp
                current = DocumentSection(
                    title=_ordinal_title(len(sections) + 1, timestamp),
                    level=1, start_timestamp=timestamp, blocks=[])
                sections.append(current)

            if kind == "figure":
                self._flush_paragraph(buffer, current.blocks)
                current.blocks.append(DocumentBlock(
                    kind="figure",
                    timestamp=payload.timestamp,
                    image_id=payload.image_id,
                    image_filename=payload.filename,
                    caption=f"لقطة عند {seconds_to_display(payload.timestamp)}",
                    ocr_text=payload.ocr_text,
                    source_start=payload.timestamp,
                    source_end=payload.timestamp))
            else:
                buffer.append(payload)
                joined = sum(len(s.text_clean or s.text_raw) for s in buffer)
                last = (payload.text_clean or payload.text_raw).strip()
                if joined >= self.paragraph_max_chars and _SENTENCE_END.search(last):
                    self._flush_paragraph(buffer, current.blocks)

        if current is not None:
            self._flush_paragraph(buffer, current.blocks)

        # أقسام فارغة تمامًا لا قيمة لها في الفهرس
        return [s for s in sections if s.blocks]

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
