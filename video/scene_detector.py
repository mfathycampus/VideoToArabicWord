"""كشف المشاهد بإشارات مطبّعة وعتبة تكيّفية.

ما الذي تغيّر عن النسخة الأولى ولماذا:

1. **العتبة**: كانت ``score > 0.35`` مطلقة و ``major = 0.85``.
   القياس الفعلي لانتقال شريحة كامل بالمعادلة القديمة = **0.073**،
   أي أن الكاشف لا يُطلق إطلاقًا على محتوى حقيقي. العتبة الآن تُشتق من
   توزيع الدرجات داخل الفيديو نفسه (median + k·MAD) مع حدّ أدنى مطلق.

2. **التطبيع**: ``1 - compareHist(CORREL)`` مداه [0, 2] وليس [0, 1]،
   فكان ``change_score`` يكسر عقد البيانات. استُبدل بـ Bhattacharyya
   ومداه [0, 1] أصلًا، وعلى هيستوجرام ملوّن (HS) لأن الهيستوجرام الرمادي
   يعطي 0.000 بين شريحتين نصيتين مختلفتين تمامًا.

3. **القراءة**: ``cap.set(CAP_PROP_POS_FRAMES)`` داخل الحلقة يفرض بحثًا
   عشوائيًا وفك تشفير من أقرب keyframe في كل تكرار (~10,800 مرة لمحاضرة
   ساعة). استُبدل بـ ``grab()`` التسلسلي الرخيص + ``retrieve()`` انتقائي.

4. **الدلالة**: المشهد الآن ``[بداية, نهاية]`` صحيحة، و ``change_score``
   يصف القطع الذي **بدأ** المشهد، لا الذي أنهاه.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

from config.settings import SceneDetectionConfig
from core.exceptions import MediaValidationError
from utils.cancellation import CancellationToken
from utils.frames import RotationPlan


@dataclass
class Scene:
    scene_id: int
    start: float
    end: float
    change_score: float          # درجة القطع الذي *بدأ* هذا المشهد
    representative_timestamp: float
    is_major: bool = False


@dataclass
class _Sample:
    timestamp: float
    score: float
    gray: np.ndarray = field(repr=False, default=None)


class SceneDetector:
    def __init__(self, config: Optional[SceneDetectionConfig] = None) -> None:
        self.config = config or SceneDetectionConfig()

    # ------------------------------------------------------------------
    # الإشارات — كل واحدة مطبّعة إلى [0, 1]
    # ------------------------------------------------------------------
    def _ssim_diff(self, a_gray: np.ndarray, b_gray: np.ndarray) -> float:
        # SSIM ∈ [-1, 1] ⇒ التطبيع بالقسمة على 2
        score = ssim(a_gray, b_gray, full=False)
        return float(np.clip((1.0 - score) / 2.0, 0.0, 1.0))

    def _hist_diff(self, a_bgr: np.ndarray, b_bgr: np.ndarray) -> float:
        # هيستوجرام HS ملوّن؛ الرمادي يعطي 0.0 بين شريحتين نصيتين مختلفتين
        ha = cv2.calcHist([cv2.cvtColor(a_bgr, cv2.COLOR_BGR2HSV)], [0, 1],
                          None, [32, 32], [0, 180, 0, 256])
        hb = cv2.calcHist([cv2.cvtColor(b_bgr, cv2.COLOR_BGR2HSV)], [0, 1],
                          None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(ha, ha, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hb, hb, 0, 1, cv2.NORM_MINMAX)
        # Bhattacharyya ∈ [0, 1] بحكم التعريف
        return float(np.clip(cv2.compareHist(ha, hb, cv2.HISTCMP_BHATTACHARYYA), 0.0, 1.0))

    def _pixel_ratio(self, a_gray: np.ndarray, b_gray: np.ndarray) -> float:
        # نسبة البكسلات المتغيّرة فعليًا — أكثر تمييزًا من متوسط الفرق المطلق
        delta = cv2.absdiff(a_gray, b_gray)
        changed = delta > self.config.pixel_change_threshold
        return float(np.count_nonzero(changed)) / float(delta.size)

    def _edge_diff(self, a_gray: np.ndarray, b_gray: np.ndarray) -> float:
        # IoU معكوس على خرائط الحواف: حسّاس جدًا لتغيّر نص الشرائح
        ea = cv2.Canny(a_gray, 80, 180) > 0
        eb = cv2.Canny(b_gray, 80, 180) > 0
        union = np.count_nonzero(ea | eb)
        if union == 0:
            return 0.0
        intersection = np.count_nonzero(ea & eb)
        return float(1.0 - intersection / union)

    def compute_change_score(self, a_bgr: np.ndarray, b_bgr: np.ndarray) -> float:
        """درجة تغيّر مركّبة مضمونة داخل [0, 1]."""
        a_gray = cv2.cvtColor(a_bgr, cv2.COLOR_BGR2GRAY)
        b_gray = cv2.cvtColor(b_bgr, cv2.COLOR_BGR2GRAY)
        cfg = self.config
        score = (
            cfg.ssim_weight * self._ssim_diff(a_gray, b_gray)
            + cfg.histogram_weight * self._hist_diff(a_bgr, b_bgr)
            + cfg.pixel_weight * self._pixel_ratio(a_gray, b_gray)
            + cfg.edge_weight * self._edge_diff(a_gray, b_gray)
        )
        total_weight = (cfg.ssim_weight + cfg.histogram_weight
                        + cfg.pixel_weight + cfg.edge_weight)
        return float(np.clip(score / total_weight, 0.0, 1.0)) if total_weight else 0.0

    # ------------------------------------------------------------------
    # العتبة التكيّفية
    # ------------------------------------------------------------------
    def compute_threshold(self, scores: List[float]) -> float:
        """يشتق العتبة من توزيع درجات هذا الفيديو تحديدًا.

        MAD مقاوم للقيم الشاذة (القطعات نفسها)، بعكس الانحراف المعياري
        الذي ترفعه القطعات فتخفي نفسها.
        """
        if len(scores) < 4:
            return self.config.absolute_floor
        median = statistics.median(scores)
        mad = statistics.median([abs(s - median) for s in scores])
        scaled_mad = mad * 1.4826  # معامل التوافق مع الانحراف المعياري الطبيعي
        adaptive = median + self.config.adaptive_k * scaled_mad
        return max(self.config.absolute_floor, adaptive)

    # ------------------------------------------------------------------
    def detect(
        self,
        video_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        fps_hint: Optional[float] = None,
        duration_hint: Optional[float] = None,
        rotation_plan: Optional[RotationPlan] = None,
    ) -> List[Scene]:
        """يكتشف المشاهد.

        ``fps_hint`` / ``duration_hint`` يأتيان من ffprobe لأن قيم OpenCV
        غير موثوقة على VFR وعلى الحاويات بلا فهرس (MPEG-TS مثلًا).
        ``rotation_plan`` يحمل زاوية التصحيح المستنتجة وقت التشغيل — بعض
        بُنى OpenCV تطبّق دوران الحاوية وبعضها لا، فلا يصح افتراض أيهما.
        """
        rotation_plan = rotation_plan or RotationPlan(0, "none")
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise MediaValidationError(f"تعذر فتح الفيديو: {video_path}")

        try:
            fps = fps_hint if (fps_hint and fps_hint > 0) else cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps <= 0 or fps != fps:  # 0 أو NaN لبعض الحاويات
                raise MediaValidationError(
                    "معدل إطارات غير صالح؛ اقرأ fps من ffprobe ومرّره صراحةً.")
            frame_step = max(1, int(round(fps / self.config.analysis_fps)))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = (duration_hint if (duration_hint and duration_hint > 0)
                        else (total_frames / fps if total_frames > 0 else 0.0))
            size = (self.config.analysis_width, self.config.analysis_height)

            samples: List[_Sample] = []
            prev_small: Optional[np.ndarray] = None
            frame_idx = 0
            last_emit = -1e9   # خنق إشارات التقدّم

            # ---- المرور الأول: حساب الدرجات بقراءة تسلسلية ----
            while True:
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                    cancel_token.wait_if_paused()

                # grab() يتقدّم دون فك تشفير كامل ولا نسخ إلى numpy
                if not cap.grab():
                    break

                if frame_idx % frame_step == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    frame = rotation_plan.apply(frame)
                    small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
                    timestamp = frame_idx / fps
                    if prev_small is not None:
                        samples.append(_Sample(
                            timestamp=timestamp,
                            score=self.compute_change_score(prev_small, small),
                        ))
                    prev_small = small

                    # خنق: إشارة كل ثانيتين من زمن الفيديو، لا كل عيّنة —
                    # وإلا أغرقنا حلقة أحداث Qt بآلاف الإشارات
                    if (progress_callback and duration > 0
                            and timestamp - last_emit >= 2.0):
                        last_emit = timestamp
                        progress_callback(
                            min(100.0, timestamp / duration * 100.0),
                            f"تحليل المشاهد: {timestamp:.0f}s / {duration:.0f}s")
                frame_idx += 1

            if duration <= 0.0:
                duration = frame_idx / fps
        finally:
            cap.release()

        return self._build_scenes(samples, duration)

    # ------------------------------------------------------------------
    def _build_scenes(self, samples: List[_Sample], duration: float) -> List[Scene]:
        """يحوّل الدرجات إلى مشاهد بتطبيق العتبة والقواعد الزمنية."""
        if not samples:
            return [Scene(1, 0.0, duration, 0.0, duration / 2.0)]

        threshold = self.compute_threshold([s.score for s in samples])
        major_threshold = threshold * self.config.major_multiplier

        # نقاط القطع
        cuts: List[tuple[float, float, bool]] = []  # (زمن, درجة, حاد؟)
        last_cut_time = 0.0
        for sample in samples:
            is_major = sample.score >= major_threshold
            gap_ok = (sample.timestamp - last_cut_time) >= self.config.minimum_gap_seconds
            if sample.score < threshold:
                continue
            if gap_ok or (is_major and self.config.allow_major_change_before_gap):
                cuts.append((sample.timestamp, sample.score, is_major))
                last_cut_time = sample.timestamp

        # سقف أمان: أبقِ أقوى القطعات فقط عند التجاوز
        if len(cuts) > self.config.max_scenes:
            strongest = sorted(cuts, key=lambda c: c[1], reverse=True)[: self.config.max_scenes]
            cuts = sorted(strongest, key=lambda c: c[0])

        # بناء المشاهد: كل مشهد يمتد من قطعه حتى القطع التالي
        boundaries = [(0.0, 0.0, False)] + cuts
        scenes: List[Scene] = []
        for index, (start, score, is_major) in enumerate(boundaries):
            end = boundaries[index + 1][0] if index + 1 < len(boundaries) else duration
            if end <= start:
                continue
            scenes.append(Scene(
                scene_id=len(scenes) + 1,
                start=start,
                end=end,
                change_score=score,          # درجة القطع الذي *بدأ* المشهد
                # نقطة تمثيلية داخل المشهد، بعيدة عن حافة الانتقال
                representative_timestamp=min(start + 0.35 * (end - start), end - 0.05)
                if end - start > 0.2 else (start + end) / 2.0,
                is_major=is_major,
            ))
        return scenes
