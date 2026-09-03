"""دمج عدة مهام في مستند واحد.

محاضرة مقسَّمة إلى ثلاثة أجزاء، أو مقرّر من عشر حلقات: المستندات المنفصلة
تعني عشرة فهارس وعشر ترقيمات أشكال تبدأ من واحد. الدمج يعطي وثيقة واحدة
بفهرس واحد وترقيم أشكال متصل.

المصيدة الجوهرية هنا هي **تصادم المعرّفات**: كل مهمة ترقّم مقاطعها من 1
وصورها من 1، فدمج الخطط كما هي يُنتج معرّفات مكرّرة تُسقط بوابة
``assert_lossless`` — وهي محقّة، لأن التتبّع إلى المصدر يصير مستحيلًا.
لذلك تُزاح معرّفات كل جزء بقاعدة مستقلة قبل الدمج.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
from utils.logger import logger

# فسحة بين قواعد المعرّفات: تكفي أي مهمة واقعية وتُبقي المعرّف مقروءًا
ID_STRIDE = 100_000


@dataclass
class MergePart:
    """جزء واحد في الدمج."""
    plan: DocumentPlan
    title: str
    images_dir: Path
    # بادئة تميّز صور هذا الجزء داخل المجلد الموحّد
    prefix: str


def _shift_blocks(blocks: List[DocumentBlock], base: int,
                  prefix: str) -> List[DocumentBlock]:
    shifted: List[DocumentBlock] = []
    for block in blocks:
        copy = block.model_copy(deep=True)
        copy.segment_ids = [base + sid for sid in block.segment_ids]
        if block.image_id is not None:
            copy.image_id = base + block.image_id
        if block.image_filename:
            copy.image_filename = f"{prefix}_{block.image_filename}"
        shifted.append(copy)
    return shifted


def merge_plans(parts: List[MergePart], title: str,
                subtitle: str = "") -> DocumentPlan:
    """يبني خطة موحّدة: كل جزء قسمٌ رئيسي وأقسامه تحته."""
    if not parts:
        raise ValueError("لا توجد أجزاء للدمج.")

    sections: List[DocumentSection] = []
    key_points: List[str] = []
    segments_in = figures_in = 0

    for index, part in enumerate(parts):
        base = (index + 1) * ID_STRIDE
        segments_in += part.plan.total_segments_in
        figures_in += part.plan.total_figures_in
        key_points.extend(part.plan.key_points)

        # عنوان الجزء قسمٌ من المستوى الأول، وملخّصه تحته إن وُجد
        sections.append(DocumentSection(
            title=part.title, level=1,
            summary=part.plan.abstract,
            start_timestamp=(part.plan.sections[0].start_timestamp
                             if part.plan.sections else None),
            blocks=[]))

        for section in part.plan.sections:
            copy = section.model_copy(deep=True)
            # تنزل درجة واحدة لتقع تحت عنوان الجزء
            copy.level = min(3, section.level + 1)
            copy.blocks = _shift_blocks(section.blocks, base, part.prefix)
            sections.append(copy)

    plan = DocumentPlan(
        title=title,
        subtitle=subtitle or f"وثيقة موحّدة من {len(parts)} مصادر",
        key_points=key_points[:6],
        sections=sections,
        generated_by="merge:" + "+".join(
            p.plan.generated_by for p in parts[:3]),
        total_segments_in=segments_in,
        total_figures_in=figures_in,
    )

    segments, figures = set(), set()
    for section in plan.sections:
        for block in section.blocks:
            segments.update(block.segment_ids)
            if block.image_id is not None:
                figures.add(block.image_id)
    plan.total_segments_out = len(segments)
    plan.total_figures_out = len(figures)
    return plan


def collect_images(parts: List[MergePart], target_dir: Path) -> int:
    """ينسخ صور كل جزء إلى مجلد موحّد بأسماء مسبوقة بلا تصادم."""
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for part in parts:
        if not part.images_dir.exists():
            continue
        for image in sorted(part.images_dir.iterdir()):
            if not image.is_file():
                continue
            destination = target_dir / f"{part.prefix}_{image.name}"
            if not destination.exists():
                shutil.copy2(image, destination)
            copied += 1
    logger.info(f"نُسخت {copied} صورة إلى مجلد الدمج.")
    return copied


def load_part(job_dir: Path, prefix: str,
              title: Optional[str] = None) -> MergePart:
    """يقرأ خطة مهمة منتهية ويجهّزها للدمج."""
    import json

    plan_path = job_dir / "plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(
            f"لا توجد خطة محفوظة في «{job_dir.name}» — عالِج الملف أولًا.")
    plan = DocumentPlan(**json.loads(plan_path.read_text(encoding="utf-8")))
    return MergePart(plan=plan, title=title or plan.title,
                     images_dir=job_dir / "keyframes", prefix=prefix)
