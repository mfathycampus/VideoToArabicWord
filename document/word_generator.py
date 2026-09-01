"""تصيير خطة المستند على القالب الاحترافي.

فصل المسؤوليات:
    ``document/planner``  → *ماذا* يحتوي المستند (أقسام وكتل)
    ``ai/rewriter``       → خطة أغنى، اختياريًا
    ``document/template`` → *كيف* يبدو (أنماط، غلاف، فهرس)
    هذه الوحدة           → تُلبس الأولى بالثاني

المولّد لا يعرف مصدر الخطة، فيمكن استبدال المُخطِّط وحده أو القالب وحده.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Inches, Pt
except ImportError as _exc:  # pragma: no cover - مسار بيئة معطوبة
    raise ImportError(
        "تعذر تحميل python-docx.\n"
        f"السبب الأصلي: {_exc}\n\n"
        "إن ذكر الخطأ وحدة «exceptions» فالسبب حزمة «docx» القديمة "
        "المهجورة، وهي تحجب python-docx. الإصلاح:\n"
        "    python -m pip uninstall -y docx\n"
        "    python -m pip install --upgrade --force-reinstall python-docx\n\n"
        "للتشخيص الكامل: python tools/doctor.py"
    ) from _exc

from PIL import Image

from config.schemas import DocumentPlan, VideoMetadata
from config.settings import DocumentConfig
from core.exceptions import DocumentGenerationError
from document.planner import assert_lossless
from document.rtl_utils import (
    add_mixed_text, dominant_direction, set_paragraph_ltr, set_paragraph_rtl,
    style_arabic_run, style_ltr_run,
)
from document.template import (
    BODY, CAPTION, LEAD, TIMECODE, Theme, add_cover_page, add_header,
    add_page_number_footer, add_table_of_contents, build_styles,
    configure_page,
)
from utils.logger import logger
from utils.timestamps import humanize_duration, seconds_to_display


class DocumentGenerator:
    def __init__(self, config: Optional[DocumentConfig] = None,
                 theme: Optional[Theme] = None) -> None:
        self.config = config or DocumentConfig()
        self.theme = theme or Theme(
            font=self.config.font_family,
            heading_font=self.config.font_family,
            body_pt=self.config.body_size_pt,
            logo_path=self.config.logo_path,
            logo_width_inches=self.config.logo_width_inches)

    # ------------------------------------------------------------------
    def generate(self, plan: DocumentPlan, metadata: VideoMetadata,
                 images_dir: Path, output_path: Path) -> Path:
        assert_lossless(plan)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        document = Document()
        configure_page(document, self.theme)
        build_styles(document, self.theme)

        add_cover_page(
            document, self.theme,
            title=plan.title,
            subtitle=plan.subtitle,
            facts=self._cover_facts(plan, metadata))

        add_header(document, self.theme, plan.title)
        add_page_number_footer(document, self.theme, self.config.footer_text)

        if self.config.enable_toc:
            add_table_of_contents(document, self.theme)

        self._add_abstract(document, plan)
        missing = self._add_body(document, plan, images_dir)

        if missing:
            raise DocumentGenerationError(
                f"{missing} صورة مُشار إليها غير موجودة على القرص.")

        document.save(str(output_path))
        logger.info(f"تم إنشاء المستند: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    def _cover_facts(self, plan, metadata) -> list[tuple[str, str]]:
        facts = [
            ("الملف المصدر", metadata.filename),
            ("مدة الفيديو", humanize_duration(metadata.duration_seconds)),
            ("الدقة", f"{metadata.width}×{metadata.height}"),
            ("عدد الأقسام", str(len(plan.sections))),
            ("عدد اللقطات", str(plan.total_figures_out)),
        ]
        if plan.generated_by != "timeline":
            facts.append(("صياغة النص", plan.generated_by))
        return facts

    def _add_abstract(self, document, plan: DocumentPlan) -> None:
        if not (plan.abstract or plan.key_points):
            return
        heading = document.add_paragraph(style="Heading 1")
        set_paragraph_rtl(heading)
        style_arabic_run(heading.add_run("ملخّص تنفيذي"),
                         self.theme.heading_font, 17, bold=True,
                         color=self.theme.accent)

        if plan.abstract:
            paragraph = document.add_paragraph(style=LEAD)
            set_paragraph_rtl(paragraph)
            style_arabic_run(paragraph.add_run(plan.abstract),
                             self.theme.font, self.theme.body_pt + 1)

        if plan.key_points:
            for point in plan.key_points:
                bullet = document.add_paragraph(style="List Bullet")
                set_paragraph_rtl(bullet)
                style_arabic_run(bullet.add_run(point), self.theme.font,
                                 self.theme.body_pt)

    # ------------------------------------------------------------------
    def _add_body(self, document, plan: DocumentPlan, images_dir: Path) -> int:
        missing = 0
        figure_number = 0

        for section in plan.sections:
            heading = document.add_paragraph(
                style=f"Heading {min(3, max(1, section.level))}")
            size = 17 if section.level == 1 else 14
            color = (self.theme.accent if section.level == 1
                     else self.theme.accent_soft)
            if dominant_direction(section.title) == "rtl":
                set_paragraph_rtl(heading)
                add_mixed_text(heading, section.title, self.theme.heading_font,
                               size, bold=True, color=color)
            else:
                set_paragraph_ltr(heading, WD_ALIGN_PARAGRAPH.RIGHT)
                style_ltr_run(heading.add_run(section.title),
                              self.theme.heading_font, size, bold=True,
                              color=color)

            if section.summary:
                lead = document.add_paragraph(style=LEAD)
                set_paragraph_rtl(lead)
                style_arabic_run(lead.add_run(section.summary),
                                 self.theme.font, self.theme.body_pt + 1,
                                 color=self.theme.muted)

            for block in section.blocks:
                if block.kind == "figure":
                    path = images_dir / (block.image_filename or "")
                    if not path.exists():
                        missing += 1
                        logger.warning(f"صورة مفقودة: {block.image_filename}")
                        continue
                    figure_number += 1
                    self._add_figure(document, path, block, figure_number)
                elif block.kind == "bullets":
                    for item in block.bullets:
                        bullet = document.add_paragraph(style="List Bullet")
                        set_paragraph_rtl(bullet)
                        style_arabic_run(bullet.add_run(item), self.theme.font,
                                         self.theme.body_pt)
                elif block.kind == "quote":
                    quote = document.add_paragraph(style=LEAD)
                    set_paragraph_rtl(quote)
                    quote.paragraph_format.left_indent = Inches(0.35)
                    style_arabic_run(quote.add_run(f"«{block.text}»"),
                                     self.theme.font, self.theme.body_pt,
                                     italic=True, color=self.theme.muted)
                elif block.text:
                    paragraph = document.add_paragraph(style=BODY)
                    set_paragraph_rtl(paragraph)
                    style_arabic_run(paragraph.add_run(block.text),
                                     self.theme.font, self.theme.body_pt)
        return missing

    def _add_figure(self, document, path: Path, block, number: int) -> None:
        """الصورة + تسمية واحدة تحمل الرقم والتوقيت.

        فقرة توقيت منفصلة كانت تضخّم مجموعة الشكل، فتُدفع المجموعة كاملة
        إلى الصفحة التالية ويبقى نصف صفحة أبيض. دمجها في التسمية يوفّر
        سطرين لكل صورة ويسمح بشكلين في الصفحة.
        """
        paragraph = document.add_paragraph()
        set_paragraph_rtl(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
        paragraph.paragraph_format.keep_with_next = True
        run = paragraph.add_run()

        width, height = self._fit(path)
        run.add_picture(str(path), width=Inches(width), height=Inches(height))
        self._add_image_border(run)

        # التسمية فقرة LTR: تبدأ برقم وتنتهي بتوقيت، فترتيبها في فقرة
        # عربية ينعكس. الكلمة العربية «شكل» داخلها تبقى سليمة لأنها run
        # مستقل يحمل w:rtl.
        caption = document.add_paragraph(style=CAPTION)
        set_paragraph_ltr(caption, WD_ALIGN_PARAGRAPH.CENTER)
        style_arabic_run(caption.add_run("شكل "), self.theme.font, 9.5,
                         bold=True, color=self.theme.muted)
        style_ltr_run(caption.add_run(str(number)), self.theme.font, 9.5,
                      bold=True, color=self.theme.muted)
        if self.config.show_timecodes and block.timestamp is not None:
            style_ltr_run(caption.add_run("  ·  "), self.theme.font, 9.5,
                          color=self.theme.muted)
            style_arabic_run(caption.add_run("التوقيت "), self.theme.font, 9.5,
                             color=self.theme.muted)
            style_ltr_run(caption.add_run(seconds_to_display(block.timestamp)),
                          self.theme.font, 9.5, bold=True,
                          color=self.theme.accent_soft)

        # نص التسمية الوصفي. كان يُحسب في الخطة ثم يُهمل هنا تمامًا،
        # فتخرج كل صورة بلا شرح — وهو العيب الذي أبلغ عنه المستخدم.
        text = (block.caption or "").strip()
        if text:
            note = document.add_paragraph(style=CAPTION)
            set_paragraph_rtl(note, WD_ALIGN_PARAGRAPH.CENTER)
            note.paragraph_format.keep_with_next = False
            add_mixed_text(note, text, self.theme.font, 9.5,
                           color=self.theme.muted)

    def _fit(self, path: Path) -> tuple[float, float]:
        """يلائم الصورة داخل صندوق الصفحة مع الحفاظ على النسبة."""
        max_width = self.config.max_image_width_inches
        max_height = self.config.max_image_height_inches
        try:
            with Image.open(path) as image:
                width_px, height_px = image.size
        except Exception:
            return max_width, max_width * 0.5625

        ratio = height_px / width_px
        width = max_width
        height = width * ratio
        if height > max_height:
            height = max_height
            width = height / ratio
        return width, height

    @staticmethod
    def _add_image_border(run) -> None:
        """إطار رفيع حول الصورة — يفصل لقطة الشاشة عن بياض الصفحة."""
        try:
            inline = run._r.find(qn("w:drawing"))
            if inline is None:
                return
            for pic in inline.iter(
                    "{http://schemas.openxmlformats.org/drawingml/2006/picture}spPr"):
                line = OxmlElement("a:ln")
                line.set("w", "9525")
                fill = OxmlElement("a:solidFill")
                color = OxmlElement("a:srgbClr")
                color.set("val", "C8D0DA")
                fill.append(color)
                line.append(fill)
                pic.append(line)
                break
        except Exception:  # pragma: no cover - تحسين تجميلي فقط
            pass
