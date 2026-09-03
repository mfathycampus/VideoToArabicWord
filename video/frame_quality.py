"""مقاييس جودة الإطار واختيار اللحظة المستقرة.

سبب وجود هذه الوحدة — من فحص مخرج حقيقي (تسجيل شاشة 88 ثانية لنظام
ويب): أنتج التطبيق 13 صورة، **ثلاث منها فارغة تمامًا** (صفحة بيضاء أثناء
التحميل) واثنتان تُظهران مربّع «Loading».

السبب الجذري: كاشف المشاهد يُطلق **عند لحظة التغيّر**، وهي بالضبط لحظة
بدء تحميل الصفحة. ثم يأخذ المنتقي إطارًا عند نسبة ثابتة من المشهد
(35%)، فيقع داخل فترة التحميل.

فحص الجودة القديم (سطوع + تباين Laplacian) لا يمسك هذه الحالة: الصفحة
البيضاء ساطعة، وفيها شريط عنوان يكفي لرفع التباين.

المقياس الحاسم المقاس على تلك الصور:

    نسبة الحواف   الإطارات الفارغة/التحميل: 1.01% – 1.25%
                  الإطارات المفيدة:         1.96% – 6.94%

فصل واضح. لذلك: **كثافة المحتوى** ترفض الفارغ، و**الاستقرار** يضمن
التقاط الشاشة بعد أن تستقر لا أثناء انتقالها.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class FrameQuality:
    """مقاييس إطار واحد. كلها مطبّعة أو بوحدات مفهومة."""
    brightness: float          # 0-255
    contrast: float            # الانحراف المعياري
    sharpness: float           # تباين Laplacian
    content_density: float     # نسبة بكسلات الحواف [0,1] — كاشف الفراغ
    dominant_color_ratio: float  # نسبة اللون الأكثر شيوعًا [0,1]
    entropy: float             # 0-8 بت

    def is_blank(self, min_content: float, max_dominant: float) -> bool:
        """صفحة بيضاء أو شاشة تحميل: محتوى ضئيل أو لون واحد يغلب."""
        return (self.content_density < min_content
                or self.dominant_color_ratio > max_dominant)

    def score(self) -> float:
        """درجة مركّبة لترجيح إطار على آخر داخل المشهد نفسه."""
        # كثافة المحتوى هي الإشارة الأقوى، والحدّة تكسر التعادل
        return (self.content_density * 100.0
                + min(self.entropy, 8.0) * 0.5
                + min(self.sharpness / 1000.0, 3.0))


def measure(frame_bgr: np.ndarray) -> FrameQuality:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    total = float(histogram.sum()) or 1.0
    probabilities = histogram / total
    nonzero = probabilities[probabilities > 0]

    return FrameQuality(
        brightness=float(np.mean(gray)),
        contrast=float(np.std(gray)),
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        content_density=float(np.count_nonzero(edges)) / float(edges.size),
        dominant_color_ratio=float(histogram.max() / total),
        entropy=float(-(nonzero * np.log2(nonzero)).sum()),
    )


def difference(a_bgr: np.ndarray, b_bgr: np.ndarray) -> float:
    """فرق بصري سريع بين إطارين — لقياس استقرار الشاشة."""
    size = (256, 144)
    a = cv2.cvtColor(cv2.resize(a_bgr, size, interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(cv2.resize(b_bgr, size, interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY)
    return float(np.mean(cv2.absdiff(a, b))) / 255.0


def dhash(frame_bgr: np.ndarray, size: int = 16) -> np.ndarray:
    """بصمة إدراكية (فرق التدرّج) لكشف الصور شبه المتطابقة.

    أدق من متوسط الفرق المطلق: لا تتأثر بفروق السطوع العامة، وتقارن
    البنية لا القيم.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    return (resized[:, 1:] > resized[:, :-1]).flatten()


def hamming_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """تشابه بصمتين في [0,1]."""
    if a.shape != b.shape:
        return 0.0
    return float(np.count_nonzero(a == b)) / float(a.size)
