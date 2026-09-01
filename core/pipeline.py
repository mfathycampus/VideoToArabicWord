"""خط الإنتاج الموحّد مع الاستئناف الكامل.

كل مرحلة تحفظ ناتجها على القرص وتُسجَّل بـ checksum. إعادة تشغيل مهمة
متوقفة تتخطى المراحل التي ما زال ناتجها صالحًا (انظر ``core/job_state``).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from audio.model_manager import ModelManager
from audio.transcriber import TranscriptionEngine
from config.schemas import (
    DocumentPlan, KeyframeMetadata, TranscriptionResult, VideoMetadata,
)
from config.settings import AppConfig
from core.exceptions import (
    AppBaseException, InsufficientDiskSpaceError, MediaValidationError,
    PipelineCancelledError,
)
from core.job_state import JobManager, Stage
from document.planner import TimelinePlanner
from document.word_generator import DocumentGenerator
from utils.cancellation import CancellationToken
from utils.ffmpeg_service import FFmpegService
from utils.logger import logger
from utils.frames import RotationPlan, resolve_rotation_plan
from utils.media_probe import extract_video_facts, probe_raw
from utils.timestamps import humanize_title
from video.keyframe_selector import KeyframeSelector
from video.scene_detector import Scene, SceneDetector

ProgressFn = Callable[[float, str], None]

# أقل مساحة حرة مطلوبة قبل بدء المهمة
_MIN_FREE_BYTES = 2 * 1024 ** 3


class VideoToDocPipeline:
    """يقبل محرّكاته بالحقن (ADR-008) ليكون قابلًا للاختبار والاستبدال."""

    def __init__(
        self,
        output_base_dir: Path,
        config: Optional[AppConfig] = None,
        ffmpeg: Optional[FFmpegService] = None,
        transcriber: Optional[TranscriptionEngine] = None,
        scene_detector: Optional[SceneDetector] = None,
        keyframe_selector: Optional[KeyframeSelector] = None,
        planner: Optional[TimelinePlanner] = None,
        document_generator: Optional[DocumentGenerator] = None,
        model_manager: Optional[ModelManager] = None,
    ) -> None:
        self.config = config or AppConfig()
        self.output_base_dir = output_base_dir
        self.ffmpeg = ffmpeg or FFmpegService()
        self.transcriber = transcriber or TranscriptionEngine(self.config.whisper)
        self.scene_detector = scene_detector or SceneDetector(self.config.scene_detection)
        self.keyframe_selector = keyframe_selector or KeyframeSelector(self.config.frames)
        self.planner = planner or TimelinePlanner(
            section_minutes=self.config.document.section_minutes,
            paragraph_max_chars=self.config.document.paragraph_max_chars,
            adaptive_sections=self.config.document.adaptive_sections)
        self.document_generator = document_generator or DocumentGenerator(self.config.document)
        self.model_manager = model_manager or ModelManager(self.config.whisper.download_root)

    # ------------------------------------------------------------------
    def job_dir_for(self, video_path: Path) -> Path:
        safe = "".join(c for c in video_path.stem if c not in '<>:"/\\|?*').strip()
        return self.output_base_dir / (safe or "job")

    def run(
        self,
        video_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[ProgressFn] = None,
        allow_model_download: bool = False,
    ) -> Path:
        cancel_token = cancel_token or CancellationToken()
        job_dir = self.job_dir_for(video_path)
        job = JobManager.create_or_resume(job_dir, video_path)
        emit = self._make_emitter(progress_callback)

        try:
            result = self._execute(job, video_path, cancel_token, emit,
                                   allow_model_download)
            job.finish()
            if not self.config.application.keep_temp_on_success:
                self._cleanup_temp(job_dir)
            emit(Stage.DOCUMENT, 1.0, "اكتملت المعالجة بنجاح.")
            return result
        except PipelineCancelledError:
            job.cancel()
            logger.info("أُلغيت المهمة بطلب المستخدم.")
            raise
        except Exception as exc:
            job.fail(f"{type(exc).__name__}: {exc}")
            logger.exception("فشل الـ pipeline")
            raise

    # ------------------------------------------------------------------
    def _make_emitter(self, progress_callback: Optional[ProgressFn]):
        from core.job_state import STAGE_PROGRESS

        def emit(stage: Stage, fraction: float, message: str) -> None:
            if progress_callback is None:
                return
            low, high = STAGE_PROGRESS[stage]
            pct = low + max(0.0, min(1.0, fraction)) * (high - low)
            progress_callback(pct, message)

        return emit

    def _cleanup_temp(self, job_dir: Path) -> None:
        temp = job_dir / "temp"
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)

    # ------------------------------------------------------------------
    def _execute(self, job: JobManager, video_path: Path,
                 cancel_token: CancellationToken, emit,
                 allow_model_download: bool) -> Path:
        job_dir = job.job_dir
        temp_dir = job_dir / "temp"
        images_dir = job_dir / "keyframes"
        output_docx = job_dir / f"{video_path.stem}.docx"

        # ---------- 1. التحقق ----------
        job.begin_stage(Stage.VALIDATION)
        emit(Stage.VALIDATION, 0.1, "التحقق من ملف الفيديو…")
        self._validate_input(video_path)
        cancel_token.raise_if_cancelled()
        job.complete_stage(Stage.VALIDATION)

        # ---------- 2. الميتاداتا ----------
        if job.is_stage_complete(Stage.METADATA) and job.has_artifact("metadata"):
            metadata = VideoMetadata(**job.load_artifact("metadata"))
            logger.info("تخطي الميتاداتا — ناتج صالح موجود.")
        else:
            job.begin_stage(Stage.METADATA)
            emit(Stage.METADATA, 0.3, "قراءة بيانات الفيديو…")
            facts = extract_video_facts(probe_raw(video_path))
            metadata = VideoMetadata(filename=video_path.name, path=video_path,
                                     **facts)
            job.save_artifact("metadata", "metadata.json",
                              metadata.model_dump_json(indent=2))
            job.complete_stage(Stage.METADATA)
        cancel_token.raise_if_cancelled()
        logger.info(f"الفيديو: {metadata.width}×{metadata.height} @ "
                    f"{metadata.fps:.2f}fps, {metadata.duration_seconds:.1f}s, "
                    f"دوران={metadata.rotation}°, صوت={metadata.has_audio}")

        # ---------- 3. الصوت + التفريغ ----------
        transcript = self._stage_transcription(
            job, video_path, metadata, temp_dir, cancel_token, emit,
            allow_model_download)

        # ---------- 4. خطة الدوران (تُستنتج مرة واحدة) ----------
        rotation_plan = resolve_rotation_plan(
            video_path, metadata.rotation,
            metadata.stored_width or metadata.width,
            metadata.stored_height or metadata.height,
            metadata.width, metadata.height, ffmpeg=self.ffmpeg)
        if metadata.rotation:
            logger.info(f"خطة الدوران: تصحيح {rotation_plan.correction}° "
                        f"({rotation_plan.reason})")

        # ---------- 5. كشف المشاهد ----------
        scenes = self._stage_scenes(job, video_path, metadata, cancel_token,
                                    emit, rotation_plan)

        # ---------- 6. الصور الرئيسية ----------
        keyframes = self._stage_keyframes(
            job, video_path, metadata, scenes, images_dir, cancel_token, emit,
            rotation_plan)

        # ---------- 7. خطة المستند ----------
        # الخطة تُعاد إن تغيّرت طريقة بنائها. بدون هذا الفحص، تشغيل
        # سابق بلا صياغة يترك خطة مكتملة، فيتخطّى الاستئناف مرحلة
        # البناء ولا تُستدعى الصياغة إطلاقًا رغم تفعيلها — والمستخدم
        # يرى مستندًا خامًا دون أي رسالة خطأ.
        expected = self._expected_plan_source()
        reusable = (job.is_stage_complete(Stage.MATCHING)
                    and job.has_artifact("plan"))
        if reusable:
            stored = DocumentPlan(**job.load_artifact("plan"))
            if self._plan_matches(stored.generated_by, expected):
                plan = stored
            else:
                logger.info(
                    f"تغيّرت طريقة بناء الوثيقة "
                    f"({stored.generated_by} ← {expected}) — إعادة البناء.")
                reusable = False
        if not reusable:
            job.begin_stage(Stage.MATCHING)
            plan = self._build_plan(transcript, keyframes, video_path,
                                    metadata, emit)
            job.save_artifact("plan", "plan.json", plan.model_dump_json(indent=2))
            job.complete_stage(Stage.MATCHING)
        cancel_token.raise_if_cancelled()

        # ---------- 8. المستند ----------
        job.begin_stage(Stage.DOCUMENT)
        emit(Stage.DOCUMENT, 0.2, "كتابة مستند Word…")
        self.document_generator.generate(plan, metadata, images_dir, output_docx)
        job.register_artifact("document", output_docx)
        job.complete_stage(Stage.DOCUMENT)
        return output_docx

    def _expected_plan_source(self) -> str:
        """توقيع مصدر الخطة حسب الإعداد الحالي."""
        settings = self.config.rewrite
        if not settings.enabled:
            return "timeline"
        return f"ai:{settings.provider}"

    @staticmethod
    def _plan_matches(stored: str, expected: str) -> bool:
        """هل الخطة المحفوظة نتجت عن نفس الإعداد الحالي؟

        ``generated_by`` المحفوظ يحمل النموذج أيضًا (``ai:anthropic/…``)،
        فتُقارَن البادئة لا النص كاملًا.
        """
        if expected == "timeline":
            return stored == "timeline"
        return stored.startswith(expected)

    def _build_plan(self, transcript, keyframes, video_path, metadata, emit
                    ) -> DocumentPlan:
        """يبني بنية الوثيقة — بإعادة صياغة إن طُلبت، وإلا زمنيًا.

        فشل إعادة الصياغة **لا يُفشل المهمة**: نسقط إلى المخطِّط الزمني
        وننبّه. المستند الخام أفضل من لا مستند.
        """
        title = humanize_title(video_path.stem)
        # لا نكرّر اسم الملف هنا: هو في جدول الغلاف أصلًا، وتكراره
        # يُطيل العنوان الفرعي بلا فائدة.
        subtitle = "مستند مُولَّد من تسجيل مرئي"
        settings = self.config.rewrite

        if settings.enabled:
            emit(Stage.MATCHING, 0.1, "إعادة صياغة النص…")
            try:
                from ai.providers import build_provider
                from ai.rewriter import RewriteConfig, TranscriptRewriter

                provider = build_provider(settings.provider, settings.model,
                                          settings.base_url, settings.api_key_env,
                                          settings.api_key,
                                          settings.workspace_id)
                if not provider.is_available():
                    raise RuntimeError(
                        f"المزوّد «{provider.info.label_ar}» غير متاح.")
                rewriter = TranscriptRewriter(
                    provider,
                    RewriteConfig(enabled=True, provider=settings.provider,
                                  model=settings.model,
                                  batch_chars=settings.batch_chars,
                                  make_outline=settings.make_outline,
                                  timeout_seconds=settings.timeout_seconds))
                plan = rewriter.build_plan(
                    transcript, keyframes, title, subtitle,
                    lambda f, m: emit(Stage.MATCHING, 0.1 + f * 0.8, m))
                logger.info(f"أُعيدت الصياغة عبر {plan.generated_by}")
                return plan
            except Exception as exc:
                logger.warning(
                    f"تعذّرت إعادة الصياغة ({exc}) — المتابعة بالنص الخام.")
                emit(Stage.MATCHING, 0.85,
                     "تعذّرت إعادة الصياغة — المتابعة بالنص الخام")

        emit(Stage.MATCHING, 0.9, "بناء بنية المستند…")
        return self.planner.build(transcript, keyframes, title, subtitle)

    # ------------------------------------------------------------------
    def _validate_input(self, video_path: Path) -> None:
        if not video_path.exists():
            raise MediaValidationError(f"الملف غير موجود: {video_path}")
        if video_path.stat().st_size == 0:
            raise MediaValidationError("الملف فارغ (0 بايت).")

        free = shutil.disk_usage(self.output_base_dir.parent
                                 if self.output_base_dir.exists()
                                 else Path.home()).free
        if free < _MIN_FREE_BYTES:
            raise InsufficientDiskSpaceError(
                f"المساحة الحرة غير كافية: {free / 1024**3:.1f}GB "
                f"(المطلوب {_MIN_FREE_BYTES / 1024**3:.0f}GB على الأقل).")

    def _stage_transcription(self, job, video_path, metadata, temp_dir,
                             cancel_token, emit, allow_model_download
                             ) -> TranscriptionResult:
        if job.is_stage_complete(Stage.TRANSCRIPTION) and job.has_artifact("transcription"):
            logger.info("تخطي التفريغ — ناتج صالح موجود.")
            return TranscriptionResult(**job.load_artifact("transcription"))

        if not metadata.has_audio:
            logger.warning("لا يوجد مسار صوتي — سيُنتَج مستند بالصور فقط.")
            transcript = TranscriptionResult(
                language=self.config.whisper.language,
                full_text_raw="", full_text_clean="", segments=[], words=[],
                engine={"name": "none", "reason": "no_audio_stream"})
            job.save_artifact("transcription", "transcription.json",
                              transcript.model_dump_json(indent=2))
            job.complete_stage(Stage.AUDIO_EXTRACTION)
            job.complete_stage(Stage.TRANSCRIPTION)
            return transcript

        # استخراج الصوت
        audio_path = temp_dir / "audio_16k.wav"
        if not (job.is_stage_complete(Stage.AUDIO_EXTRACTION) and audio_path.exists()):
            job.begin_stage(Stage.AUDIO_EXTRACTION)
            emit(Stage.AUDIO_EXTRACTION, 0.2, "استخراج المسار الصوتي…")
            self.ffmpeg.extract_audio(video_path, audio_path)
            job.complete_stage(Stage.AUDIO_EXTRACTION)
        cancel_token.raise_if_cancelled()

        # النموذج
        job.begin_stage(Stage.TRANSCRIPTION)
        emit(Stage.TRANSCRIPTION, 0.02, "تجهيز نموذج التفريغ…")
        self.model_manager.ensure_available(
            self.config.whisper.model_size,
            allow_download=allow_model_download,
            progress_callback=lambda m: emit(Stage.TRANSCRIPTION, 0.03, m))

        transcript = self.transcriber.transcribe(
            audio_path, cancel_token,
            lambda f, m: emit(Stage.TRANSCRIPTION, f, m))
        job.save_artifact("transcription", "transcription.json",
                          transcript.model_dump_json(indent=2))
        job.complete_stage(Stage.TRANSCRIPTION)
        return transcript

    def _stage_scenes(self, job, video_path, metadata, cancel_token, emit,
                      rotation_plan) -> List[Scene]:
        if job.is_stage_complete(Stage.SCENE_DETECTION) and job.has_artifact("scenes"):
            raw = job.load_artifact("scenes")
            return [Scene(**s) for s in raw["scenes"]]

        job.begin_stage(Stage.SCENE_DETECTION)
        emit(Stage.SCENE_DETECTION, 0.05, "تحليل المشاهد…")
        scenes = self.scene_detector.detect(
            video_path, cancel_token,
            lambda pct, msg: emit(Stage.SCENE_DETECTION, pct / 100.0, msg),
            fps_hint=metadata.fps,
            duration_hint=metadata.duration_seconds,
            rotation_plan=rotation_plan)
        job.save_artifact("scenes", "scenes.json", json.dumps(
            {"schema_version": "1.2",
             "scenes": [s.__dict__ for s in scenes]},
            ensure_ascii=False, indent=2))
        job.complete_stage(Stage.SCENE_DETECTION)
        cancel_token.raise_if_cancelled()
        logger.info(f"اكتُشف {len(scenes)} مشهد.")
        return scenes

    def _stage_keyframes(self, job, video_path, metadata, scenes, images_dir,
                         cancel_token, emit, rotation_plan
                         ) -> List[KeyframeMetadata]:
        if job.is_stage_complete(Stage.KEYFRAMES) and job.has_artifact("keyframes"):
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
            ffmpeg=self.ffmpeg)
        job.save_artifact("keyframes", "keyframes.json", json.dumps(
            {"schema_version": "1.2",
             "keyframes": [k.model_dump() for k in keyframes]},
            ensure_ascii=False, indent=2))
        job.complete_stage(Stage.KEYFRAMES)
        cancel_token.raise_if_cancelled()
        return keyframes
