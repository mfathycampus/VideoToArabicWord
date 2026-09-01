"""قراءة الإطارات مع حل مشكلة الدوران حلًا لا يعتمد على إصدار OpenCV.

المشكلة:
    بعض بُنى OpenCV تطبّق مصفوفة عرض الحاوية تلقائيًا، وبعضها يتجاهلها.
    الفرق يعتمد على إصدار OpenCV وعلى backend الـ FFmpeg المبني معه، ولا
    توجد راية موثوقة للاستعلام عنه.

    تصحيح الدوران دائمًا  ⇒ صور مقلوبة على البُنى الحديثة (دوران مزدوج).
    عدم التصحيح إطلاقًا    ⇒ صور مقلوبة على البُنى القديمة.

الحل:
    نستنتج السلوك وقت التشغيل بمقارنة الإطار المفكوك بحقيقة مرجعية:

    1. عند دوران 90/270، أبعاد الإطار وحدها تحسم الأمر: إن طابقت الأبعاد
       المعروضة فقد طبّق OpenCV الدوران، وإن طابقت المخزَّنة فلم يطبّقه.
    2. عند دوران 180 (أو فيديو مربّع) لا تتغير الأبعاد، فنستخرج إطارًا
       مرجعيًا بـ FFmpeg — وهو يحترم مصفوفة العرض دائمًا — ونختار زاوية
       التصحيح التي تطابقه.

    القرار يُتَّخذ مرة واحدة لكل فيديو ثم يُخزَّن.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from utils.logger import logger

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def apply_rotation(frame: np.ndarray, rotation: int) -> np.ndarray:
    """يدوّر إطارًا بزاوية من مضاعفات 90."""
    code = _ROTATIONS.get(rotation % 360)
    return cv2.rotate(frame, code) if code is not None else frame


@dataclass
class RotationPlan:
    """زاوية التصحيح الواجب تطبيقها على إطارات OpenCV (0 = لا شيء)."""
    correction: int = 0
    reason: str = "no_rotation_metadata"

    def apply(self, frame: np.ndarray) -> np.ndarray:
        return apply_rotation(frame, self.correction) if self.correction else frame


def _shape_matches(frame: np.ndarray, width: int, height: int) -> bool:
    return frame.shape[1] == width and frame.shape[0] == height


def resolve_rotation_plan(
    video_path: Path,
    declared_rotation: int,
    stored_width: int,
    stored_height: int,
    display_width: int,
    display_height: int,
    sample_frame: Optional[np.ndarray] = None,
    ffmpeg=None,
) -> RotationPlan:
    """يقرر ما إذا كان يجب تدوير إطارات OpenCV، وبأي زاوية."""
    if not declared_rotation % 360:
        return RotationPlan(0, "no_rotation_metadata")

    if sample_frame is None:
        capture = cv2.VideoCapture(str(video_path))
        try:
            ok, sample_frame = capture.read()
        finally:
            capture.release()
        if not ok or sample_frame is None:
            return RotationPlan(declared_rotation, "unreadable_sample_assume_manual")

    # --- الحالة 1: الأبعاد تحسم (دوران 90/270 على فيديو غير مربّع) ---
    if (stored_width, stored_height) != (display_width, display_height):
        if _shape_matches(sample_frame, display_width, display_height):
            return RotationPlan(0, "opencv_applied_rotation")
        if _shape_matches(sample_frame, stored_width, stored_height):
            return RotationPlan(declared_rotation, "opencv_ignored_rotation")

    # --- الحالة 2: الأبعاد لا تحسم (180 أو مربّع) ⇒ قارن بمرجع FFmpeg ---
    if ffmpeg is not None:
        try:
            reference_path = video_path.parent / f".__rotref_{video_path.stem}.png"
            ffmpeg.extract_frame(video_path, 0.0, reference_path)
            reference = cv2.imread(str(reference_path))
            reference_path.unlink(missing_ok=True)
            if reference is not None:
                best_angle, best_score = 0, float("inf")
                for angle in (0, declared_rotation % 360):
                    candidate = apply_rotation(sample_frame, angle)
                    if candidate.shape != reference.shape:
                        continue
                    score = float(np.mean(np.abs(
                        candidate.astype(np.int16) - reference.astype(np.int16))))
                    if score < best_score:
                        best_angle, best_score = angle, score
                logger.debug(f"مرجع الدوران: أفضل زاوية {best_angle} (فرق {best_score:.1f})")
                return RotationPlan(
                    best_angle,
                    "matched_ffmpeg_reference" if best_angle
                    else "opencv_applied_rotation")
        except Exception as exc:
            logger.warning(f"تعذر التحقق من الدوران عبر FFmpeg: {exc}")

    # --- افتراض متحفظ: طبّق الدوران المصرَّح به ---
    return RotationPlan(declared_rotation, "fallback_assume_manual")
