"""خط الإنتاج الموحّد مع الاستئناف الكامل.

كل مرحلة تحفظ ناتجها على القرص وتُسجَّل بـ checksum. إعادة تشغيل مهمة
متوقفة تتخطى المراحل التي ما زال ناتجها صالحًا (انظر ``core/job_state``).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from audio.engines.base import ASREngine
from audio.model_manager import ModelManager
from config.profiles import apply_profile
from config.schemas import (
    DocumentPlan,
    KeyframeMetadata,
    TranscriptionCheckpoint,
    TranscriptionResult,
    VideoMetadata,
)
from config.settings import AppConfig, ClipRange
from core.exceptions import (
    ArtifactMissingError,
    DocumentGenerationError,
    InsufficientDiskSpaceError,
    MediaValidationError,
    ModelUnavailableError,
    PipelineCancelledError,
)
from core.job_state import JobManager, Stage, atomic_write_text
from document.planner import TimelinePlanner
from document.word_generator import DocumentGenerator
from utils.cancellation import CancellationToken
from utils.ffmpeg_service import FFmpegService
from utils.fingerprints import (
    config_fingerprint,
    processing_fingerprint,
    source_fingerprint,
)
from utils.frames import resolve_rotation_plan
from utils.logger import logger
from utils.media_probe import extract_video_facts, probe_raw
from utils.power import keep_awake
from utils.timestamps import humanize_title, seconds_to_display
from video.keyframe_selector import KeyframeSelector
from video.scene_detector import Scene, SceneDetector

ProgressFn = Callable[[float, str], None]

# أقل مساحة حرة مطلوبة قبل بدء المهمة
_MIN_FREE_BYTES = 2 * 1024 ** 3


def _shift_transcript(transcript: TranscriptionResult,
                      offset: float) -> TranscriptionResult:
    """يزيح كل توقيتات التفريغ بمقدار بداية المقطع.

    بلا هذا، مقطع يبدأ عند الدقيقة 30 تخرج توقيتاته من الصفر، فتصير
    تسميات الأشكال وملفات الترجمة مضلِّلة تمامًا.
    """
    if offset <= 0:
        return transcript

    shifted = transcript.model_copy(deep=True)
    # الكلمة الواحدة مشتركة بين ``segment.words`` والقائمة العليا
    # ``transcript.words`` — ونسخ pydantic العميق يحفظ هذا الاشتراك.
    # إزاحة القائمتين بلا حراسة تُزيح كل كلمة **مرتين**.
    seen: set[int] = set()

    def shift_word(word) -> None:
        if id(word) in seen:
            return
        seen.add(id(word))
        word.start += offset
        word.end += offset

    for segment in shifted.segments:
        segment.start += offset
        segment.end += offset
        for word in segment.words:
            shift_word(word)
    for word in shifted.words:
        shift_word(word)
    return shifted


def _legacy_job_dir_matches(legacy_dir: Path, video_path: Path) -> bool:
    """مجلد مهمة بالاسم القديم (بلا بصمة) يُستأنف منه فقط لو أُنشئ لنفس
    الملف بالضبط — أي تطابق حرفي مع ``video_path`` المخزَّن في حالته.

    الشرط صارم عمدًا: تطابق تقريبي هنا هو بالضبط التصادم الذي نتجنّبه.
    """
    state_path = legacy_dir / JobManager.STATE_FILENAME
    if not state_path.exists():
        return False
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return stored.get("video_path") == str(video_path)


class VideoToDocPipeline:
    """يقبل محرّكاته بالحقن (ADR-008) ليكون قابلًا للاختبار والاستبدال."""

    def __init__(
        self,
        output_base_dir: Path,
        config: Optional[AppConfig] = None,
        ffmpeg: Optional[FFmpegService] = None,
        transcriber: Optional[ASREngine] = None,
        scene_detector: Optional[SceneDetector] = None,
        keyframe_selector: Optional[KeyframeSelector] = None,
        planner: Optional[TimelinePlanner] = None,
        document_generator: Optional[DocumentGenerator] = None,
        model_manager: Optional[ModelManager] = None,
    ) -> None:
        # ملفّ المحتوى يُطبَّق هنا، مرّة واحدة وفي أبكر موضع ممكن: كل ما
        # بعده — المحرّكات وبصمات المراحل — يُبنى من القيم المُطبَّقة،
        # فتغيير الملفّ يُبطل المشاهد والصور المخزّنة تلقائيًا. وهو
        # السلوك الصحيح، لأن الملفّ يغيّرها فعلًا.
        base = config or AppConfig()
        self.config = apply_profile(base, base.application.content_profile)
        self.output_base_dir = output_base_dir
        self.ffmpeg = ffmpeg or FFmpegService()
        self.transcriber = transcriber or self._build_transcriber()
        self.scene_detector = scene_detector or SceneDetector(self.config.scene_detection)
        self.keyframe_selector = keyframe_selector or KeyframeSelector(self.config.frames)
        self.planner = planner or TimelinePlanner(
            section_minutes=self.config.document.section_minutes,
            paragraph_max_chars=self.config.document.paragraph_max_chars,
            adaptive_sections=self.config.document.adaptive_sections,
            screen_text_titles=self.config.document.screen_text_titles)
        self.document_generator = document_generator or DocumentGenerator(self.config.document)
        self.model_manager = model_manager or ModelManager(self.config.whisper.download_root)

    # ------------------------------------------------------------------
    def _source_fp(self, video_path: Path) -> str:
        """Source identity shared by all stage reuse decisions."""
        return source_fingerprint(video_path)

    def _stage_fp(self, stage: Stage, video_path: Path, clip: Optional[ClipRange],
                  *dependencies) -> str:
        """Fingerprint the exact inputs/configuration relevant to a stage.

        This prevents a subtle resume bug: a valid old artifact must not be
        reused merely because the source video is unchanged when the user
        changed a model, glossary, OCR, scene, frame, rewrite or document
        setting.
        """
        source_fp = self._source_fp(video_path)
        clip_data = clip.model_dump(mode="json") if clip is not None else None
        config_map = {
            Stage.METADATA: self.config.application.model_dump(mode="json"),
            Stage.AUDIO_EXTRACTION: {"denoise_audio": self.config.application.denoise_audio},
            Stage.TRANSCRIPTION: self.config.whisper.model_dump(mode="json", exclude={"hf_token"}),
            Stage.SCENE_DETECTION: self.config.scene_detection.model_dump(mode="json"),
            Stage.KEYFRAMES: self.config.frames.model_dump(mode="json"),
            Stage.MATCHING: {
                "document": self.config.document.model_dump(mode="json"),
                "rewrite": self.config.rewrite.model_dump(mode="json", exclude={"api_key"}),
            },
            Stage.DOCUMENT: self.config.document.model_dump(mode="json"),
        }
        return processing_fingerprint(stage.value, source_fp, clip_data,
                                      config_fingerprint(config_map), *dependencies)

    # ------------------------------------------------------------------
    def _build_transcriber(self):
        """ينشئ محرّك التفريغ المختار (ADR-017).

        السقوط إلى الافتراضي عند فشل بناء محرّك اختياري: مستخدم اختار
        Cohere ثم أزال PyTorch يجب ألا تتعطّل عليه الأداة كليًا.
        """
        from audio.engines.registry import build_engine

        name = (self.config.whisper.engine or "faster-whisper").strip()
        try:
            engine = build_engine(name, config=self.config.whisper)
            if not engine.is_available():
                hint = engine.info.install_hint
                if not hint and name == "faster-whisper":
                    hint = "ثبّته عبر: pip install faster-whisper"
                raise ModelUnavailableError(
                    f"محرّك «{engine.info.label_ar}» غير مثبّت. {hint or 'شغّل Doctor لمعرفة طريقة التثبيت.'}")
            return engine
        except ModuleNotFoundError as exc:
            # faster-whisper نفسه (المحرّك الافتراضي الوحيد) غير مثبَّت.
            # بلا هذا الالتقاط، يتسرّب ModuleNotFoundError الخام حتى
            # الواجهة — رسالة تقنية بلا سبب ولا حل. هذا بالضبط ما تصفه
            # ``available_engines()`` بصمت لو استُدعيت بدل بناء محرّك
            # صريح (تُعيد قائمة فارغة عبر ``except Exception: continue``).
            if name == "faster-whisper":
                raise ModelUnavailableError(
                    "faster-whisper غير مثبَّت. ثبّته عبر: "
                    "pip install faster-whisper") from exc
            logger.warning(
                f"تعذّر تجهيز محرّك «{name}» ({exc}) — "
                "العودة إلى faster-whisper.")
            return build_engine("faster-whisper", config=self.config.whisper)
        except Exception as exc:
            if name == "faster-whisper":
                raise
            logger.warning(
                f"تعذّر تجهيز محرّك «{name}» ({exc}) — "
                "العودة إلى faster-whisper.")
            return build_engine("faster-whisper", config=self.config.whisper)

    def job_dir_for(self, video_path: Path,
                    clip: Optional[ClipRange] = None) -> Path:
        """مجلد المهمة. نطاق زمني مختلف ⇒ مهمة مختلفة، ومصدر مختلف ⇒ مهمة مختلفة.

        بلا هذا التمييز، تجربة على أول خمس دقائق تُفسد نواتج المعالجة
        الكاملة لنفس الملف — أو تُستأنف فوقها بنواتج لا تخصّها.

        الاسم وحده لا يكفي كهوية: ملفان بنفس الاسم من مجلدين مختلفين (أو
        بامتدادين مختلفين) كانا ينتهيان إلى مجلد مهمة واحد، فيُفسد أحدهما
        نواتج الآخر صامتًا. لذلك يُلحَق بالاسم بصمة مصدر قصيرة
        (``_source_fingerprint``) من المسار المطلق + الحجم + وقت التعديل.
        مهمة قديمة أُنشئت قبل هذا الإصلاح (اسمها بلا بصمة) تبقى قابلة
        للاستئناف من مكانها إن كانت لنفس الملف بالضبط — انظر
        ``_legacy_job_dir_matches``.
        """
        safe = "".join(c for c in video_path.stem if c not in '<>:"/\\|?*').strip()
        name = safe or "job"
        label = clip.label() if clip else ""
        suffix = f" [{label}]" if label else ""

        legacy_dir = self.output_base_dir / f"{name}{suffix}"
        if _legacy_job_dir_matches(legacy_dir, video_path):
            return legacy_dir

        fingerprint = source_fingerprint(video_path)
        return self.output_base_dir / f"{name}__{fingerprint}{suffix}"

    def rebuild_document(self, job_dir: Path,
                         output_path: Optional[Path] = None) -> Path:
        """يعيد توليد المستند من ``plan.json`` المحفوظ — بلا إعادة معالجة.

        تغيير الخط أو الشعار أو حجم الصور أو إخفاء التوقيتات كان يستلزم
        إعادة المهمة كاملة — ساعات على المعالج — رغم أن كل ما يحتاجه
        التوليد موجود على القرص: الخطة والميتاداتا والصور. هنا يستغرق
        ثوانٍ.

        تُستدعى من الواجهة (زر «أعد بناء المستند») ومن سطر الأوامر
        (``--rebuild``).
        """
        job_dir = Path(job_dir)
        plan_path = job_dir / "plan.json"
        metadata_path = job_dir / "metadata.json"
        if not plan_path.exists() or not metadata_path.exists():
            raise ArtifactMissingError(
                f"لا توجد خطة محفوظة في {job_dir.name}. "
                "شغّل المعالجة كاملةً مرة واحدة أولًا.")

        plan = DocumentPlan(**json.loads(plan_path.read_text(encoding="utf-8")))
        metadata = VideoMetadata(
            **json.loads(metadata_path.read_text(encoding="utf-8")))
        target = output_path or (job_dir / f"{Path(metadata.filename).stem}.docx")

        # القالب يُبنى من الإعداد الحالي — وهذا بيت القصيد
        generator = DocumentGenerator(self.config.document)
        generator.generate(plan, metadata, job_dir / "keyframes", target)
        logger.info(f"أُعيد بناء المستند: {target}")

        transcript = None
        transcription_path = job_dir / "transcription.json"
        if transcription_path.exists():
            try:
                transcript = TranscriptionResult(**json.loads(
                    transcription_path.read_text(encoding="utf-8")))
                from document.subtitles import write_subtitles
                if self.config.document.export_subtitles:
                    write_subtitles(transcript, target,
                                    self.config.document.subtitle_formats)
            except Exception as exc:
                logger.warning(f"تعذّرت إعادة كتابة الترجمات: {exc}")

        # وهذا بيت القصيد من المُصيِّرات: صفحة HTML وشرائح وفصول
        # وPDF كلّها تُبنى من الخطة المحفوظة في ثوانٍ — بلا تفريغ ولا
        # كشف مشاهد. من ملك ``plan.json`` ملك كل الصيغ.
        self._write_exports(plan, metadata, job_dir / "keyframes", target,
                            None, docx_path=target, transcript=transcript)
        return target

    def merge_documents(self, job_dirs: List[Path], output_path: Path,
                        title: str = "") -> Path:
        """يدمج مهامًّا منتهية في مستند واحد بفهرس وترقيم أشكال متصلين."""
        from document.merge import collect_images, load_part, merge_plans

        job_dirs = [Path(d) for d in job_dirs]
        if len(job_dirs) < 2:
            raise ValueError("الدمج يحتاج مهمّتين على الأقل.")

        parts = [load_part(job_dir, prefix=f"p{index:02d}",
                           title=humanize_title(job_dir.name))
                 for index, job_dir in enumerate(job_dirs, start=1)]

        merge_dir = output_path.parent / f"{output_path.stem}_merged"
        images_dir = merge_dir / "keyframes"
        collect_images(parts, images_dir)

        plan = merge_plans(parts, title or humanize_title(output_path.stem))
        merge_dir.mkdir(parents=True, exist_ok=True)
        (merge_dir / "plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8")

        # ميتاداتا تمثيلية: المدة مجموع الأجزاء، ولا أبعاد لمصدر مركّب
        metadata = VideoMetadata(
            filename=" + ".join(p.title for p in parts)[:200],
            path=output_path,
            duration_seconds=max(1.0, sum(
                (s.start_timestamp or 0.0) for p in parts
                for s in p.plan.sections[-1:])),
            width=0, height=0, fps=0.0, codec="merged",
            has_audio=True, has_video=False)

        DocumentGenerator(self.config.document).generate(
            plan, metadata, images_dir, output_path)
        logger.info(f"مستند مدموج من {len(parts)} أجزاء: {output_path}")
        return output_path

    def run(
        self,
        video_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[ProgressFn] = None,
        allow_model_download: bool = False,
        allow_audio_only: bool = False,
        clip: Optional[ClipRange] = None,
        transcript_only: bool = False,
    ) -> Path:
        """يعالج مصدرًا حتى مستند Word.

        ``allow_audio_only``: يقبل ملفًا صوتيًا خالصًا (‏mp3، m4a، wav…)
        وينتج مستندًا نصّيًا منسّقًا بلا لقطات. الافتراضي مرفوض حتى يبقى
        اختيار ملف صوتي بالخطأ في منتقي الفيديو خطأً واضحًا.
        """
        cancel_token = cancel_token or CancellationToken()
        clip = clip or ClipRange()
        job_dir = self.job_dir_for(video_path, clip)
        job = JobManager.create_or_resume(job_dir, video_path)
        emit = self._make_emitter(progress_callback)

        # سجلّ التدقيق يلفّ المهمّة كلّها من هنا: ``finally`` بداخله
        # يكتب السطر مهما كانت النهاية — نجاحًا أو إلغاءً أو سقوطًا.
        # سجلٌّ لا يحوي إلا النجاحات لا يصلح للتدقيق أصلًا.
        from utils import audit as audit_module

        with audit_module.record_job(
                self.config, video_path, job_dir,
                enabled=bool(getattr(self.config.application,
                                     "audit_log", False))) as audit:
            return self._run_guarded(job, video_path, cancel_token, emit,
                                     allow_model_download, allow_audio_only,
                                     clip, transcript_only, audit)

    def _run_guarded(self, job, video_path, cancel_token, emit,
                     allow_model_download, allow_audio_only, clip,
                     transcript_only, audit=None) -> Path:
        try:
            # ساعةٌ من المعالجة بلا لمس لوحة المفاتيح تُنيم ويندوز
            # بسياسته الافتراضية، فيقف التنفيذ بلا خطأ ولا تفسير.
            with keep_awake(f"معالجة {video_path.name}"):
                result = self._execute(job, video_path, cancel_token, emit,
                                       allow_model_download, allow_audio_only,
                                       clip, transcript_only)
            job.finish()
            if not self.config.application.keep_temp_on_success:
                self._cleanup_temp(job.job_dir)
            emit(Stage.DOCUMENT, 1.0, "اكتملت المعالجة بنجاح.")
            self._fill_audit(audit, job)
            return result
        except PipelineCancelledError:
            job.cancel()
            logger.info("أُلغيت المهمة بطلب المستخدم.")
            raise
        except Exception as exc:
            job.fail(f"{type(exc).__name__}: {exc}")
            logger.exception("فشل الـ pipeline")
            raise

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

    @staticmethod
    def _fill_audit(audit, job) -> None:
        """يملأ ما لا يُعرف إلا بعد انتهاء المهمّة — ولا يرمي أبدًا.

        المدّة تُقرأ من ``metadata.json`` لا من حقلٍ في الكائن: المهمّة
        المستأنَفة قد تتخطّى مرحلة الميتاداتا كلّها، فالقيمة في الذاكرة
        تبقى صفرًا بينما الملفّ على القرص صحيح.
        """
        if audit is None:
            return
        try:
            audit.job_dir = str(job.job_dir)
            audit.outputs = sorted(
                path.name for path in job.job_dir.iterdir() if path.is_file())
            metadata_path = job.job_dir / "metadata.json"
            if metadata_path.is_file():
                audit.duration_seconds = float(json.loads(
                    metadata_path.read_text(encoding="utf-8")
                ).get("duration_seconds") or 0.0)
        except Exception as exc:
            logger.warning(f"تعذّر استكمال سطر التدقيق: {exc}")

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
                 allow_model_download: bool,
                 allow_audio_only: bool = False,
                 clip: Optional[ClipRange] = None,
                 transcript_only: bool = False) -> Path:
        job_dir = job.job_dir
        temp_dir = job_dir / "temp"
        images_dir = job_dir / "keyframes"
        output_docx = job_dir / f"{video_path.stem}.docx"

        # ---------- 1. التحقق ----------
        job.begin_stage(Stage.VALIDATION)
        emit(Stage.VALIDATION, 0.1,
             "التحقق من الملف الصوتي…" if allow_audio_only
             else "التحقق من ملف الفيديو…")
        self._validate_input(video_path, allow_audio_only)
        cancel_token.raise_if_cancelled()
        job.complete_stage(Stage.VALIDATION)

        # ---------- 2. الميتاداتا ----------
        metadata_fp = self._stage_fp(Stage.METADATA, video_path, clip)
        if (job.is_stage_complete(Stage.METADATA)
                and job.has_artifact("metadata", metadata_fp)):
            metadata = VideoMetadata(**job.load_artifact("metadata"))
            logger.info("تخطي الميتاداتا — ناتج صالح موجود.")
        else:
            job.begin_stage(Stage.METADATA)
            emit(Stage.METADATA, 0.3, "قراءة بيانات الفيديو…")
            facts = extract_video_facts(probe_raw(video_path),
                                        allow_audio_only=allow_audio_only)
            metadata = VideoMetadata(filename=video_path.name, path=video_path,
                                     **facts)
            job.save_artifact("metadata", "metadata.json",
                              metadata.model_dump_json(indent=2),
                              processing_fingerprint=metadata_fp)
            job.complete_stage(Stage.METADATA)
        cancel_token.raise_if_cancelled()
        if metadata.has_video:
            logger.info(
                f"الفيديو: {metadata.width}×{metadata.height} @ "
                f"{metadata.fps:.2f}fps, {metadata.duration_seconds:.1f}s, "
                f"دوران={metadata.rotation}°, صوت={metadata.has_audio}")
        else:
            logger.info(f"مصدر صوتي: {metadata.codec}, "
                        f"{metadata.duration_seconds:.1f}s, "
                        f"{metadata.audio_sample_rate or '?'}Hz")

        clip = clip or ClipRange()
        if clip.is_partial:
            end_label = ("النهاية" if clip.end_seconds is None
                         else seconds_to_display(clip.end_seconds))
            logger.info(f"نطاق جزئي: من {seconds_to_display(clip.start_seconds)}"
                        f" إلى {end_label}")

        # ---------- 3. الصوت + التفريغ ----------
        transcript = self._stage_transcription(
            job, video_path, metadata, temp_dir, cancel_token, emit,
            allow_model_download, clip)

        # ---------- وضع «تفريغ فقط»: نقف هنا ----------
        if transcript_only:
            emit(Stage.DOCUMENT, 0.5, "كتابة ملفات التفريغ…")
            outputs = self._write_transcript_files(
                transcript, job_dir / video_path.stem,
                humanize_title(video_path.stem))
            if not outputs:
                raise DocumentGenerationError(
                    "لا يوجد نص لتصديره — تحقّق من وجود كلام في المصدر.")
            for path in outputs:
                job.register_artifact(f"transcript_{path.suffix.lstrip('.')}",
                                      path)
            job.complete_stage(Stage.SCENE_DETECTION)
            job.complete_stage(Stage.KEYFRAMES)
            job.complete_stage(Stage.MATCHING)
            job.complete_stage(Stage.DOCUMENT)
            logger.info("ملفات التفريغ: "
                        + "، ".join(p.name for p in outputs))
            return outputs[0]

        # ---------- 4-6. المسار البصري — يُتخطّى كليًا لمصدر صوتي ----------
        if metadata.has_video:
            self._prepare_screen_crop(video_path, metadata)
            rotation_plan = resolve_rotation_plan(
                video_path, metadata.rotation,
                metadata.stored_width or metadata.width,
                metadata.stored_height or metadata.height,
                metadata.width, metadata.height, ffmpeg=self.ffmpeg,
                temp_dir=temp_dir)
            if metadata.rotation:
                logger.info(f"خطة الدوران: تصحيح {rotation_plan.correction}° "
                            f"({rotation_plan.reason})")

            scenes = self._stage_scenes(job, video_path, metadata,
                                        cancel_token, emit, rotation_plan,
                                        clip)
            keyframes = self._stage_keyframes(
                job, video_path, metadata, scenes, images_dir, cancel_token,
                emit, rotation_plan, clip)
        else:
            # لا مشاهد ولا لقطات في مصدر صوتي — نُعلّم المرحلتين مكتملتين
            # حتى يبقى التقدّم متّسقًا والاستئناف صحيحًا.
            keyframes = []
            emit(Stage.SCENE_DETECTION, 1.0, "مصدر صوتي — لا تحليل بصري.")
            job.complete_stage(Stage.SCENE_DETECTION)
            job.complete_stage(Stage.KEYFRAMES)

        # ---------- 7. خطة المستند ----------
        # الخطة تُعاد إن تغيّرت طريقة بنائها. بدون هذا الفحص، تشغيل
        # سابق بلا صياغة يترك خطة مكتملة، فيتخطّى الاستئناف مرحلة
        # البناء ولا تُستدعى الصياغة إطلاقًا رغم تفعيلها — والمستخدم
        # يرى مستندًا خامًا دون أي رسالة خطأ.
        expected = self._expected_plan_source()
        transcript_ref = job.state.artifacts.get("transcription")
        keyframes_ref = job.state.artifacts.get("keyframes")
        plan_fp = self._stage_fp(
            Stage.MATCHING, video_path, clip,
            transcript_ref.processing_fingerprint if transcript_ref else None,
            keyframes_ref.processing_fingerprint if keyframes_ref else None,
            expected)
        reusable = (job.is_stage_complete(Stage.MATCHING)
                    and job.has_artifact("plan", plan_fp))
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
            from document.quality import assert_quality_gate, evaluate
            quality = evaluate(plan, keyframes)
            # البوابة تفشل على الخلل البنيوي وحده. درجة قابلية القراءة
            # تُسجَّل وتُتابَع ولا تُفرَض بعد: المخرج الحالي ضعيف فيها
            # بحكم تصميمه، ففرض عتبة اليوم يُسقط كل مهمة عقابًا على عيب
            # لم يُصلَح. انظر ``document/quality.assert_quality_gate``.
            assert_quality_gate(quality)
            plan.quality_score = quality.overall
            plan.quality_warnings = list(quality.warnings)
            job.save_artifact("plan", "plan.json", plan.model_dump_json(indent=2),
                              processing_fingerprint=plan_fp)
            job.save_artifact("quality", "quality.json",
                              json.dumps(quality.as_dict(), ensure_ascii=False, indent=2),
                              processing_fingerprint=plan_fp)
            if quality.warnings:
                logger.warning("بوابة الجودة: " + " | ".join(quality.warnings))
            if quality.overall is not None:
                readable = ", ".join(
                    f"{m.name}={m.value:.0f}%"
                    for m in quality.metrics if m.applicable)
                logger.info("قابلية القراءة: %.1f%%  (%s)",
                            quality.overall, readable)
            job.complete_stage(Stage.MATCHING)
        cancel_token.raise_if_cancelled()

        # ---------- 8. المستند ----------
        job.begin_stage(Stage.DOCUMENT)

        # الحزمة التعليمية قبل المستند: هي المسار الوحيد هنا الذي قد
        # يستغرق دقائق أو يكلّف مالًا، وتقديمها يجعل الإلغاء أثناءها
        # لا يُضيّع مستندًا مكتوبًا بالفعل.
        study_pack = self._build_study_pack(
            plan, transcript, keyframes, job, emit)

        emit(Stage.DOCUMENT, 0.5, "كتابة مستند Word…")
        self.document_generator.generate(plan, metadata, images_dir, output_docx)
        document_fp = self._stage_fp(
            Stage.DOCUMENT, video_path, clip,
            job.state.artifacts.get("plan").processing_fingerprint
            if job.state.artifacts.get("plan") else None,
            self.config.document.subtitle_formats if self.config.document.export_subtitles else None)
        job.register_artifact("document", output_docx, document_fp)

        # مخرج إضافي لا شرط نجاح: فشله لا يُفشل المهمة
        emit(Stage.DOCUMENT, 0.85, "كتابة ملفات الترجمة…")
        self._write_subtitles(transcript, output_docx, job)

        self._write_translations(transcript, output_docx, job)

        emit(Stage.DOCUMENT, 0.93, "تصيير الصيغ الإضافية…")
        self._write_exports(plan, metadata, images_dir, output_docx, job,
                            docx_path=output_docx, transcript=transcript,
                            source_media=video_path, study_pack=study_pack)

        job.complete_stage(Stage.DOCUMENT)
        return output_docx

    # ------------------------------------------------------------------
    @staticmethod
    def _write_transcript_files(transcript, base_path: Path,
                                title: str) -> List[Path]:
        """يكتب النص وMarkdown والترجمات — بلا مستند Word."""
        from document.transcript_export import write_transcript

        return write_transcript(transcript, base_path, title)

    def _write_subtitles(self, transcript, output_docx: Path, job) -> None:
        """يكتب SRT/VTT بجوار المستند من توقيت الكلمات المحفوظ."""
        if not self.config.document.export_subtitles or not transcript.segments:
            return
        try:
            from document.subtitles import write_subtitles

            written = write_subtitles(
                transcript, output_docx,
                self.config.document.subtitle_formats)
            for path in written:
                job.register_artifact(f"subtitles_{path.suffix.lstrip('.')}",
                                      path)
            if written:
                logger.info("ملفات الترجمة: "
                            + "، ".join(p.name for p in written))
        except Exception as exc:
            logger.warning(f"تعذّرت كتابة ملفات الترجمة: {exc}")

    def _build_study_pack(self, plan, transcript, keyframes, job, emit):
        """يبني حزمة المذاكرة ويحفظها في ``study.json`` — أو يعيد ``None``.

        **لا يُفشل المهمة أبدًا.** الحزمة مخرج إضافي كالترجمات تمامًا:
        غياب المزوّد أو سقوطه يترك المستند كما يخرج اليوم بالضبط.

        **ولا يُستدعى النموذج مرّتين لنفس الخطة.** التوليد هو الخطوة
        الوحيدة في البرنامج التي قد تكلّف مالًا أو دقائق انتظار، فحزمةٌ
        محفوظة بالتوقيع نفسه تُعاد كما هي.
        """
        settings = self.config.study
        if not settings.enabled or not transcript.segments:
            return None

        from config.schemas import StudyPack

        signature = self._study_signature()
        study_path = job.job_dir / "study.json"
        if study_path.is_file():
            try:
                stored = StudyPack(**json.loads(
                    study_path.read_text(encoding="utf-8")))
                if stored.generated_by == signature and not stored.is_empty():
                    logger.info("حزمة تعليمية محفوظة بالتوقيع نفسه — أُعيد استعمالها.")
                    return stored
            except Exception as exc:
                logger.warning(f"تعذّرت قراءة study.json المحفوظ: {exc}")

        emit(Stage.DOCUMENT, 0.05, "توليد الحزمة التعليمية…")
        try:
            pack = self._generate_study_pack(plan, transcript, keyframes, emit)
        except Exception as exc:
            logger.warning(f"تعذّرت الحزمة التعليمية: {exc}")
            return None

        if pack is None or pack.is_empty():
            return None
        try:
            atomic_write_text(study_path, pack.model_dump_json(indent=2))
            job.register_artifact("study", study_path)
        except Exception as exc:
            logger.warning(f"تعذّر حفظ study.json: {exc}")
        return pack

    def _study_signature(self) -> str:
        settings = self.config.study
        if not settings.enabled:
            return "none"
        return (f"ai:{settings.provider}"
                + (f"/{settings.model}" if settings.model else ""))

    def _generate_study_pack(self, plan, transcript, keyframes, emit):
        """يختار المسار: نموذج لغوي، أو المسار الإحصائي عند تعذّره."""
        from ai.providers import build_provider
        from ai.study_builder import (
            StudyBuilder,
            StudyConfig,
            build_without_model,
        )

        settings = self.config.study
        config = StudyConfig(**{
            key: getattr(settings, key) for key in StudyConfig.__dataclass_fields__
            if hasattr(settings, key)})

        provider = None
        try:
            provider = build_provider(
                settings.provider, model=settings.model,
                base_url=settings.base_url, api_key_env=settings.api_key_env,
                api_key=settings.api_key, workspace_id=settings.workspace_id)
            available = provider.is_available()
        except Exception as exc:
            logger.warning(f"تعذّر تجهيز مزوّد الحزمة التعليمية: {exc}")
            available = False

        if not available:
            if not settings.fallback_without_model:
                logger.warning(
                    f"مزوّد الحزمة التعليمية «{settings.provider}» غير متاح — "
                    "أُلغيت الحزمة.")
                return None
            # صادقٌ ومفيد: قائمة مصطلحات بلا ادّعاء تعريفات، وهي نفسها
            # ما يُلصق في خانة «مصطلحات المادة» فيرفع دقّة المحاضرة التالية.
            logger.info(
                f"مزوّد الحزمة التعليمية «{settings.provider}» غير متاح — "
                "أُخرجت قائمة المصطلحات وحدها.")
            return build_without_model(plan, transcript, keyframes,
                                       limit=settings.max_glossary)

        def report(done: int, total: int) -> None:
            emit(Stage.DOCUMENT, 0.05 + 0.35 * (done / max(1, total)),
                 f"توليد الحزمة التعليمية… ({done}/{total})")

        return StudyBuilder(provider, config).build(
            plan, transcript, keyframes, progress=report)

    def _write_translations(self, transcript, output_docx: Path, job) -> None:
        """يترجم ملفّات الترجمة إلى اللغات المطلوبة — مخرج إضافي لا شرط.

        يستعمل مزوّد الحزمة التعليمية نفسه: مزوّدٌ ثانٍ بمفتاح ثانٍ
        وإعدادٍ ثانٍ لنفس النوع من العمل تعقيدٌ بلا مقابل.
        """
        targets = [str(t).strip().lower()
                   for t in (getattr(self.config.document, "translate_to", [])
                             or []) if str(t).strip()]
        if not targets or not transcript.segments:
            return
        try:
            from ai.providers import build_provider
            from ai.translator import TranslationConfig, translate_subtitles

            settings = self.config.study
            provider = build_provider(
                settings.provider, model=settings.model,
                base_url=settings.base_url, api_key_env=settings.api_key_env,
                api_key=settings.api_key, workspace_id=settings.workspace_id)
            if not provider.is_available():
                logger.info(
                    f"مزوّد الترجمة «{settings.provider}» غير متاح — "
                    "تُخطّى الترجمة.")
                return
            for target in targets:
                for path in translate_subtitles(
                        transcript, output_docx, provider,
                        TranslationConfig(
                            target=target,
                            timeout_seconds=settings.timeout_seconds),
                        self.config.document.subtitle_formats):
                    job.register_artifact(f"translation_{target}", path)
        except Exception as exc:
            logger.warning(f"تعذّرت الترجمة: {exc}")

    def _write_exports(self, plan, metadata, images_dir: Path,
                       base_path: Path, job,
                       docx_path: Optional[Path] = None,
                       transcript=None,
                       source_media: Optional[Path] = None,
                       study_pack=None) -> List[Path]:
        """يصيّر الصيغ الإضافية من الخطة نفسها — بعقد الترجمات ذاته.

        مخرجات إضافية لا شروط نجاح: ``write_exports`` يعزل كل مُصيِّر،
        وهذا الغلاف يعزل الوحدة كلّها. مستند Word وحده هو ما يُفشل
        فشلُه المهمة.
        """
        formats = list(getattr(self.config.document, "export_formats", []) or [])
        if not formats:
            return []
        try:
            from document.exporters import ExportContext, write_exports

            written = write_exports(
                ExportContext(
                    plan=plan,
                    metadata=metadata,
                    images_dir=images_dir,
                    base_path=base_path,
                    docx_path=docx_path,
                    transcript=transcript,
                    source_media=source_media,
                    document_config=self.config.document,
                    options={"study_pack": study_pack} if study_pack else {},
                ),
                formats)
        except Exception as exc:
            logger.warning(f"تعذّرت الصيغ الإضافية: {exc}")
            return []

        if job is not None:
            for path in written:
                job.register_artifact(
                    f"export_{path.name.split('.', 1)[-1].replace('.', '_')}",
                    path)
        return written

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
        subtitle = ("مستند مُولَّد من تسجيل مرئي" if metadata.has_video
                    else "مستند مُولَّد من تسجيل صوتي")
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
                # السبب يُعرض في الواجهة لا في السجلّ وحده: المستخدم
                # الذي يرى «تعذّرت» بلا سبب لا يستطيع فعل شيء، والمستخدم
                # الذي يرى «مفتاح غير صالح» يُصلحها في دقيقة.
                reason = str(exc).strip() or type(exc).__name__
                logger.warning(
                    f"تعذّرت إعادة الصياغة — المتابعة بالنص الخام. السبب: {reason}")
                emit(Stage.MATCHING, 0.85,
                     f"تعذّرت إعادة الصياغة ({reason[:120]}) — "
                     "المتابعة بالنص الخام")

        emit(Stage.MATCHING, 0.9, "بناء بنية المستند…")
        return self.planner.build(transcript, keyframes, title, subtitle)

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

    def _stage_transcription(self, job, video_path, metadata, temp_dir,
                             cancel_token, emit, allow_model_download,
                             clip: Optional[ClipRange] = None
                             ) -> TranscriptionResult:
        transcription_fp = self._stage_fp(Stage.TRANSCRIPTION, video_path, clip)
        if (job.is_stage_complete(Stage.TRANSCRIPTION)
                and job.has_artifact("transcription", transcription_fp)):
            logger.info("تخطي التفريغ — ناتج صالح موجود.")
            return TranscriptionResult(**job.load_artifact("transcription"))

        if not metadata.has_audio:
            logger.warning("لا يوجد مسار صوتي — سيُنتَج مستند بالصور فقط.")
            transcript = TranscriptionResult(
                language=self.config.whisper.language,
                full_text_raw="", full_text_clean="", segments=[], words=[],
                engine={"name": "none", "reason": "no_audio_stream"})
            job.save_artifact("transcription", "transcription.json",
                              transcript.model_dump_json(indent=2),
                              processing_fingerprint=transcription_fp)
            job.complete_stage(Stage.AUDIO_EXTRACTION)
            job.complete_stage(Stage.TRANSCRIPTION)
            return transcript

        # استخراج الصوت
        audio_path = temp_dir / "audio_16k.wav"
        clip = clip or ClipRange()
        audio_fp = self._stage_fp(Stage.AUDIO_EXTRACTION, video_path, clip)
        if not (job.is_stage_complete(Stage.AUDIO_EXTRACTION)
                and job.has_artifact("audio", audio_fp)
                and audio_path.exists()):
            job.begin_stage(Stage.AUDIO_EXTRACTION)
            denoise = self.config.application.denoise_audio
            normalize = getattr(self.config.application,
                                "normalize_audio", True)
            emit(Stage.AUDIO_EXTRACTION, 0.2,
                 "استخراج المسار الصوتي وتنظيفه…" if denoise
                 else "استخراج المسار الصوتي…")
            self._report_audio_level(video_path, normalize)
            self.ffmpeg.extract_audio(
                video_path, audio_path,
                start_seconds=clip.start_seconds,
                end_seconds=clip.end_seconds,
                denoise=denoise, normalize=normalize)
            audio_fp = self._stage_fp(Stage.AUDIO_EXTRACTION, video_path, clip)
            job.register_artifact("audio", audio_path, audio_fp)
            job.complete_stage(Stage.AUDIO_EXTRACTION)
        cancel_token.raise_if_cancelled()

        # النموذج
        job.begin_stage(Stage.TRANSCRIPTION)
        self._autofill_screen_glossary(video_path, metadata, emit)
        emit(Stage.TRANSCRIPTION, 0.02, "تجهيز نموذج التفريغ…")
        # الإذن يُمرَّر إلى المحرّك أيضًا، لا إلى مدير النماذج وحده:
        # هو الحاجز الذي يمنع التنزيل فعليًا داخل faster-whisper.
        self.transcriber.allow_download = allow_model_download

        engine_label = getattr(getattr(self.transcriber, "info", None),
                               "name", "faster-whisper")
        # أوزان Whisper تخصّ محرّك Whisper وحده. كان هذا الفحص يُنفَّذ
        # بلا شرط، فأيّ محرّك آخر — أو محرّك مُحقَن في اختبار — يسقط
        # بـ ``ModelUnavailableError`` يطالب بـ 1.7GB لا يستعملها.
        # الشرط على المحرّك لا على الإعداد: ``engine_label`` هو ما يعمل
        # فعلًا، بينما ``config.engine`` قد يكون قديمًا بعد حقن محرّك.
        if engine_label.startswith("faster-whisper"):
            self.model_manager.ensure_available(
                self.config.whisper.model_size,
                allow_download=allow_model_download,
                progress_callback=lambda m: emit(Stage.TRANSCRIPTION, 0.03, m))
        logger.info(f"محرّك التفريغ: {engine_label} · "
                    f"النموذج {self.config.whisper.model_size}")

        # ── الاستئناف داخل التفريغ ────────────────────────────────────
        # المرحلة تستغرق 85٪ من زمن التشغيل — أربع ساعات ونصف على
        # محاضرة ثلاث ساعات. وكان الاستئناف على مستوى المرحلة وحدها:
        # انقطاعٌ في الساعة الرابعة يبدأ من الصفر.
        full = metadata.duration_seconds
        end = clip.end_seconds if clip.end_seconds is not None else full
        audio_seconds = max(0.0, min(end, full) - clip.start_seconds)

        resume = self._load_checkpoint(job, transcription_fp)
        transcribe_from = audio_path
        if resume is not None:
            emit(Stage.TRANSCRIPTION, 0.04,
                 f"استئناف التفريغ من {seconds_to_display(resume.resume_at)}…")
            logger.info(
                f"استئناف تفريغ محفوظ: {len(resume.segments)} مقطعًا حتى "
                f"{seconds_to_display(resume.resume_at)}.")
            # قصٌّ دقيق عند حدّ المقطع: ملف PCM، فـ``-ss`` عليه ليس
            # بحثًا تقريبيًّا إلى إطار مفتاحي. لا تكرار ولا سقوط.
            transcribe_from = temp_dir / "audio_resume.wav"
            # بلا تطبيع: ``audio_path`` مطبَّع أصلًا، وتطبيعه ثانيةً
            # يجعل الجزء المستأنَف بمستوى مختلف عن أوّل الملفّ —
            # فيتغيّر سلوك VAD عند حدّ الاستئناف بالضبط.
            self.ffmpeg.extract_audio(audio_path, transcribe_from,
                                      start_seconds=resume.resume_at,
                                      normalize=False)

        transcript = self._transcribe_with_fallback(
            transcribe_from, cancel_token, emit, engine_label,
            checkpoint=self._checkpoint_writer(
                job, transcription_fp, audio_seconds),
            resume=resume)
        # الصوت المستخرج يبدأ من الصفر؛ توقيتات المستند يجب أن تبقى
        # مطلقة بزمن المصدر الأصلي وإلا تعذّر الرجوع إلى الفيديو.
        transcript = _shift_transcript(transcript, clip.start_seconds)
        job.save_artifact("transcription", "transcription.json",
                          transcript.model_dump_json(indent=2),
                          processing_fingerprint=transcription_fp)
        # اكتمل، فلا معنى لبقاء نقطة الحفظ: وجودها بعد النجاح يعني
        # استئنافًا من منتصف عملٍ تامّ عند إعادة تشغيل لاحقة.
        self._checkpoint_path(job).unlink(missing_ok=True)
        job.complete_stage(Stage.TRANSCRIPTION)
        return transcript

    # ------------------------------------------------------------------
    @staticmethod
    def _checkpoint_path(job) -> Path:
        return job.job_dir / "transcription.partial.json"

    def _load_checkpoint(self, job, fingerprint: str
                         ) -> Optional[TranscriptionCheckpoint]:
        """نقطة حفظٍ صالحة، أو ``None``.

        ثلاثة أسباب للرفض، وكلّها تُسجَّل ولا يفشل بها التشغيل — نقطة
        الحفظ تسريعٌ لا شرطُ صحّة:

        * المحرّك لا يستأنف (``supports_resume``).
        * البصمة مختلفة: تغيّر الإعداد أو المصدر، فالمقاطع المحفوظة
          نتاج إعدادٍ آخر ولا يجوز خلطها بمقاطع الإعداد الجديد.
        * الملف تالف أو مقطوع — وهو ما يحدث بالضبط عند الانقطاع الذي
          وُجدت له نقطة الحفظ.
        """
        if not getattr(self.transcriber, "supports_resume", False):
            return None
        path = self._checkpoint_path(job)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = TranscriptionCheckpoint(**data)
        except Exception as exc:                            # noqa: BLE001
            logger.info(f"نقطة حفظ التفريغ غير صالحة ({exc}) — تُتجاهَل.")
            path.unlink(missing_ok=True)
            return None

        if checkpoint.processing_fingerprint != fingerprint:
            logger.info("نقطة حفظ التفريغ لإعدادٍ مختلف — تُتجاهَل.")
            path.unlink(missing_ok=True)
            return None
        if checkpoint.resume_at <= 0 or not checkpoint.segments:
            path.unlink(missing_ok=True)
            return None
        return checkpoint

    def _checkpoint_writer(self, job, fingerprint: str, audio_seconds: float):
        """يُعيد دالّةً تكتب نقطة الحفظ، أو ``None`` إن كان المحرّك لا يستأنف.

        ``None`` صريحة لا دالّة فارغة: كتابة نقاطٍ لا يقرؤها أحد تُوهم
        المستخدم بأمانٍ لا وجود له.
        """
        if not getattr(self.transcriber, "supports_resume", False):
            return None

        path = self._checkpoint_path(job)

        def write(segments, words, resume_at: float) -> None:
            checkpoint = TranscriptionCheckpoint(
                processing_fingerprint=fingerprint,
                audio_seconds=audio_seconds,
                resume_at=resume_at,
                segments=segments,
                words=words,
            )
            try:
                atomic_write_text(path, checkpoint.model_dump_json())
            except Exception as exc:                        # noqa: BLE001
                # قرصٌ ممتلئ أو مجلّد مقفل لا يجوز أن يُسقط تفريغًا
                # جاريًا منذ ساعات — أسوأ ما يحدث فقدان الاستئناف.
                logger.debug(f"تعذّرت كتابة نقطة حفظ التفريغ: {exc}")

        return write

    def _transcribe_with_fallback(self, audio_path, cancel_token, emit,
                                  engine_label: str, checkpoint=None,
                                  resume=None):
        """يفرّغ بالمحرّك المختار، ويسقط إلى الافتراضي إن فشل **أثناء التنفيذ**.

        فحص ``is_available`` يغطي وجود الحزم فقط. المحرّك الاختياري قد
        ينجح في الفحص ثم يفشل عند تحميل أوزانه — نموذج مقيّد الوصول على
        HuggingFace، أو ذاكرة غير كافية، أو انقطاع تنزيل. كانت النتيجة
        فشل المهمة كلها بعد دقائق من العمل، وقد نجحت مراحلها السابقة.

        هذا هو بالضبط نمط العطل الذي أُصلح في السقوط من CUDA إلى المعالج
        (يغطي التحميل **والتنفيذ**)، وفي إعادة الصياغة (فشلها لا يُفشل
        المهمة). المحرّك الاختياري يتبع القاعدة نفسها الآن.
        """
        progress = lambda f, m: emit(Stage.TRANSCRIPTION, f, m)  # noqa: E731
        # الوسيطان يُمرَّران **فقط** لمن أعلن أنه يستأنف. محرّك قديم أو
        # مُحقَن في اختبار يبقى بتوقيعه الثلاثي ولا ينكسر — وهو ما
        # انكسر فعلًا عند أول تمرير غير مشروط.
        extra = {}
        if getattr(self.transcriber, "supports_resume", False):
            extra = {"checkpoint": checkpoint, "resume": resume}
        try:
            return self.transcriber.transcribe(audio_path, cancel_token,
                                               progress, **extra)
        except PipelineCancelledError:
            raise
        except Exception as exc:
            if engine_label == "faster-whisper":
                raise      # الافتراضي نفسه فشل: لا بديل نسقط إليه

            logger.warning(
                f"فشل محرّك «{engine_label}» أثناء التنفيذ ({exc}) — "
                "المتابعة بمحرّك Whisper الافتراضي.")
            emit(Stage.TRANSCRIPTION, 0.02,
                 f"تعذّر محرّك {engine_label} — المتابعة بـ Whisper")

            from audio.engines.registry import build_engine

            fallback = build_engine("faster-whisper",
                                    config=self.config.whisper)
            fallback.allow_download = self.transcriber.allow_download
            self.transcriber = fallback
            # بلا نقطة حفظ ولا استئناف هنا عن قصد: هذا المسار لا يُبلَغ
            # إلا من محرّك لا يستأنف، فـ``resume`` صفرٌ حتمًا و
            # ``audio_path`` الصوت كاملًا. تمريرُ نقطة حفظ لمحرّكٍ بديل
            # بدأ من الصفر يكتب تقدّمًا لا يطابق ما على القرص.
            return fallback.transcribe(audio_path, cancel_token, progress)

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
        for index, kf in enumerate(keyframes):
            cancel_token.raise_if_cancelled()
            emit(Stage.KEYFRAMES, 0.85 + 0.15 * (index / total),
                f"استخراج نص الشاشة… ({index + 1}/{total})")
            kf.ocr_text = extract_text(images_dir / kf.filename)
