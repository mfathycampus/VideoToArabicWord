"""ثقة Tesseract تفصل القراءة الناجحة عن الضوضاء.

هذه الأسطر مأخوذة حرفيًا من مخرَج Tesseract على اللقطات الخمس عشرة
المستخرَجة من تسجيل حقيقي مدّته 8.5 دقائق. قبل هذا الفحص كانت كلّها —
الناجح والفاشل — تُكتب تعليقًا تحت الصور، وتنال من بوابة الجودة 100٪
لأن شرطها الوحيد كان «هل قرأ Tesseract شيئًا؟».
"""
from __future__ import annotations

import pytest

from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
from document.quality import evaluate
from video.ocr import MIN_LINE_CONFIDENCE, _clean, _confident_lines

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t" \
         "left\ttop\twidth\theight\tconf\ttext"


def _tsv(lines):
    """يبني مخرَج tsv مصغّرًا: [(رقم السطر، [(كلمة، ثقة)…])…]."""
    rows = [HEADER]
    for line_no, words in enumerate(lines, start=1):
        for word_no, (word, conf) in enumerate(words, start=1):
            rows.append(
                f"5\t1\t1\t1\t{line_no}\t{word_no}\t0\t0\t10\t10\t{conf}\t{word}")
    return "\n".join(rows)


def test_high_confidence_line_is_kept():
    tsv = _tsv([[("Enter", 90.0), ("password", 92.2)]])
    assert _confident_lines(tsv) == "Enter password"


def test_failed_read_is_dropped():
    """«ston. © Freee (ease text ner Al stern» — ثقة 33."""
    tsv = _tsv([[("ston.", 30.0), ("©", 12.0), ("Freee", 40.0),
                 ("(ease", 35.0), ("text", 48.0)]])
    assert _confident_lines(tsv) == ""


def test_browser_title_bar_is_dropped_by_confidence():
    """«Bi Lessons | Chalk - Googke Chrome» — ثقة 63، والاسم نفسه مقروء
    خطأً. الثقة تكفي هنا بلا قاعدة خاصّة بالمتصفّحات."""
    tsv = _tsv([[("Bi", 55.0), ("Lessons", 70.0), ("|", 40.0),
                 ("Chalk", 75.0), ("-", 50.0), ("Googke", 68.0),
                 ("Chrome", 88.0)]])
    assert _confident_lines(tsv) == ""


def test_screen_order_is_preserved_not_confidence_order():
    """النصّ يُطبع تحت الصورة في المستند: ترتيبه بالثقة يجعله غير مقروء."""
    tsv = _tsv([[("Timetable", 80.0)],
                [("Social", 95.0), ("Studies", 95.0)]])
    assert _confident_lines(tsv) == "Timetable\nSocial Studies"


def test_negative_confidence_rows_are_ignored():
    """‏Tesseract يضع ‎-1‎ للصفوف التي ليست كلمات (حدود الكتل)."""
    tsv = _tsv([[("", -1.0), ("Import", 93.0), ("Classes", 91.0)]])
    assert _confident_lines(tsv) == "Import Classes"


def test_threshold_is_a_measured_value():
    """الحدّ بين 75 و80: أدنى سطر صحيح قيس عند 80، وأعلى سطر مشوّش 72."""
    assert 72.0 < MIN_LINE_CONFIDENCE <= 80.0


def test_clean_still_drops_one_character_lines():
    assert _clean("Import Classes\nx\n\n=") == "Import Classes"


# ── أثر ذلك في بوابة الجودة ───────────────────────────────────────────

def _figures(captions):
    blocks = [DocumentBlock(kind="figure", image_id=i,
                            image_filename=f"{i}.jpg", timestamp=float(i),
                            caption=c, ocr_text=c.split(" — ")[0])
              for i, c in enumerate(captions, start=1)]
    return DocumentPlan(title="مستند", sections=[
        DocumentSection(title="قسم", start_timestamp=0.0, blocks=blocks)])


def _captioning(plan):
    report = evaluate(plan, [])
    return next(m for m in report.metrics if m.name == "figure_captioning")


@pytest.mark.parametrize("caption", [
    "timetable?semesterid=1186991 — 00:02:53",
    "mast S078 chalkcom/lessons/time — 00:03:40",
    "Bi Lessons | Chalk - Googke Chrome — 00:06:33",
    "لقطة عند 00:01:34",
])
def test_junk_captions_score_zero(caption):
    """كلّها ظهرت في مستند حقيقي ونالت 100٪ من المقياس السابق."""
    assert _captioning(_figures([caption])).value == 0.0


@pytest.mark.parametrize("caption", [
    "Enter password — 00:00:37",
    "Social Studies - Section 1 — 00:02:53",
    "How many days are in your rotation? x — 00:02:25",
])
def test_real_screen_text_captions_score(caption):
    assert _captioning(_figures([caption])).value == 100.0


def test_ocr_presence_alone_no_longer_counts():
    """العيب بعينه: الشرط كان ``ocr_text.strip()`` لا جودة التعليق."""
    block = DocumentBlock(kind="figure", image_id=1, image_filename="1.jpg",
                          timestamp=1.0, caption="لقطة عند 00:01:34",
                          ocr_text="ston. © Freee (ease text ner")
    plan = DocumentPlan(title="مستند", sections=[
        DocumentSection(title="قسم", start_timestamp=0.0, blocks=[block])])
    assert _captioning(plan).value == 0.0
