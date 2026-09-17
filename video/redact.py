"""إخفاء أسماء الأشخاص من لقطات الشاشة ونصّها.

سبب وجوده: تسجيل «متابعة تحضير المعلمين» يعرض قائمة معلّمات بأسمائهن
الكاملة وحالة تحضير كلٍّ منهن («Not Met»، «0 / 5 Lessons»). المستند
يُوزَّع ويُرفع حزمة SCORM، فخرجت الأسماء في اللقطات، وفي نصّ الشاشة تحت
كل صورة، وفي مسرد الحزمة التعليمية («Alhanouf Alsubaie»).

**الكشف تقديري ومكتوب على هذه الحدود:**

* يلتقط أسماء بالحروف اللاتينية كما تعرضها أنظمة المدارس: سطرٌ من
  كلمتين إلى أربع، كلٌّ منها تبدأ بحرف كبير، وليس فيها كلمة واجهة معروفة
  (``_UI_WORDS``) — و«Abdullah Omran» يُموَّه و«Math Education» يبقى.
* وعناوين البريد الإلكتروني.
* لا يلتقط الأسماء العربية المكتوبة على الشاشة، ولا الأسماء المنطوقة في
  الكلام — الثانية تعالجها تعليمة الصياغة (``ai/rewriter.py``).

والخطأ في اتجاه التمويه أرخص: كلمة واجهة مموَّهة لا تضرّ قارئًا،
واسمٌ مكشوف لا يُستردّ بعد التوزيع.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List

from utils.logger import logger

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NAME_WORD = re.compile(r"^[A-Z][A-Za-z'\-]{1,}$")

#: كلمات واجهة وتعليم شائعة. أي سطر فيه واحدة منها ليس اسم شخص.
_UI_WORDS = {w.lower() for w in """
    Admin Account Activity All Arabic Art Assessment Assessments Attached Back
    Boys Biology Calendar Cancel Chemistry Class Classes Close Comment Comments
    Computer Criteria Curriculum Dashboard Day Days Delete Edit Education
    Elementary English Feedback File Filter French Friday Geography Girls Grade
    Group Groups Help High History Holy Home Instruction Islamic Kg Lesson
    Lessons Math Mathematics Menu Met Middle Monday Month Music New Next Not
    Physical Physics Plan Planned Planner Pending Primary Quran Reports Reset
    Resources Saturday School Schools Science Search Section Select Settings
    Share Shared Social Studies Subject Sunday Support Teacher Teachers This
    Thursday Timeline Today Tuesday Unit Units Upload View Wednesday Week Weekly
    Welcome With Your Assessments Activity Month Year Page Status Total Report
    January February March April May June July August September October
    November December Sep Oct Nov Dec Jan Feb Mar Apr Jun Jul Aug
    Summary Details Overview Profile Logout Login Sign Student Students Parent
    Parents Staff Principal Department Library Sports Health Technology
