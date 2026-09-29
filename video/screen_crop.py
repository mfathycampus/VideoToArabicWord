"""قصّ زينة الشاشة — إزالة ما لا يخصّ الدرس من كل لقطة.

**هذا ليس تحسينًا تجميليًّا، بل إغلاق تسريب.** فُحصت لقطة من مخرج
حقيقي فوجدت فيها:

* شريط عنوان المتصفّح — رابط النظام بمعرّفاته: ``…?s_iid=5078&s_sid=14821``
* شريط المفضّلة — روابط المحاضر الشخصية، منها «My files – OneDrive»
* شريط مهام ويندوز — كل تطبيق مفتوح على جهازه
* الساعة وودجت الطقس — ``39°C`` واسم مدينته

مستندٌ يُوزَّع على طلاب، أو يُرفع وحدةً في منصّة تعلّم، يحمل كل ذلك.
وهذا وحده سببٌ كافٍ لرفض الأداة في أيّ مراجعة أمنية.

**والكشف بنيويّ لا بالمطابقة.** لا نبحث عن شكل متصفّح بعينه — ذلك
يكسر مع أول تحديث واجهة أو ثيم داكن. الزينة لها خاصيّة واحدة ثابتة:
**صفوفٌ لا تتغيّر عبر الزمن.** فنقارن عدّة إطارات من التسجيل ونقصّ من
الأعلى والأسفل ما بقي ثابتًا فيها كلّها بينما تغيّر وسطُ الشاشة.

**وحدّ الأمان مقصود:** لا نقصّ أكثر من ربع الارتفاع إطلاقًا. صفحةٌ
ساكنة حقًّا (نصّ لم يتغيّر بين الإطارات) تبدو للكاشف زينةً، وقصُّها
يحذف الدرس نفسه. والخطأ هنا في اتجاه واحد فقط: ترك شريطٍ أهونُ من
حذف محتوى.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from utils.logger import logger

#: أقصى ما يُقصّ من الأعلى ومن الأسفل — نسبةً من الارتفاع.
MAX_TOP_RATIO = 0.25
MAX_BOTTOM_RATIO = 0.12
#: صفٌّ يُعدّ «ثابتًا» إن كان أقصى فرقه بين الإطارات دون هذا الحدّ
#: (0–255).
#:
#: **مُعايَر على تسجيل حقيقي** (‏1920×1040، ستّة إطارات متباعدة):
#:   صفوف 0–83 فرقها ‎0–3 ثم يقفز إلى 37 عند الصفّ 84 — وهو بالضبط
#:   أوّل صفّ من محتوى الصفحة تحت شريطي العنوان والمفضّلة.
#:   وشريط المهام يبدأ عند الصفّ 967.
#:
#: عند 3.0 كان القصّ يترك ثلاثة صفوف من الزينة و26 من شريط المهام؛
#: وعند 8.0 يبتلع 156px من المحتوى. و4.0 يعطي 84 و73 — أي الحدّين
#: الحقيقيين. الساعة في شريط المهام تتغيّر بالدقائق، ولذلك يحتاج
#: أسفلُ الشاشة تسامحًا أعلى قليلًا من أعلاها.
STATIC_ROW_TOLERANCE = 4.0
#: أقلّ عدد إطارات يصلح للمقارنة. بإطارين يصير القرار حظًّا.
MIN_FRAMES = 3

#: **الحارس الفاصل.** فوق هذه النسبة من الصفوف الثابتة لا يُقصّ شيء.
#:
#: الزينة حاشيةٌ حول محتوى متحرّك — لا العكس. فحين يكون أكثرُ الإطار
#: ثابتًا فالتسجيل ساكن (شريحة معروضة، كاميرا على لوح) لا شاشةٌ
#: مؤطَّرة، ومنطق «الصفوف الثابتة» يفقد معناه فيبتلع المحتوى نفسه.
#:
#: **مُعايَر على الطرفين:**
#:   تسجيل شاشة حقيقي (متصفّح + شريط مهام): **20٪** ثابت ⇒ يُقصّ
#:   فيديوهات مصفوفة الاختبار: **61–62٪** ثابت ⇒ لا يُقصّ
#:
#: وكانت العتبة 0.9 فسقطت عليها مصفوفةُ الاختبار كلّها: قُصّت لقطاتها
#: فاختلّ فحص الدوران — وهو بالضبط ما يُفترض أن يمنعه هذا الحدّ.
MAX_STATIC_RATIO = 0.5


# ---------------------------------------------------------------------
# منصّة الاجتماع — لوحة المشاركين وحواشي العرض السوداء
# ---------------------------------------------------------------------
# **تسريبٌ ثانٍ من النوع نفسه، رُصد على تسجيل Teams حقيقي** (81 دقيقة،
# 128 لقطة): الشاشة المشارَكة تشغل يسار الإطار، وعلى يمينه عمودٌ من
# بطاقات المشاركين — وجوهٌ وأسماء («Hanaa...»، «Nasser I.»، «+89») —
# وتحته شريطٌ أسود. الصفوف الثابتة لا تكشف ذلك: البطاقات جانبية لا
# أفقية، ووجوه المتكلّمين تتغيّر.
#
# **والكشف بنيويّ أيضًا:** منصّة العرض خلفيّةٌ سوداء تحيط بمستطيل
# الشاشة المشارَكة، ويفصلها عن لوحة المشاركين عمودٌ أسود خالص. مقيس
# على اللقطات نفسها (نسبة البكسلات الداكنة لكل عمود، وسيطًا عبر الإطارات):
#   أعمدة الشاشة المشارَكة ‎0.07–0.11 · الفاصل ‎1.0 · البطاقات ‎0.38–1.0
#   صفوف الشاشة ‎0.06–0.13 · الشريط السفلي ‎0.93–1.0
#
# نقصّ إلى أعرض مدى متّصل من الأعمدة (والصفوف) الفاتحة، **بشرط** أن
# يحدّه من الجهة المقصوصة فاصلٌ داكن، وأن يبقى المحتوى أكثر من نصف
# الإطار. شاشةٌ بثيمٍ داكن لا تعطي مدًى فاتحًا عريضًا، فلا تُقصّ.

#: رماديٌّ دون هذا يُعدّ «أسود المنصّة». المنصّة سوداء خالصة (‏0–6 مقيسًا)،
#: والثيمات الداكنة للتطبيقات أفتح منها (‏#121212 = 18، ‏#1e1e1e = 30) —
#: فلا تُعدّ منصّةً ولا تُقصّ.
STAGE_DARK_LEVEL = 12
#: عمودٌ/صفٌّ «فاتح» إن كانت نسبة الداكن فيه دون هذا.
STAGE_BRIGHT_MAX = 0.5
#: الفاصل الملاصق للمدى المحتفَظ به يجب أن يكون داكنًا بهذه النسبة فأكثر.
STAGE_GUTTER_MIN = 0.85
#: أقلّ نسبة للمحتوى المحتفَظ به من العرض/الارتفاع.
STAGE_MIN_CONTENT = 0.55
#: أقصى ما يُقصّ من كل جهة.
STAGE_MAX_SIDE = 0.35
#: قصٌّ أصغر من هذه النسبة لا يستحقّ.
STAGE_MIN_SIDE = 0.01


@dataclass
class CropBox:
    top: int = 0
    bottom: int = 0          # عدد الصفوف المقصوصة من الأسفل
    height: int = 0
    left: int = 0            # أعمدة تُقصّ من اليسار
    right: int = 0           # أعمدة تُقصّ من اليمين
    width: int = 0           # العرض الذي عُوير عليه القصّ الأفقي

    @property
    def active(self) -> bool:
        return self.top > 0 or self.bottom > 0 or self.left > 0 or self.right > 0

    def matches(self, image: np.ndarray) -> bool:
        """هل عُوير الصندوق على أبعاد هذه الصورة؟"""
        if self.height and image.shape[0] != self.height:
            return False
        if (self.left or self.right) and self.width and image.shape[1] != self.width:
            return False
        return True

    def apply(self, image: np.ndarray) -> np.ndarray:
        if not self.active:
            return image
        end = image.shape[0] - self.bottom if self.bottom else image.shape[0]
        right = image.shape[1] - self.right if self.right else image.shape[1]
        return image[self.top:end, self.left:right]

    def describe(self) -> str:
        parts = []
        if self.top:
            parts.append(f"{self.top}px من الأعلى")
        if self.bottom:
            parts.append(f"{self.bottom}px من الأسفل")
        if self.left:
            parts.append(f"{self.left}px من اليسار")
        if self.right:
            parts.append(f"{self.right}px من اليمين")
        return "، ".join(parts) or "لا قصّ"


def _stage_cut(dark: np.ndarray) -> tuple[int, int]:
    """‏(يُقصّ من البداية، يُقصّ من النهاية) على محور واحد — أو (0، 0)."""
    size = len(dark)
    if size == 0:
        return 0, 0
    bright = dark < STAGE_BRIGHT_MAX
    best = (0, 0)
    start = None
    for index, value in enumerate(list(bright) + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start > best[1] - best[0]:
                best = (start, index)
            start = None
    lo, hi = best
    if hi - lo < size * STAGE_MIN_CONTENT:
        return 0, 0

    def gutter(segment: np.ndarray) -> bool:
        return segment.size > 0 and float(np.min(segment)) >= STAGE_GUTTER_MIN

    head = lo if (size * STAGE_MIN_SIDE <= lo <= size * STAGE_MAX_SIDE
                  and gutter(dark[max(0, lo - 3):lo])) else 0
    tail_size = size - hi
    tail = tail_size if (size * STAGE_MIN_SIDE <= tail_size <= size * STAGE_MAX_SIDE
                         and gutter(dark[hi:hi + 3])) else 0
    return head, tail


def detect_stage(frames: Sequence[np.ndarray]) -> tuple[int, int, int, int]:
    """‏(يسار، يمين، أعلى، أسفل) تُقصّ حول الشاشة المشارَكة — انظر أعلاه."""
    usable = [f for f in frames if f is not None and getattr(f, "size", 0)]
    if len(usable) < MIN_FRAMES:
        return 0, 0, 0, 0
    shape = usable[0].shape[:2]
    if any(f.shape[:2] != shape for f in usable):
        return 0, 0, 0, 0
    grays = [f.mean(axis=2) if f.ndim == 3 else f for f in usable]
    masks = [g < STAGE_DARK_LEVEL for g in grays]
    columns = np.median(np.stack([m.mean(axis=0) for m in masks]), axis=0)
    rows = np.median(np.stack([m.mean(axis=1) for m in masks]), axis=0)
    left, right = _stage_cut(columns)
    top, bottom = _stage_cut(rows)
    return left, right, top, bottom


def _row_means(frame: np.ndarray) -> np.ndarray:
    """متوسّط كل صفّ بالرمادي — تمثيلٌ يكفي لكشف الثبات ورخيص."""
    if frame.ndim == 3:
        frame = frame.mean(axis=2)
    return frame.mean(axis=1)


def detect(frames: Sequence[np.ndarray]) -> CropBox:
    """يحدّد الصفوف الثابتة أعلى الشاشة وأسفلها عبر إطارات متباعدة."""
    usable = [f for f in frames if f is not None and getattr(f, "size", 0)]
    if len(usable) < MIN_FRAMES:
        return CropBox()

    height = usable[0].shape[0]
    if any(f.shape[0] != height for f in usable):
        return CropBox(height=height)

    profiles = np.stack([_row_means(f) for f in usable])
    # أقصى فرق لكل صفّ عبر الإطارات: الصفّ الذي لم يتحرّك إطلاقًا زينة.
    spread = profiles.max(axis=0) - profiles.min(axis=0)
    static = spread < STATIC_ROW_TOLERANCE

    # من الأعلى: أطول سلسلة ثابتة متّصلة تبدأ من الصفّ صفر.
    top = 0
    while top < height and static[top]:
        top += 1
    bottom = 0
    while bottom < height and static[height - 1 - bottom]:
        bottom += 1

    top = min(top, int(height * MAX_TOP_RATIO))
    bottom = min(bottom, int(height * MAX_BOTTOM_RATIO))

    # قصٌّ تافه لا يستحقّ تعقيد المسار — ولا يزيل شريطًا حقيقيًّا.
    if top < height * 0.02:
        top = 0
    if bottom < height * 0.01:
        bottom = 0

    # الحارس الفاصل — انظر ``MAX_STATIC_RATIO``.
    if static.mean() > MAX_STATIC_RATIO:
        top = bottom = 0

    # منصّة الاجتماع: تُقاس داخل ما تبقّى بعد قصّ الزينة.
    width = usable[0].shape[1]
    inner = [f[top:height - bottom if bottom else height] for f in usable]
    left, right, stage_top, stage_bottom = detect_stage(inner)
    top += stage_top
    bottom += stage_bottom
    return CropBox(top=top, bottom=bottom, height=height,
                   left=left, right=right, width=width)


# ---------------------------------------------------------------------
# شريط المتصفّح داخل الشاشة المشارَكة
# ---------------------------------------------------------------------
# **التسريب الثالث، على التسجيل نفسه:** بعد قصّ منصّة Teams بقي أعلى
# الشاشة المشارَكة شريطُ العنوان برابط النظام ومعرّفاته
# (``…/EvaluationPage.aspx?AssignedStepID=58402``) وشريط المفضّلة
# بروابط المحاضر («Master S»، «SchoolMessenger»، «ClearAllCache»).
#
# الصفوف الثابتة لا تكشفه: الرابط وعناوين التبويبات تتغيّر بين الإطارات.
# ولا الثبات على مستوى البكسل: ترويسة التطبيق تحته («Perform» الزرقاء)
# ثابتةٌ مثله في تسجيلٍ لتطبيقٍ واحد، فيُقصّ التطبيق معه.
#
# **الفاصل الوحيد الموثوق نصّيّ: سطرٌ فيه رابط.** نقرأ أعلى الإطار (OCR
# محلّي)، ونجد سطر الرابط، ونضمّ إليه سطر المفضّلة الملاصق له، ثم نقصّ
# عند أول حدٍّ أفقيّ عريض تحته — بداية الصفحة. بلا Tesseract لا قصّ.

#: أعلى الإطار الذي يُقرأ بحثًا عن الرابط، وأقصى ما يُقصّ.
BROWSER_SEARCH_RATIO = 0.25
BROWSER_MAX_RATIO = 0.18
_URL_TOKEN = None


def _url_like(text: str) -> bool:
    import re

    global _URL_TOKEN
    if _URL_TOKEN is None:
        _URL_TOKEN = re.compile(
            r"(?:https?:|www\.|[a-z0-9-]\.(?:com|net|org|edu|gov|sa|io|co)\b|"
            r"[a-z0-9-]+\.[a-z0-9.-]+/[\w./?=&%-]*)", re.I)
    return bool(_URL_TOKEN.search(text or ""))


def _ocr_lines(image: np.ndarray) -> list[tuple[int, int, str]]:
    """‏(أعلى، أسفل، نصّ) لكل سطر يقرؤه Tesseract في الصورة."""
    import tempfile

    import cv2

    from video.ocr import extract_text_and_words, is_available

    if not is_available():
        return []
    with tempfile.TemporaryDirectory(prefix="vtad_chrome_") as workspace:
        path = Path(workspace) / "top.png"
        cv2.imwrite(str(path), image)
        _text, words = extract_text_and_words(path, timeout_seconds=20.0)
    lines: dict = {}
    for word in words:
        lines.setdefault(word["line"], []).append(word)
    result = []
    for items in lines.values():
        top = min(w["top"] for w in items)
        bottom = max(w["top"] + w["height"] for w in items)
        result.append((int(top), int(bottom), " ".join(w["text"] for w in items)))
    return sorted(result)


def _browser_bottom(image: np.ndarray,
                    lines: Optional[list] = None) -> int:
    """الصفّ الذي تبدأ عنده الصفحة تحت شريط المتصفّح، أو 0."""
    height = image.shape[0]
    search = image[:max(1, int(height * BROWSER_SEARCH_RATIO))]
    if lines is None:
        lines = _ocr_lines(search)
    urls = [(t, b) for t, b, text in lines
            if _url_like(text) and t < height * BROWSER_MAX_RATIO]
    if not urls:
        return 0
    top, bottom = urls[-1]
    line_height = max(6, bottom - top)
    # شريط المفضّلة: سطرٌ ملاصقٌ تحت الرابط فيه عناصر كثيرة.
    for t, b, text in lines:
        if bottom <= t <= bottom + 1.6 * line_height and len(text.split()) >= 4:
            bottom = max(bottom, b)
    # أول حدٍّ أفقيّ عريض تحت الشريط: بداية الصفحة.
    gray = search.mean(axis=2) if search.ndim == 3 else search.astype(float)
    limit = min(len(gray) - 1, int(bottom + 0.08 * height))
    boundary = 0
    for y in range(bottom + 2, limit + 1):
        changed = (np.abs(gray[y] - gray[y - 1]) > 10).mean()
        if changed >= 0.5:
            boundary = y
            break
    if not boundary:
        boundary = min(len(gray), bottom + max(3, line_height // 2))
    return boundary if boundary <= height * BROWSER_MAX_RATIO else 0


def detect_browser_chrome(frames: Sequence[np.ndarray], samples: int = 3) -> int:
    """كم صفًّا يُقصّ من أعلى الإطار لإزالة شريط المتصفّح (0 = لا شيء)."""
    usable = [f for f in frames if f is not None and getattr(f, "size", 0)]
    if not usable:
        return 0
    step = max(1, len(usable) // samples)
    found = []
    for frame in usable[::step][:samples]:
        try:
            value = _browser_bottom(frame)
        except Exception as exc:                        # noqa: BLE001
            logger.debug(f"تعذّر كشف شريط المتصفّح: {exc}")
            value = 0
        if value:
            found.append(value)
    # إطارٌ واحد من ثلاثة قد يقرأ رابطًا داخل الصفحة نفسها: نطلب أغلبية.
    needed = 1 if len(usable[::step][:samples]) == 1 else 2
    if len(found) < needed:
        return 0
    return int(max(found))


def detect_from_video(video_path: Path, duration: float,
                      ffmpeg, samples: int = 6) -> CropBox:
    """يلتقط إطارات متباعدة من التسجيل ويستنتج منها القصّ.

    التباعد شرط: إطارات متجاورة تتشابه فيبدو نصفُ الشاشة ثابتًا.
    """
    import tempfile

    import cv2

    if duration <= 0:
        return CropBox()
    moments = [duration * (index + 1) / (samples + 1) for index in range(samples)]
    frames: List[np.ndarray] = []
    with tempfile.TemporaryDirectory(prefix="vtad_crop_") as workspace:
        for index, moment in enumerate(moments):
            target = Path(workspace) / f"{index}.png"
            try:
                ffmpeg.extract_frame(video_path, moment, target)
                image = cv2.imread(str(target))
            except Exception:
                continue
            if image is not None:
                frames.append(image)
    box = detect(frames)
    if frames:
        try:
            chrome = detect_browser_chrome([box.apply(f) for f in frames])
        except Exception as exc:                        # noqa: BLE001
            logger.debug(f"تعذّر كشف شريط المتصفّح: {exc}")
            chrome = 0
        if chrome:
            box.top += chrome
            box.height = box.height or frames[0].shape[0]
            box.width = box.width or frames[0].shape[1]
            logger.info(f"شريط المتصفّح: يُقصّ {chrome}px إضافية من أعلى الشاشة المشارَكة.")
    if box.active:
        logger.info(f"زينة الشاشة: يُقصّ {box.describe()} من كل لقطة.")
    return box


def manual_box(height: int, top_ratio: float = 0.0,
               bottom_ratio: float = 0.0) -> CropBox:
    """قصّ يدويّ بالنِّسب — لمن يعرف تخطيط شاشته ولا يريد الاستنتاج."""
    return CropBox(top=int(max(0.0, min(0.4, top_ratio)) * height),
                   bottom=int(max(0.0, min(0.3, bottom_ratio)) * height),
                   height=height)


def crop_file(path: Path, box: Optional[CropBox]) -> bool:
    """يقصّ صورة محفوظة في مكانها. يعيد ``True`` إن تغيّرت."""
    if box is None or not box.active:
        return False
    import cv2

    image = cv2.imread(str(path))
    if image is None or image.shape[0] != box.height or not box.matches(image):
        return False
    cropped = box.apply(image)
    if (cropped.shape[0] < image.shape[0] * 0.5
            or cropped.shape[1] < image.shape[1] * 0.5):
        # حارسٌ ثانٍ: قصٌّ يأكل نصف الصورة خطأٌ مهما قال الكاشف.
        return False
    cv2.imwrite(str(path), cropped,
                [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return True
