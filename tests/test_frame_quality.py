"""جودة الإطارات — انحدار على مخرج حقيقي فاسد.

تسجيل شاشة 88 ثانية أنتج 13 صورة بحجم 8.5 ميجابايت، **ثلاث منها فارغة**
(صفحة بيضاء أثناء التحميل) واثنتان بمربّع «Loading».
"""
import numpy as np
import pytest

from config.settings import KeyframeConfig
from video.frame_quality import (
    dhash, difference, hamming_similarity, measure,
)


def blank_frame(shade=250, size=(720, 1280)):
    frame = np.full((size[0], size[1], 3), shade, np.uint8)
    frame[:60] = (120, 60, 60)          # شريط عنوان فقط — كصفحة تُحمّل
    return frame


def content_frame(seed=1, size=(720, 1280)):
    rng = np.random.default_rng(seed)
    frame = np.full((size[0], size[1], 3), 248, np.uint8)
    frame[:60] = (120, 60, 60)
    for i in range(14):
        y = 90 + i * 40
        w = int(rng.integers(200, 1000))
        frame[y:y + 16, 60:60 + w] = 35
    return frame


def test_blank_page_is_detected():
    config = KeyframeConfig()
    quality = measure(blank_frame())
    assert quality.is_blank(config.min_content_density,
                            config.max_dominant_color_ratio)


def test_content_page_is_not_blank():
    config = KeyframeConfig()
    quality = measure(content_frame())
    assert not quality.is_blank(config.min_content_density,
                                config.max_dominant_color_ratio)


def test_content_scores_far_above_blank():
    """الفصل يجب أن يكون واسعًا لا هامشيًا."""
    blank = measure(blank_frame()).score()
    content = measure(content_frame()).score()
    assert content > blank * 2.0, f"فصل ضعيف: {blank:.2f} مقابل {content:.2f}"


def test_sparse_slide_is_not_rejected():
    """شريحة عرض بسيطة (عنوان وثلاث نقاط) محتوى سليم لا فراغ.

    حدّ مطلق عالٍ كان يرفضها ويُفقد المستند صورًا صحيحة.
    """
    config = KeyframeConfig()
    frame = np.full((720, 1280, 3), 250, np.uint8)
    frame[80:130, 200:900] = 40                    # عنوان
    for i in range(3):
        frame[260 + i * 90:280 + i * 90, 240:800] = 60   # ثلاث نقاط
    quality = measure(frame)
    assert not quality.is_blank(config.min_content_density,
                                config.max_dominant_color_ratio), \
        f"رُفضت شريحة سليمة (كثافة={quality.content_density:.4f})"


def test_uniform_frame_is_blank():
    config = KeyframeConfig()
    quality = measure(np.full((720, 1280, 3), 235, np.uint8))
    assert quality.is_blank(config.min_content_density,
                            config.max_dominant_color_ratio)


def test_difference_detects_change_and_ignores_identity():
    a, b = content_frame(1), content_frame(2)
    assert difference(a, a) == pytest.approx(0.0, abs=1e-6)
    assert difference(a, b) > 0.01


def test_dhash_matches_itself_and_separates_others():
    a, b = content_frame(1), content_frame(2)
    assert hamming_similarity(dhash(a), dhash(a)) == 1.0
    assert hamming_similarity(dhash(a), dhash(b)) < 0.98


def test_dhash_ignores_uniform_brightness_shift():
    """بصمة إدراكية تقارن البنية لا القيم المطلقة."""
    a = content_frame(3)
    brighter = np.clip(a.astype(np.int16) + 18, 0, 255).astype(np.uint8)
    assert hamming_similarity(dhash(a), dhash(brighter)) > 0.95


def test_defaults_favour_compact_output():
    """PNG بعرض كامل أنتج 8.5MB لـ 88 ثانية — محاضرة ساعة تتجاوز 300MB."""
    config = KeyframeConfig()
    assert config.format.lower() in ("jpg", "jpeg")
    assert config.max_width <= 1600
    assert 70 <= config.jpg_quality <= 92
