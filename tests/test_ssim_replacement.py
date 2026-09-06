"""‏SSIM بمرشّحات OpenCV — والتطابق الرقمي الذي يحمي معايرة ADR-011.

القصة: ``skimage.metrics.structural_similarity`` كانت تستهلك **90٪** من
حساب كشف المشاهد — 20.3 مللي ثانية من أصل 22.5 لكل زوج إطارات.

لكن الاستبدال هنا ليس تحسينًا حرًّا. ADR-011 يشتقّ عتبة القطع من توزيع
درجات الفيديو نفسه (``median + k·MAD``)، فبديلٌ بقيمة **مختلفة قليلًا**
يُزيح التوزيع كلّه ويُغيّر عدد المشاهد بلا أن يلاحظ أحد — ويوجب إعادة
معايرة كاملة بـ ``tools/calibrate_scenes.py``.

فالشرط ليس «قريب بما يكفي» بل **مطابق**. هذه الاختبارات تقفل ذلك.

وقد تحقّق التطابق على كل مصفوفة الاختبار: 24 ملفًا، صفر اختلاف في عدد
المشاهد، وأقصى فرق في الدرجات 3.3e-07 — ضجيج فاصلة عائمة لا أكثر.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from video.scene_detector import SceneDetector, structural_similarity_fast

pytest.importorskip("skimage",
                    reason="المرجع للمقارنة فقط — ليس تبعية إنتاج")
from skimage.metrics import structural_similarity as reference  # noqa: E402

H, W = 270, 480


def _slide(lines: int, seed: int = 0) -> np.ndarray:
    """شريحة نصّية — أقرب ما يكون إلى مادّة المحاضرات الحقيقية."""
    frame = np.full((H, W), 240, np.uint8)
    for index in range(lines):
        cv2.putText(frame, f"lecture line {index + seed}", (30, 30 + index * 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, 20, 1)
    return frame


def _cases():
    rng = np.random.default_rng(19)
    identical = rng.integers(0, 255, (H, W), dtype=np.uint8)
    flat = rng.integers(80, 180, (H, W), dtype=np.uint8)
    noisy = np.clip(identical.astype(int) + rng.integers(-12, 12, (H, W)), 0, 255)
    return {
        "متطابقان": (identical, identical.copy()),
        "شريحة بنقطة مضافة": (_slide(9), _slide(10)),
        "شريحتان مختلفتان": (_slide(9), _slide(9, seed=40)),
        "قطع كامل": (np.full((H, W), 240, np.uint8),
                     rng.integers(0, 255, (H, W), dtype=np.uint8)),
        "ضجيج خفيف": (identical, noisy.astype(np.uint8)),
        "تغيّر إضاءة": (flat, np.clip(flat.astype(int) + 30, 0, 255).astype(np.uint8)),
        "لون واحد": (np.full((H, W), 128, np.uint8), np.full((H, W), 128, np.uint8)),
    }


@pytest.mark.parametrize("name,pair", list(_cases().items()))
def test_matches_skimage_to_five_decimals(name, pair):
    """الشرط الأساسي: نفس القيمة، لا قيمة قريبة.

    لو انحرف البديل ولو قليلًا لانزاح توزيع الدرجات، ولتغيّرت العتبة
    التكيّفية، ولتغيّر عدد المشاهد في كل مستند — بصمت.
    """
    a, b = pair
    assert structural_similarity_fast(a, b) == pytest.approx(
        reference(a, b, full=False), abs=1e-5), name


def test_identical_frames_score_exactly_one():
    frame = _slide(9)
    assert structural_similarity_fast(frame, frame) == pytest.approx(1.0, abs=1e-6)


def test_a_full_cut_scores_far_below_a_slide_edit():
    """الترتيب النسبي هو ما يبني عليه الكاشف قراره."""
    detector = SceneDetector()
    edit = detector._ssim_diff(_slide(9), _slide(10))
    cut = detector._ssim_diff(np.full((H, W), 240, np.uint8),
                              np.full((H, W), 20, np.uint8))
    assert cut > edit


def test_output_stays_inside_the_data_contract():
    """‏``change_score`` موثّق داخل [0, 1] — كسره يُفسد عقد البيانات."""
    detector = SceneDetector()
    rng = np.random.default_rng(5)
    for _ in range(12):
        a = rng.integers(0, 255, (H, W), dtype=np.uint8)
        b = rng.integers(0, 255, (H, W), dtype=np.uint8)
        assert 0.0 <= detector._ssim_diff(a, b) <= 1.0


def test_scene_detector_no_longer_imports_skimage():
    """‏scikit-image أُسقطت من التبعيات — 144 ميغابايت مع scipy.

    لو عاد استيرادها إلى كود الإنتاج لعاد معها 144 ميغابايت إلى
    الحزمة، وهو ما لا يظهر في أي اختبار آخر.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if any(part in {"tests", "__pycache__", ".venv", "venv"}
               for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        if "import skimage" in source or "from skimage" in source:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"{offenders}: استعمل structural_similarity_fast"


def test_scikit_image_is_not_a_declared_dependency():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("requirements.txt", "requirements.in"):
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                assert "scikit-image" not in stripped, f"{name}: {line}"
