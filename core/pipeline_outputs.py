"""مخرجات خط المعالجة: الخطة والصياغة، والترجمات، والحزمة التعليمية، والصيغ الإضافية.

منقولة من ``core/pipeline.py`` كما هي (مزيج يرثه ``VideoToDocPipeline``). لا تغيير في السلوك.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from config.schemas import (
    DocumentPlan,
)
from core.job_state import Stage, atomic_write_text
from utils.logger import logger
from utils.timestamps import humanize_title


class OutputsMixin:
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

    def _rewrite_provider_for_study(self, study_settings):
        """مزوّد إعادة الصياغة بديلًا للحزمة التعليمية — إن كان متاحًا.

        لا يُستعمل إلا إن فُعّلت الصياغة في هذه المهمة: المستخدم رضي
        بإرسال النص إلى هذا المزوّد أصلًا، فلا يُفتح بابُ خروجٍ جديد.
        """
        rewrite = self.config.rewrite
        if not (getattr(study_settings, "use_rewrite_provider_as_fallback", True)
                and rewrite.enabled and rewrite.provider
                and rewrite.provider != study_settings.provider):
            return None
        try:
            from ai.providers import provider_from_settings

            provider = provider_from_settings(rewrite)
            if not provider.is_available():
                return None
        except Exception as exc:
            logger.debug(f"مزوّد الصياغة غير صالح بديلًا للحزمة: {exc}")
            return None
        logger.info(
            f"مزوّد الحزمة التعليمية «{study_settings.provider}» غير متاح — "
            f"تُبنى الحزمة بمزوّد الصياغة «{rewrite.provider}».")
        return provider

    def _generate_study_pack(self, plan, transcript, keyframes, emit):
        """يختار المسار: نموذج لغوي، أو المسار الإحصائي عند تعذّره."""
        from ai.providers import provider_from_settings
        from ai.study_builder import (
            StudyBuilder,
            StudyConfig,
            build_without_model,
        )

        settings = self.config.study
        config = StudyConfig(**{
            key: getattr(settings, key) for key in StudyConfig.__dataclass_fields__
            if hasattr(settings, key)})
        config.anonymize_names = getattr(self.config.document,
                                         "anonymize_names", False)

        provider = None
        try:
            provider = provider_from_settings(settings)
            available = provider.is_available()
        except Exception as exc:
            logger.warning(f"تعذّر تجهيز مزوّد الحزمة التعليمية: {exc}")
            available = False

        if not available:
            substitute = self._rewrite_provider_for_study(settings)
            if substitute is not None:
                provider = substitute
                available = True
                config.provider = self.config.rewrite.provider
                config.model = self.config.rewrite.model

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
            from ai.providers import provider_from_settings
            from ai.translator import TranslationConfig, translate_subtitles

            settings = self.config.study
            provider = provider_from_settings(settings)
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
                from ai.providers import provider_from_settings
                from ai.rewriter import RewriteConfig, TranscriptRewriter

                provider = provider_from_settings(settings)
                if not provider.is_available():
                    raise RuntimeError(
                        f"المزوّد «{provider.info.label_ar}» غير متاح.")
                rewriter = TranscriptRewriter(
                    provider,
                    RewriteConfig(enabled=True, provider=settings.provider,
                                  model=settings.model,
                                  batch_chars=settings.batch_chars,
                                  make_outline=settings.make_outline,
                                  timeout_seconds=settings.timeout_seconds,
                                  glossary=self.config.whisper.glossary or "",
                                  anonymize_names=getattr(
                                      self.config.document, "anonymize_names", False)))
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
