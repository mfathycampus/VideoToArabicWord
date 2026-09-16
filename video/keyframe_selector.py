"""اختيار الصور الممثلة للمشاهد.

المبدأ الذي تغيّر: لا نلتقط الإطار عند لحظة الانتقال، بل **بعد استقرار
الشاشة**. في تسجيلات الشاشة لحظة الانتقال هي بالضبط لحظة تحميل الصفحة،
فينتهي المستند بصور بيضاء ومربّعات «Loading».

الخوارزمية لكل مشهد:
    1. جرّب عدة لحظات موزّعة على المشهد، منحازة إلى نهايته.
    2. لكل لحظة: اقرأ الإطار وإطارًا بعده بقليل. الفرق بينهما = عدم
       استقرار (الشاشة ما زالت تتحرك أو تُحمّل).
    3. ارفض الإطارات الفارغة وشاشات التحميل بمقياس كثافة المحتوى.
    4. اختر أعلى درجة = محتوى غني + شاشة مستقرة.
    5. امنع التكرار ببصمة إدراكية تُقارَن بكل الصور المحفوظة، لا بالسابقة
       فقط.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np
from PIL import Image

from config.schemas import KeyframeMetadata
from config.settings import KeyframeConfig
from core.exceptions import MediaValidationError
from utils.cancellation import CancellationToken
from utils.frames import RotationPlan
from utils.logger import logger
from utils.timestamps import safe_timestamp_for_filename
from video.frame_quality import (
    FrameQuality,
    dhash,
    difference,
    hamming_similarity,
    measure,
)
from video.scene_detector import Scene


class KeyframeSelector:
    def __init__(self, config: Optional[KeyframeConfig] = None,
                 crop_box=None) -> None:
        self.config = config or KeyframeConfig()
        #: صندوق قصّ زينة الشاشة (``video/screen_crop``). يُطبَّق في
        #: ``_read_at`` — أي **قبل** فحص الجودة والبصمة و OCR والحفظ.
        #: القصّ بعد الحفظ كان سيترك شريط المهام يُحسب في بصمة التكرار،
        #: وساعةٌ تتغيّر فيه تجعل كل لقطتين مختلفتين فينجو المكرَّر.
        self.crop_box = crop_box

    # ------------------------------------------------------------------
    def _read_at(self, cap, timestamp: float, fps: float,
                 rotation_plan: RotationPlan, video_path: Optional[Path] = None,
                 ffmpeg=None, temp_dir: Optional[Path] = None
                 ) -> Optional[np.ndarray]:
        """يقرأ إطارًا، مع مسار احتياطي عبر FFmpeg.

        ضروري لا تحسينيّ: بُنى OpenCV كثيرة لا تفكّ ترميز AV1 وتعيد
        «Failed to get pixel format»، فيخرج المستند بلا صور إطلاقًا رغم
        سلامة الفيديو. FFmpeg يفكّه ويحترم الدوران أصلًا.
        """
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(timestamp * fps)))
        ok, frame = cap.read()
        if ok and frame is not None:
            return self._crop(rotation_plan.apply(frame))

        if ffmpeg is not None and video_path is not None:
            scratch = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
            scratch.mkdir(parents=True, exist_ok=True)
            target = scratch / "_probe_frame.png"
            try:
                ffmpeg.extract_frame(video_path, timestamp, target)
                frame = cv2.imread(str(target))
                return self._crop(frame)   # FFmpeg يطبّق الدوران بنفسه
            except Exception as exc:
                logger.debug(f"تعذّر استخراج الإطار عبر FFmpeg: {exc}")
            finally:
                target.unlink(missing_ok=True)
        return None

    def _crop(self, frame):
        """يقصّ زينة الشاشة إن وُجد صندوق قصّ صالح لهذا الارتفاع."""
        box = self.crop_box
        if frame is None or box is None or not getattr(box, "active", False):
            return frame
        if box.height and frame.shape[0] != box.height:
            # ارتفاعٌ مختلف عمّا عُوير عليه الصندوق: لا نقصّ بالتخمين.
            return frame
        return box.apply(frame)

    # فوق هذه المسافة يصير البحث العشوائي أرخص من التقدّم إطارًا إطارًا.
    # ‏250 إطارًا تقارب طول مجموعة الصور (GOP) المعتادة في H.264، وهي
    # المسافة التي يضطر البحث لفكّ ترميزها على أي حال.
    MAX_SEQUENTIAL_GAP = 250

    def _read_pair(self, cap, first: float, second: Optional[float],
                   fps: float, rotation_plan: RotationPlan,
                   video_path: Optional[Path] = None, ffmpeg=None,
                   temp_dir: Optional[Path] = None
                   ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """يقرأ لحظتين متقاربتين بقفزة واحدة بدل قفزتين.

        هذا هو العطل B7 نفسه الذي أُصلح في كاشف المشاهد ونجا منه المنتقي:
        ``cap.set`` داخل الحلقة يفرض على OpenCV فكّ الترميز من أقرب إطار
        مفتاحي في **كل** استدعاء. مع خمسة مرشّحين وثلاث قراءات لكلٍّ منهم
        كانت النتيجة خمس عشرة قفزة لكل مشهد — على محاضرة بمئات المشاهد
        تصير المرحلة أبطأ من التفريغ نفسه، وشريط التقدّم ساكن فتبدو
        متجمّدة.

        اللحظة الثانية تقع بعد الأولى بأقل من ثانية عادةً، فالتقدّم إليها
        بـ ``grab()`` (بلا نسخ إلى numpy) أرخص بكثير من قفزة ثانية.
        """
        first_index = max(0, int(first * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, first_index)
        ok, frame = cap.read()

        if ok and frame is not None:
            frame = self._crop(rotation_plan.apply(frame))
            # المؤشر الآن عند first_index + 1 بالضبط — وهذا وحده ما يجعل
            # التقدّم التسلسلي صحيحًا.
            sequential = True
        else:
            # مسار FFmpeg الاحتياطي: موضع المؤشر غير معروف بعده، فلا
            # يجوز التقدّم منه تسلسليًا.
            frame = self._read_at(cap, first, fps, rotation_plan,
                                  video_path, ffmpeg, temp_dir)
            sequential = False

        if frame is None or second is None or second <= first:
            return frame, None

        second_index = max(0, int(second * fps))
        gap = second_index - first_index
        if gap <= 0:
            return frame, None
        if not sequential or gap > self.MAX_SEQUENTIAL_GAP:
            # مسافة بعيدة أو مؤشر مجهول: القفزة أرخص وأسلم
            return frame, self._read_at(cap, second, fps, rotation_plan,
                                        video_path, ffmpeg, temp_dir)

        for _ in range(gap - 1):
            if not cap.grab():
                return frame, None
        ok, probe = cap.read()
        if not ok or probe is None:
            return frame, None
        # الإطار المرجعي يُقصّ أيضًا: مقارنة مقصوصٍ بغير مقصوص تُنتج
        # فرقًا وهميًّا بحجم الشريط، فيُعدّ كل إطار «غير مستقرّ».
        return frame, self._crop(rotation_plan.apply(probe))

    def _candidate_times(self, scene: Scene) -> List[float]:
        """لحظات مرشّحة داخل المشهد، منحازة إلى ما بعد الانتقال.

        البداية مستبعدة عمدًا: هناك تقع شاشة التحميل.
        """
        span = max(0.0, scene.end - scene.start)
        count = max(1, self.config.candidates_per_scene)
        if span < 0.6:
            return [scene.start + span / 2.0]
        # من 45% إلى 92% من المشهد
        return [scene.start + span * f
                for f in np.linspace(0.45, 0.92, count)]

    def _evaluate(self, cap, timestamp: float, fps: float,
                  rotation_plan: RotationPlan, scene_end: float,
                  video_path: Optional[Path] = None, ffmpeg=None,
                  temp_dir: Optional[Path] = None,
                  scene_reference: Optional[np.ndarray] = None
                  ) -> Optional[tuple[float, np.ndarray, FrameQuality]]:
        """يعيد (درجة, إطار, جودة) أو None إن كان الإطار غير صالح.

        ``scene_reference``: إطار الحالة المستقرة قرب نهاية المشهد. يُقرأ
        **مرة واحدة لكل مشهد** في ``extract`` بدل مرة لكل مرشّح — فهو
        نفسه لكل مرشّحي المشهد، وقراءته خمس مرات كانت هدرًا خالصًا.
        """
        settle_probe = min(timestamp + self.config.settle_probe_seconds,
                           scene_end - 0.4)
        frame, near_probe = self._read_pair(
            cap, timestamp, settle_probe if settle_probe > timestamp else None,
            fps, rotation_plan, video_path, ffmpeg, temp_dir)
        if frame is None:
            return None

        quality = measure(frame)
        if quality.is_blank(self.config.min_content_density,
                            self.config.max_dominant_color_ratio):
            return None
        if quality.brightness < self.config.min_brightness:
            return None
        if quality.sharpness < self.config.min_variance:
            return None

        # الاستقرار يُقاس مرّتين:
        #   (أ) مقابل إطار قريب  → يمسك الحركة اللحظية
        #   (ب) مقابل نهاية المشهد → يمسك شاشة التحميل تحديدًا: محتواها
        #       يصل لاحقًا داخل المشهد نفسه، فتختلف كثيرًا عن حالته
        #       المستقرة الأخيرة. الفحص القريب وحده لا يراها لأن شاشة
        #       التحميل ساكنة تمامًا أثناء انتظارها.
        instability = 0.0
        if near_probe is not None:
            instability = max(instability, difference(frame, near_probe))
        # هامش قبل نهاية المشهد: النهاية نفسها هي لحظة الانتقال التالي،
        # فمقارنتها تُظهر اختلافًا في كل مرة وتعاقب الإطارات السليمة بلا سبب.
        if scene_reference is not None and scene_end - 0.4 > timestamp:
            instability = max(instability, difference(frame, scene_reference))

        score = quality.score()
        if instability > self.config.max_instability:
            # عقوبة متدرّجة: الشاشة ما زالت تتحرك
            score -= min(8.0, instability * 200.0)
        return score, frame, quality

    @staticmethod
    def _drop_weak_outliers(candidates: list, floor_ratio: float) -> list:
        """يُسقط المرشّحين الأضعف بكثير من وسيط المجموعة.

        شاشة تحميل قد تتجاوز العتبة المطلقة بفارق ضئيل (قِيس: 0.0197
        مقابل حدّ 0.018)، لكنها تبقى شاذّة أمام بقية صور الفيديو نفسه.
        العتبة النسبية تمسكها دون رقم سحري جديد.
        """
        if len(candidates) < 4:
            return candidates
        scores = sorted(c["score"] for c in candidates)
        median = scores[len(scores) // 2]
        threshold = median * floor_ratio
        return [c for c in candidates if c["score"] >= threshold]

    # ------------------------------------------------------------------
    def extract(
        self,
        video_path: Path,
        scenes: List[Scene],
        output_dir: Path,
        cancel_token: Optional[CancellationToken] = None,
        fps_hint: Optional[float] = None,
        rotation_plan: Optional[RotationPlan] = None,
        ffmpeg=None,
        temp_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[KeyframeMetadata]:
        rotation_plan = rotation_plan or RotationPlan(0, "none")
        output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise MediaValidationError(f"تعذر فتح الفيديو: {video_path}")

        keyframes: List[KeyframeMetadata] = []
        rejected = {"blank": 0, "duplicate": 0, "too_close": 0, "unreadable": 0}

        try:
            fps = fps_hint if (fps_hint and fps_hint > 0) else cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps <= 0 or fps != fps:
                raise MediaValidationError("معدل إطارات غير صالح.")

            # --- المرور الأول: تقييم المرشّحين (بلا حفظ) ---
            # نحتفظ بالبصمة والدرجة فقط، لا بالإطارات: 150 إطارًا بدقة
            # كاملة تتجاوز جيجابايت من الذاكرة.
            candidates: list[dict] = []
            last_time = -1e9
            total_scenes = max(1, len(scenes))
            last_emit = -1.0
            for index, scene in enumerate(scenes, start=1):
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                    cancel_token.wait_if_paused()

                # تقدّم حقيقي: بلا هذا يبقى الشريط ساكنًا طوال المرحلة
                # فتبدو متجمّدة، ويُلغي المستخدم عملًا سليمًا.
                if progress_callback:
                    fraction = index / total_scenes
                    if fraction - last_emit >= 0.01 or index == total_scenes:
                        last_emit = fraction
                        progress_callback(
                            fraction * 0.85,
                            f"اختيار الصور: المشهد {index} من {total_scenes}")

                # مرجع الحالة المستقرة: إطار واحد لكل مشهد لا لكل مرشّح
                scene_reference = None
                if scene.end - 0.4 > scene.start:
                    scene_reference = self._read_at(
                        cap, scene.end - 0.4, fps, rotation_plan,
                        video_path, ffmpeg, temp_dir)

                best = None
                for timestamp in self._candidate_times(scene):
                    evaluated = self._evaluate(cap, timestamp, fps,
                                               rotation_plan, scene.end,
                                               video_path, ffmpeg, temp_dir,
                                               scene_reference=scene_reference)
                    if evaluated is None:
                        continue
                    if best is None or evaluated[0] > best[0]:
                        best = (evaluated[0], evaluated[1], timestamp)

                if best is None:
                    rejected["blank"] += 1
                    logger.debug(f"المشهد {scene.scene_id}: لا إطار صالح")
                    continue

                score, frame, chosen_time = best
                # التباعد يُقاس بين اللحظتين المختارتين فعليًا. قياسه من
                # بداية المشهد إلى اللحظة المختارة السابقة خطأ: اللحظة
                # المختارة تقع متأخرة داخل مشهدها، فيبدو المشهد التالي
                # «متقاربًا» ويُرفض بلا سبب.
                if chosen_time - last_time < self.config.min_seconds_between_keyframes:
                    rejected["too_close"] += 1
                    continue

                candidates.append({
                    "score": score, "timestamp": chosen_time, "scene": scene,
                    "signature": dhash(frame),
                })
                last_time = chosen_time

            # --- التصفية: الشواذ الضعيفة ثم التكرار ثم السقف ---
            before = len(candidates)
            filtered = self._drop_weak_outliers(
                candidates, self.config.relative_score_floor)
            # شبكة أمان: إن أسقطت التصفية كل شيء، أبقِ الأقوى. مستند بلا
            # صور أسوأ من مستند بصورة واحدة متواضعة.
            if not filtered and candidates:
                filtered = [max(candidates, key=lambda c: c["score"])]
            rejected["blank"] += before - len(filtered)
            candidates = filtered

            unique: list[dict] = []
            for candidate in candidates:
                if any(hamming_similarity(candidate["signature"], kept["signature"])
                       >= self.config.dedup_similarity for kept in unique):
                    rejected["duplicate"] += 1
                    continue
                unique.append(candidate)

            if len(unique) > self.config.max_keyframes:
                # عند التجاوز نُبقي الأقوى محتوى، لا الأوائل زمنيًا
                strongest = sorted(unique, key=lambda c: -c["score"])
                unique = sorted(strongest[: self.config.max_keyframes],
                                key=lambda c: c["timestamp"])
                logger.warning(f"بلغ سقف الصور ({self.config.max_keyframes}).")

            # --- المرور الثاني: قراءة الإطارات المقبولة وحفظها ---
            for index, candidate in enumerate(unique, start=1):
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                if progress_callback:
                    progress_callback(
                        0.85 + 0.15 * index / max(1, len(unique)),
                        f"حفظ الصور: {index} من {len(unique)}")
                frame = self._read_at(cap, candidate["timestamp"], fps,
                                      rotation_plan, video_path, ffmpeg,
                                      temp_dir)
                if frame is None:
                    rejected["unreadable"] += 1
                    continue
                scene = candidate["scene"]
                path = self._save(frame, output_dir, index, candidate["timestamp"])
                image_width, image_height = Image.open(path).size
                keyframes.append(KeyframeMetadata(
                    image_id=index,
                    timestamp=candidate["timestamp"],
                    filename=path.name,
                    scene_id=scene.scene_id,
                    change_score=scene.change_score,
                    width=image_width, height=image_height,
                    selection_reason="stable_frame_major" if scene.is_major
                    else "stable_frame",
                    checksum=hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                ))
        finally:
            cap.release()

        logger.info(
            f"حُفظت {len(keyframes)} صورة من {len(scenes)} مشهد "
            f"(مستبعَد: {rejected['blank']} فارغ/تحميل، "
            f"{rejected['duplicate']} مكرر، {rejected['too_close']} متقارب)")
        return keyframes

    # ------------------------------------------------------------------
    def _save(self, frame_bgr: np.ndarray, output_dir: Path,
              image_id: int, timestamp: float) -> Path:
        image = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        if image.width > self.config.max_width:
            ratio = self.config.max_width / float(image.width)
            image = image.resize(
                (self.config.max_width, max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS)

        extension = self.config.format.lower()
        stamp = safe_timestamp_for_filename(timestamp)
        path = output_dir / f"{image_id:04d}_{stamp}.{extension}"
        if extension in ("jpg", "jpeg"):
            image.save(path, quality=self.config.jpg_quality, optimize=True,
                       progressive=True)
        else:
            image.save(path, optimize=True)
        return path
