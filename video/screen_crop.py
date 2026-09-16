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


@dataclass
class CropBox:
    top: int = 0
    bottom: int = 0          # عدد الصفوف المقصوصة من الأسفل
    height: int = 0

    @property
    def active(self) -> bool:
        return self.top > 0 or self.bottom > 0

    def apply(self, image: np.ndarray) -> np.ndarray:
        if not self.active:
            return image
        end = image.shape[0] - self.bottom if self.bottom else image.shape[0]
        return image[self.top:end]

    def describe(self) -> str:
        parts = []
        if self.top:
            parts.append(f"{self.top}px من الأعلى")
        if self.bottom:
            parts.append(f"{self.bottom}px من الأسفل")
        return "، ".join(parts) or "لا قصّ"


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
        return CropBox(height=height)

    return CropBox(top=top, bottom=bottom, height=height)


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
    if image is None or image.shape[0] != box.height:
        return False
    cropped = box.apply(image)
    if cropped.shape[0] < image.shape[0] * 0.5:
        # حارسٌ ثانٍ: قصٌّ يأكل نصف الصورة خطأٌ مهما قال الكاشف.
        return False
    cv2.imwrite(str(path), cropped,
                [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return True
