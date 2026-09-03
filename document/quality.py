"""بوابة جودة قابلة للقياس لخطة المستند قبل التصيير."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from config.schemas import DocumentPlan, TranscriptionResult, KeyframeMetadata
from core.exceptions import QualityGateError


@dataclass(frozen=True)
class QualityReport:
    """مؤشرات جودة مستقلة عن صيغة DOCX/PDF."""
    overall: float
    text_completeness: float
    figure_coverage: float
    ocr_coverage: float
    section_quality: float
    provenance_coverage: float
    empty_sections: int
    duplicate_segments: int
    duplicate_figures: int
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "overall": round(self.overall, 2),
            "text_completeness": round(self.text_completeness, 2),
            "figure_coverage": round(self.figure_coverage, 2),
            "ocr_coverage": round(self.ocr_coverage, 2),
            "section_quality": round(self.section_quality, 2),
            "provenance_coverage": round(self.provenance_coverage, 2),
            "empty_sections": self.empty_sections,
            "duplicate_segments": self.duplicate_segments,
            "duplicate_figures": self.duplicate_figures,
            "warnings": list(self.warnings),
        }


def _ratio(out: int, total: int) -> float:
    if total <= 0:
        return 100.0
    return max(0.0, min(100.0, 100.0 * out / total))


def evaluate(plan: DocumentPlan, keyframes: Iterable[KeyframeMetadata] = ()) -> QualityReport:
    """يحسب جودة الخطة ويكشف المشكلات البنيوية دون تعديل المحتوى."""
    segments: list[int] = []
    figures: list[int] = []
    provenance = 0
    content_blocks = 0
    ocr_blocks = 0
    figure_blocks = 0
    warnings: list[str] = []

    for section in plan.sections:
        for block in section.blocks:
            content_blocks += 1
            segments.extend(block.segment_ids)
            if block.image_id is not None:
                figures.append(block.image_id)
                figure_blocks += 1
            if block.ocr_text.strip():
                ocr_blocks += 1
            if block.segment_ids or block.timestamp is not None:
                provenance += 1

    duplicate_segments = len(segments) - len(set(segments))
    duplicate_figures = len(figures) - len(set(figures))
    empty_sections = sum(1 for s in plan.sections if not s.blocks)

    text_completeness = _ratio(len(set(segments)), plan.total_segments_in)
    figure_coverage = _ratio(len(set(figures)), plan.total_figures_in)
    kfs = list(keyframes)
    expected_ocr = sum(1 for k in kfs if k.ocr_text.strip())
    ocr_coverage = _ratio(expected_ocr, len(kfs)) if kfs else 100.0
    section_quality = 100.0
    if plan.sections:
        section_quality = max(0.0, 100.0 - empty_sections * 20.0)
        if len(plan.sections) > 1:
            populated = sum(1 for s in plan.sections if s.blocks)
            section_quality *= populated / len(plan.sections)
    provenance_coverage = _ratio(provenance, content_blocks)

    if duplicate_segments:
        warnings.append(f"يوجد {duplicate_segments} تكرارًا لمراجع المقاطع.")
    if duplicate_figures:
        warnings.append(f"يوجد {duplicate_figures} تكرارًا لمراجع الصور.")
    if plan.total_segments_in and text_completeness < 100:
        warnings.append("لم تصل كل المقاطع الأصلية إلى الخطة النهائية.")
    if plan.total_figures_in and figure_coverage < 100:
        warnings.append("لم تصل كل اللقطات الأصلية إلى الخطة النهائية.")
    if not plan.sections and (plan.total_segments_in or plan.total_figures_in):
        warnings.append("الخطة لا تحتوي أقسامًا رغم وجود محتوى مصدر.")

    # الأوزان تعكس المنتج: اكتمال النص والبنية أهم من OCR الاختياري.
    overall = (
        text_completeness * 0.40
        + figure_coverage * 0.20
        + section_quality * 0.20
        + provenance_coverage * 0.15
        + ocr_coverage * 0.05
    )
    return QualityReport(
        overall=max(0.0, min(100.0, overall)),
        text_completeness=text_completeness,
        figure_coverage=figure_coverage,
        ocr_coverage=ocr_coverage,
        section_quality=section_quality,
        provenance_coverage=provenance_coverage,
        empty_sections=empty_sections,
        duplicate_segments=duplicate_segments,
        duplicate_figures=duplicate_figures,
        warnings=tuple(warnings),
    )


def assert_quality_gate(report: QualityReport, minimum: float = 85.0) -> None:
    """يفشل فقط عند خلل بنيوي أو درجة منخفضة جدًا؛ OCR اختياري ولا يُسقط المهمة.

    ترفع ``QualityGateError`` (وهي أيضًا ``AssertionError`` للتوافق مع
    الاختبارات القائمة) بدل ``AssertionError`` مجردة، لتشارك في نظام
    ``error_code``/``category`` الذي يعتمده بقية التطبيق بدل الظهور
    للمستخدم كخطأ داخلي عام (راجع ``utils/error_reporting.py``).
    """
    if report.duplicate_segments or report.duplicate_figures:
        raise QualityGateError("فشل بوابة الجودة: توجد مراجع مكررة داخل الخطة.")
    if report.overall < minimum:
        raise QualityGateError(
            f"فشل بوابة جودة المستند: الدرجة {report.overall:.1f}% "
            f"أقل من الحد {minimum:.1f}%."
        )