""".split()}


def _is_name_line(words: List[str]) -> bool:
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower() in _UI_WORDS for w in words):
        return False
    return all(_NAME_WORD.match(w) and len(w) >= 3 for w in words)


def find_name_boxes(words: Iterable[dict]) -> tuple[list[dict], list[str]]:
    """‏(كلمات تُموَّه، الأسماء النصّية) من كلمات OCR بإحداثياتها."""
    lines: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for word in words:
        lines.setdefault(word["line"], []).append(word)

    hide: list[dict] = []
    names: list[str] = []
    for items in lines.values():
        # ‏«AlOtaib!»: الحرف الأخير قراءةٌ خاطئة لـ i/l — لا جزءٌ من الاسم
        tokens = [w["text"].strip(".,:;()[]!|") for w in items]
        for w, token in zip(items, tokens):
            if _EMAIL.fullmatch(token):
                hide.append(w)
                names.append(token)
        # نافذة منزلقة: «AA Alhanouf Alsubaie View Account» سطرٌ واحد
        index = 0
        while index < len(tokens):
            for size in (4, 3, 2):
                window = tokens[index:index + size]
                if len(window) == size and _is_name_line(window):
                    hide.extend(items[index:index + size])
                    names.append(" ".join(window))
                    index += size
                    break
            else:
                index += 1
    hide.extend(_same_column_rows(lines, hide))
    return hide, names


#: فرق يسار الكلمة الأولى المقبول ليُعدّ السطر في عمود الأسماء نفسه.
COLUMN_TOLERANCE_PX = 12


def _same_column_rows(lines, hidden: list[dict]) -> list[dict]:
    """أسطرٌ في عمود قائمة الأسماء نفسه، قرأها OCR مشوَّهة.

    قائمة معلّمات حقيقية: «Abdullah Omran» و«Afnan Alharbi» كُشفا، و
    «Adwaa Alwaqdani» قُرئ «مديهة Alwaqdont» فنجا. السطر الذي يبدأ عند
    يسار اسمٍ مكشوف وارتفاعه مثله اسمٌ في القائمة نفسها غالبًا.
    """
    if not hidden:
        return []
    anchors = [(w["left"], w["height"]) for w in hidden]
    hidden_ids = {id(w) for w in hidden}
    extra: list[dict] = []
    for items in lines.values():
        if any(id(w) in hidden_ids for w in items):
            continue
        texts = [w for w in items if w["text"].strip("©®@|_-—")]
        if not texts:
            continue
        first = texts[0]
        in_column = any(abs(first["left"] - left) <= COLUMN_TOLERANCE_PX
                        and 0.6 <= first["height"] / max(1.0, height) <= 1.6
                        for left, height in anchors)
        if not in_column:
            continue
        cleaned = [w["text"].strip(".,:;()[]!|") for w in texts]
        # سطرٌ بلا كلمةٍ تشبه اسمًا (أزمنة، أرقام، كلمات واجهة) ليس اسمًا:
        # «12:15 PM - 12:45 PM» في بطاقةٍ يوافق يسارُها يسارَ اسم مصادفةً.
        looks_named = any(
            (_NAME_WORD.match(t) and len(t) >= 3 and t.lower() not in _UI_WORDS)
            or re.search(r"[\u0600-\u06FF]{3,}", t)
            for t in cleaned)
        if not looks_named:
            continue
        for w, t in zip(texts, cleaned):
            if t.lower() not in _UI_WORDS:
                extra.append(w)
    return extra


def blur_boxes(image_path: Path, boxes: list[dict], pad: int = 3) -> bool:
    """يُبكسل المستطيلات في الصورة ويحفظها مكانها."""
    if not boxes:
        return False
    from PIL import Image

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    for box in boxes:
        left = max(0, int(box["left"]) - pad)
        top = max(0, int(box["top"]) - pad)
        right = min(image.width, int(box["left"] + box["width"]) + pad)
        bottom = min(image.height, int(box["top"] + box["height"]) + pad)
        if right - left < 2 or bottom - top < 2:
            continue
        region = image.crop((left, top, right, bottom))
        small = region.resize((max(1, region.width // 8),
                               max(1, region.height // 8)))
        image.paste(small.resize(region.size, Image.NEAREST), (left, top))
    suffix = image_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.save(image_path, quality=88)
    else:
        image.save(image_path)
    return True


def scrub_text(text: str, names: Iterable[str]) -> str:
    """يحذف الأسماء المكتشفة وعناوين البريد من نصّ الشاشة."""
    for name in sorted(set(names), key=len, reverse=True):
        text = text.replace(name, "")
    text = _EMAIL.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if len(line) >= 2)


def redact_keyframe(image_path: Path, words: list[dict], text: str
                    ) -> tuple[str, list[str]]:
    """يموّه الأسماء في اللقطة ويعيد (النصّ منقّى، الأسماء)."""
    boxes, names = find_name_boxes(words)
    if boxes:
        try:
            blur_boxes(image_path, boxes)
        except Exception as exc:                     # noqa: BLE001
            logger.warning(f"تعذّر تمويه الأسماء في {image_path.name}: {exc}")
    return scrub_text(text, names), names
