"""مراحل الوسائط في خط المعالجة: التحقّق، ومصطلحات الشاشة، والمشاهد، واللقطات، وOCR.

منقولة من ``core/pipeline.py`` كما هي (مزيج يرثه ``VideoToDocPipeline``) —
الملف تجاوز 1300 سطر يجمع كل مرحلة. لا تغيير في السلوك.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import List, Optional

from config.schemas import (
    KeyframeMetadata,
)
from config.settings import ClipRange
from core.exceptions import (
    InsufficientDiskSpaceError,
    MediaValidationError,
)
from core.job_state import Stage
from utils.cancellation import CancellationToken
from utils.logger import logger
from video.scene_detector import Scene

#: أدنى مساحة حرّة قبل البدء — منقولة مع ``_validate_input``.
_MIN_FREE_BYTES = 2 * 1024 ** 3


class MediaStagesMixin:
    def _autofill_screen_glossary(self, video_path: Path, metadata,
                                  emit) -> None:
        """يملأ «مصطلحات المادة» من نصّ الشاشة قبل التفريغ — إن كانت فارغة.

        الترتيب هو كل الفكرة: نصّ الشاشة كان يُستخرج في مرحلة اللقطات،
        أي **بعد** التفريغ بمرحلتين — فيُعرَض في المستند ولا ينفع الدقّة
        التي كان يستطيع رفعها. والمسح هنا أربعةَ عشر إطارًا فقط، ثوانٍ
        أمام ساعات التفريغ.

        ولا يُلمس ما كتبه المستخدم بيده: اختياره يفوز دائمًا.
        """
        settings = self.config.whisper
        if not getattr(settings, "auto_screen_glossary", True):
            return
        if (settings.glossary or "").strip():
            return
        if not metadata.has_video or metadata.duration_seconds <= 0:
            return
        try:
            from video.screen_terms import as_glossary, scan_video

            emit(Stage.TRANSCRIPTION, 0.01,
                 "مسح مصطلحات الشاشة قبل التفريغ…")
            terms = scan_video(video_path, self.ffmpeg,
                               metadata.duration_seconds)
            cap = int(getattr(settings, "max_screen_terms", 0) or 0)
            if cap > 0:
                terms = terms[:cap]
            glossary = as_glossary(terms)
            if glossary:
                settings.glossary = glossary
                logger.info(
                    f"مصطلحات من الشاشة ({len(terms)}): {glossary[:160]}")
        except Exception as exc:
            # اقتراحٌ مساعد لا شرط تشغيل — فشلُه لا يمسّ التفريغ.
            logger.warning(f"تعذّر مسح مصطلحات الشاشة: {exc}")

    def _prepare_screen_crop(self, video_path: Path, metadata) -> None:
        """يستنتج صندوق قصّ زينة الشاشة ويحقنه في منتقي اللقطات.

        يقع قبل كشف المشاهد فيسري على كل إطار يُقرأ بعده — بما فيه
        إطارات المقارنة. وفشلُه لا يُفشل شيئًا: لقطةٌ بشريطٍ زائد أهون
        من مهمّة ساقطة.
        """
        frames_config = self.config.frames
        if not getattr(frames_config, "crop_screen_chrome", True):
            return
        try:
            from video.screen_crop import detect_from_video, manual_box

            top = float(getattr(frames_config, "crop_top_ratio", 0.0) or 0.0)
            bottom = float(getattr(frames_config, "crop_bottom_ratio", 0.0) or 0.0)
            if top > 0 or bottom > 0:
                # اليدويّ يُلغي الاستنتاج: من ضبط النسب يعرف شاشته.
                box = manual_box(metadata.height or 0, top, bottom)
                logger.info(f"قصّ يدويّ لزينة الشاشة: {box.describe()}")
            else:
                box = detect_from_video(video_path,
                                        metadata.duration_seconds, self.ffmpeg)
            self.keyframe_selector.crop_box = box
        except Exception as exc:
            logger.warning(f"تعذّر كشف زينة الشاشة: {exc}")

    def _report_audio_level(self, media_path: Path, normalized: bool) -> None:
        """يقيس مستوى الصوت ويقول للمستخدم إن كان هادئًا — تشخيص لا قرار.

        القرار (التطبيع) يقع دائمًا. وهذا السطر يجيب على السؤال الذي
        كان بلا جواب: «لماذا خرج تفريغي ناقصًا؟» — سطرٌ في السجلّ يقول
        إن المصدر أهدأ من اللازم أنفع من تخمين المستخدم.
        """
        try:
            levels = self.ffmpeg.audio_levels(media_path)
        except Exception as exc:
            logger.debug(f"تعذّر قياس مستوى الصوت: {exc}")
            return
        if not levels or "peak_db" not in levels:
            logger.debug("قياس مستوى الصوت لم يُرجع قيمًا.")
            return
        peak = levels["peak_db"]
        mean = levels.get("mean_db")
        detail = f"ذروة {peak:.1f} dBFS" + (
            f" · متوسّط {mean:.1f} dBFS" if mean is not None else "")
        if peak < self.ffmpeg.QUIET_PEAK_DBFS:
            logger.warning(
                f"المصدر هادئ ({detail}). "
                + ("أُطبّق توحيد المستوى قبل التفريغ."
                   if normalized else
                   "توحيد المستوى معطَّل — متوقَّعٌ ضياع مقاطع من التفريغ."))
        else:
            logger.info(f"مستوى الصوت: {detail}")

    # ------------------------------------------------------------------
    def _validate_input(self, video_path: Path,
                        allow_audio_only: bool = False) -> None:
        if not video_path.exists():
            raise MediaValidationError(f"الملف غير موجود: {video_path}")
        if video_path.stat().st_size == 0:
            raise MediaValidationError("الملف فارغ (0 بايت).")

        # فحص فك الترميز فعليًا قبل بدء العمل (المواصفة §22: "Invalid video
        # → Fail before pipeline"). ``validate_readable`` كانت مكتوبة ولا
        # تُستدعى، فملف يقرأ ffprobe رأسه ويعجز عن فك إطاراته كان يمرّ
        # حتى مرحلة الصور ثم يفشل بعد دقائق من التفريغ.
        try:
            self.ffmpeg.validate_readable(video_path,
                                          expect_video=not allow_audio_only)
        except MediaValidationError:
            raise
        except Exception as exc:
            logger.warning(f"تعذّر التحقق المسبق من فك الترميز: {exc}")

        free = shutil.disk_usage(self.output_base_dir.parent
                                 if self.output_base_dir.exists()
                                 else Path.home()).free
        if free < _MIN_FREE_BYTES:
            raise InsufficientDiskSpaceError(
                f"المساحة الحرة غير كافية: {free / 1024**3:.1f}GB "
                f"(المطلوب {_MIN_FREE_BYTES / 1024**3:.0f}GB على الأقل).")

    def _stage_scenes(self, job, video_path, metadata, cancel_token, emit,
                      rotation_plan, clip: Optional[ClipRange] = None
                      ) -> List[Scene]:
        scenes_fp = self._stage_fp(Stage.SCENE_DETECTION, video_path, clip)
        if (job.is_stage_complete(Stage.SCENE_DETECTION)
                and job.has_artifact("scenes", scenes_fp)):
            raw = job.load_artifact("scenes")
            return [Scene(**s) for s in raw["scenes"]]

        job.begin_stage(Stage.SCENE_DETECTION)
        emit(Stage.SCENE_DETECTION, 0.05, "تحليل المشاهد…")
        clip = clip or ClipRange()
        scenes = self.scene_detector.detect(
            video_path, cancel_token,
            lambda pct, msg: emit(Stage.SCENE_DETECTION, pct / 100.0, msg),
            fps_hint=metadata.fps,
            duration_hint=metadata.duration_seconds,
            rotation_plan=rotation_plan,
            start_seconds=clip.start_seconds,
            end_seconds=clip.end_seconds)
        job.save_artifact("scenes", "scenes.json", json.dumps(
            {"schema_version": "1.3",
             "scenes": [s.__dict__ for s in scenes]},
            ensure_ascii=False, indent=2),
            processing_fingerprint=scenes_fp)
        job.complete_stage(Stage.SCENE_DETECTION)
        cancel_token.raise_if_cancelled()
        logger.info(f"اكتُشف {len(scenes)} مشهد.")
        return scenes

    def _stage_keyframes(self, job, video_path, metadata, scenes, images_dir,
                         cancel_token, emit, rotation_plan, clip: Optional[ClipRange] = None
                         ) -> List[KeyframeMetadata]:
        scenes_ref = job.state.artifacts.get("scenes")
        scenes_artifact_fp = scenes_ref.processing_fingerprint if scenes_ref else None
        keyframes_fp = self._stage_fp(Stage.KEYFRAMES, video_path, clip, scenes_artifact_fp)
        if (job.is_stage_complete(Stage.KEYFRAMES)
                and job.has_artifact("keyframes", keyframes_fp)):
            raw = job.load_artifact("keyframes")
            keyframes = [KeyframeMetadata(**k) for k in raw["keyframes"]]
            if all((images_dir / k.filename).exists() for k in keyframes):
                return keyframes
            logger.warning("صور مفقودة — إعادة الاستخراج.")

        job.begin_stage(Stage.KEYFRAMES)
        emit(Stage.KEYFRAMES, 0.2, "استخراج الصور الرئيسية…")
        keyframes = self.keyframe_selector.extract(
            video_path, scenes, images_dir, cancel_token,
            fps_hint=metadata.fps, rotation_plan=rotation_plan,
            ffmpeg=self.ffmpeg, temp_dir=job.job_dir / "temp",
            progress_callback=lambda f, m: emit(Stage.KEYFRAMES, f * 0.85, m))

        if self.config.frames.enable_ocr:
            self._run_ocr(keyframes, images_dir, cancel_token, emit)

        job.save_artifact("keyframes", "keyframes.json", json.dumps(
            {"schema_version": "1.3",
             "keyframes": [k.model_dump() for k in keyframes]},
            ensure_ascii=False, indent=2),
            processing_fingerprint=keyframes_fp)
        job.complete_stage(Stage.KEYFRAMES)
        cancel_token.raise_if_cancelled()
        return keyframes

    def _run_ocr(self, keyframes: List[KeyframeMetadata], images_dir: Path,
                cancel_token: CancellationToken, emit) -> None:
        """يستخرج نص الشاشة لكل لقطة (ADR جديد: OCR اختياري بـ Tesseract،
        انظر video/ocr.py). إثراء لمحتوى المستند لا مرحلة حرجة — غياب
        Tesseract أو فشل صورة بعينها لا يوقف المعالجة، فقط يترك
        ``ocr_text`` فارغًا.
        """
        from video.ocr import extract_text, is_available

        if not keyframes:
            return
        if not is_available():
            logger.info(
                "OCR مفعَّل في الإعداد لكن Tesseract غير مثبَّت — "
                "المتابعة بلا نص شاشة. شغّل tools/doctor.py للتثبيت.")
            return

        total = len(keyframes)
        hidden = 0
        for index, kf in enumerate(keyframes):
            cancel_token.raise_if_cancelled()
            emit(Stage.KEYFRAMES, 0.85 + 0.15 * (index / total),
                f"استخراج نص الشاشة… ({index + 1}/{total})")
            path = images_dir / kf.filename
            if getattr(self.config.document, "anonymize_names", False):
                from video.ocr import extract_text_and_words
                from video.redact import redact_keyframe

                text, words = extract_text_and_words(path)
                kf.ocr_text, names = redact_keyframe(path, words, text)
                if names:
                    hidden += len(names)
                    try:
                        kf.checksum = hashlib.sha256(
                            path.read_bytes()).hexdigest()[:16]
                    except OSError:
                        pass
            else:
                kf.ocr_text = extract_text(path)
        if hidden:
            logger.info(f"إخفاء الأسماء: مُوِّه {hidden} اسمًا/بريدًا في اللقطات "
                        "وحُذف من نصّ الشاشة.")
