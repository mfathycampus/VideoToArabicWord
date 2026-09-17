"""إخفاء الأسماء — حالاتٌ من قائمة معلّمات حقيقية في «متابعة تحضير المعلمين».

على اللقطة الحقيقية (قائمة 7 معلّمات + اسمٌ في الترويسة): مُوِّهت 7 من 8؛
«Abrar Alhussain» لم يقرأها OCR أصلًا فلا شيء يُكشف.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from video.redact import find_name_boxes, redact_keyframe, scrub_text


def _w(text, line, left, top=0, height=14):
    return {"text": text, "conf": 90.0, "line": ("1", "1", str(line)),
            "left": left, "top": top, "width": 8 * len(text), "height": height}


def test_person_names_are_found_and_ui_labels_are_not():
    words = [_w("©", 1, 30), _w("Abdullah", 1, 48), _w("Omran", 1, 120),
             _w("Math", 2, 450), _w("Education", 2, 490),
             _w("Holy", 3, 450), _w("Quran", 3, 490),
             _w("Lesson", 4, 20), _w("Feedback", 4, 80)]
    boxes, names = find_name_boxes(words)
    assert names == ["Abdullah Omran"]
    assert {b["text"] for b in boxes} == {"Abdullah", "Omran"}


def test_name_inside_a_longer_line_and_ocr_punctuation():
    words = [_w("Week", 1, 10), _w("of", 1, 60), _w("Sep", 1, 80),
             _w("Alhanouf", 1, 250), _w("Alsubaie", 1, 330),
             _w("View", 1, 400), _w("Account", 1, 440),
             _w("AlAnoud", 2, 48, top=40), _w("AlOtaib!", 2, 120, top=40)]
    _boxes, names = find_name_boxes(words)
    assert "Alhanouf Alsubaie" in names
    assert "AlAnoud AlOtaib" in names


def test_misread_rows_in_the_same_list_column_are_hidden():
    """‏«Adwaa Alwaqdani» قُرئ «مديهة Alwaqdont» — في عمود الأسماء نفسه."""
    words = [_w("Abdullah", 1, 48, top=10), _w("Omran", 1, 120, top=10),
             _w("مديهة", 2, 47, top=60), _w("Alwaqdont", 2, 100, top=60),
             _w("12:15", 3, 50, top=110), _w("PM", 3, 90, top=110)]
    boxes, _names = find_name_boxes(words)
    hidden = {b["text"] for b in boxes}
    assert {"مديهة", "Alwaqdont"} <= hidden
    assert "12:15" not in hidden          # سطر زمنٍ في العمود نفسه يبقى


def test_emails_are_hidden():
    boxes, names = find_name_boxes([_w("teacher.one@school.edu.sa", 1, 10)])
    assert names == ["teacher.one@school.edu.sa"] and len(boxes) == 1
    assert "@" not in scrub_text("راسل teacher.one@school.edu.sa", names)


def test_redaction_changes_the_pixels_and_scrubs_the_text(tmp_path):
    image = tmp_path / "shot.png"
    canvas = Image.new("RGB", (300, 60), "white")
    for x in range(40, 200, 4):
        for y in range(10, 30):
            canvas.putpixel((x, y), (0, 0, 0))
    canvas.save(image)
    before = image.read_bytes()

    words = [_w("Afnan", 1, 40, top=10, height=20), _w("Alharbi", 1, 100,
                                                       top=10, height=20)]
    text, names = redact_keyframe(image, words, "Afnan Alharbi\nPlanned 9")

    assert names == ["Afnan Alharbi"]
    assert text == "Planned 9"
    assert image.read_bytes() != before
