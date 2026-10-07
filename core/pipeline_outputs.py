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
        # **لا يُشترط أن يختلف اسم المزوّد.** رُصد على تشغيل حقيقي: الصياغة
        # عبر «anthropic» نجحت، والحزمة التعليمية مضبوطة على «anthropic»
        # أيضًا لكن بلا مفتاح (المفتاح محفوظ في إعداد الصياغة وحده)، فكان
        # الشرط ``rewrite.provider != study.provider`` يمنع البديل — وخرجت
        # الحزمة بلا أهداف ولا أسئلة وفي السجلّ «anthropic غير متاح».
        if not (getattr(study_settings, "use_rewrite_provider_as_fallback", True)
                and rewrite.enabled and rewrite.provider):
            return None
        try:
            from ai.providers import provider_from_settings

            provider = provider_from_settings(rewrite)
            if not provider.is_available():
                return None
        except Exception as exc:
            logger.debug(f"مزوّد الصياغة غير صالح بديلًا للحزمة: {exc}")
            return None
        if rewrite.provider == study_settings.provider:
            logger.info(
                f"إعداد الحزمة التعليمية «{study_settings.provider}» بلا مفتاح — "
                "تُبنى الحزمة بإعداد الصياغة (المزوّد نفسه ومفتاحه).")
        else:
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
                provider = self._rewrite_provider_for_study(settings)
            if provider is None or not provider.is_available():
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
                    options={
                        **({"study_pack": study_pack} if study_pack else {}),
                        "scorm_mastery": getattr(
                            self.config.document, "scorm_mastery", 70),
                    },
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

    def _build_plan(self, transcript, keyframes, video_path, metadata, emit,
                    images_dir=None) -> DocumentPlan:
        """يبني بنية الوثيقة — مرئيًّا، أو بإعادة صياغة، وإلا زمنيًا.

        الترتيب من الأغنى إلى الأبسط، وكل فشل يسقط إلى التالي ولا يُفشل
        المهمة: المستند الخام أفضل من لا مستند.

        1. المؤلّف المرئي (‏``ai/visual_author``): اللقطات صورًا + التفريغ.
        2. الصياغة النصّية (‏``ai/rewriter``): التفريغ + نصّ الشاشة.
        3. المخطّط الزمني: التفريغ كما هو بين اللقطات.
        """
        title = humanize_title(video_path.stem)
        # لا نكرّر اسم الملف هنا: هو في جدول الغلاف أصلًا، وتكراره
        # يُطيل العنوان الفرعي بلا فائدة.
        subtitle = ("مستند مُولَّد من تسجيل مرئي" if metadata.has_video
                    else "مستند مُولَّد من تسجيل صوتي")
        settings = self.config.rewrite
        anonymize = getattr(self.config.document, "anonymize_names", False)
        has_text = any((s.text_clean or s.text_raw or "").strip()
                       for s in transcript.segments)
        #: ما يجب أن يعرفه المستخدم عن طريقة بناء المستند — يُلحق بتحذيرات
        #: الجودة. كان سقوط الوضع المرئي و14 دفعة خامًا يمرّان سطرًا في
        #: السجلّ وحده، والمستند يحمل وسم «ai:».
        self._plan_notes = []
        try:
            from ai.providers import set_response_dump_dir

            set_response_dump_dir(Path(images_dir).parent if images_dir else None)
        except Exception:                                   # noqa: BLE001
            pass

        from ai.providers import FatalProviderError

        if settings.enabled:
            provider = None
            try:
                from ai.providers import provider_from_settings

                provider = provider_from_settings(settings)
                if not provider.is_available():
                    raise RuntimeError(
                        f"المزوّد «{provider.info.label_ar}» غير متاح.")
            except Exception as exc:
                reason = str(exc).strip() or type(exc).__name__
                logger.warning(f"تعذّرت إعادة الصياغة — السبب: {reason}")
                self._plan_notes.append(
                    f"ai_unavailable: تعذّرت إعادة الصياغة ({reason[:160]}) — "
                    "المستند نصٌّ خام")
                emit(Stage.MATCHING, 0.85,
                     f"تعذّرت إعادة الصياغة ({reason[:120]}) — "
                     "المتابعة بالنص الخام")
                provider = None

            if (provider is not None and getattr(settings, "send_images", True)
                    and images_dir is not None):
                from ai.visual_author import VisualAuthor, VisualAuthorConfig

                unredacted = [k for k in keyframes if not getattr(k, "redacted", False)]
                blocked = (anonymize and unredacted and not provider.info.is_local
                           and not getattr(settings, "send_unredacted_images", False))
                if blocked and VisualAuthor.usable(provider, keyframes):
                    logger.warning(
                        "إخفاء الأسماء مطلوب ولم تُموَّه اللقطات (Tesseract غير "
                        "مثبَّت؟) — لن تُرسل اللقطات، والصياغة نصّية. ثبّت "
                        "Tesseract، أو فعّل rewrite.send_unredacted_images.")
                    self._plan_notes.append(
                        "ai_visual: اللقطات لم تُموَّه فيها الأسماء فلم تُرسل — "
                        "كُتب المستند بالصياغة النصّية")
                    emit(Stage.MATCHING, 0.1,
                         "اللقطات لم تُموَّه فيها الأسماء — لن تُرسل؛ صياغة نصّية")
                elif VisualAuthor.usable(provider, keyframes):
                    emit(Stage.MATCHING, 0.1, "كتابة المستند من لقطات الشاشة والكلام…")
                    try:
                        author = VisualAuthor(provider, VisualAuthorConfig(
                            max_images_per_call=getattr(settings, "max_images_per_call", 12),
                            # ردٌّ بـ16000 رمز يستغرق دقائق؛ 300 ث كانت تقطعه.
                            timeout_seconds=max(600, settings.timeout_seconds),
                            glossary=self.config.whisper.glossary or "",
                            anonymize_names=anonymize))
                        plan = author.build_plan(
                            transcript, keyframes, Path(images_dir), title, "",
                            duration_seconds=metadata.duration_seconds,
                            progress_callback=lambda f, m: emit(
                                Stage.MATCHING, 0.1 + f * 0.8, m))
                        logger.info(f"كُتب المستند مرئيًّا عبر {plan.generated_by} "
                                    f"({author.images_sent} لقطة أُرسلت)")
                        return plan
                    except FatalProviderError:
                        raise               # لا نُكمل بنصّ خام بعد نفاد الرصيد
                    except Exception as exc:
                        reason = str(exc).strip() or type(exc).__name__
                        logger.warning(
                            f"تعذّر الوضع المرئي — المتابعة بالصياغة النصّية. السبب: {reason}")
                        self._plan_notes.append(
                            f"ai_visual: تعذّر الوضع المرئي ({reason[:160]}) — "
                            "كُتب المستند بالصياغة النصّية")
                        emit(Stage.MATCHING, 0.15,
                             f"تعذّر الوضع المرئي ({reason[:100]}) — صياغة نصّية")
                elif keyframes:
                    logger.info(f"المزوّد «{provider.info.name}» لا يقرأ الصور — "
                                "صياغة نصّية بلا لقطات.")

            if provider is not None and has_text:
                emit(Stage.MATCHING, 0.1, "إعادة صياغة النص…")
                try:
                    from ai.rewriter import RewriteConfig, TranscriptRewriter

                    rewriter = TranscriptRewriter(
                        provider,
                        RewriteConfig(enabled=True, provider=settings.provider,
                                      model=settings.model,
                                      batch_chars=settings.batch_chars,
                                      make_outline=settings.make_outline,
                                      timeout_seconds=settings.timeout_seconds,
                                      glossary=self.config.whisper.glossary or "",
                                      anonymize_names=anonymize))
                    plan = rewriter.build_plan(
                        transcript, keyframes, title, subtitle,
                        lambda f, m: emit(Stage.MATCHING, 0.1 + f * 0.8, m))
                    logger.info(f"أُعيدت الصياغة عبر {plan.generated_by}")
                    raw, total = rewriter.raw_batches, rewriter.total_batches
                    if raw:
                        self._plan_notes.append(
                            f"ai_raw_batches: {raw} من {total} قسمًا بقي نصًّا خامًا "
                            f"لأن ردّ النموذج لم يُستعمل ({rewriter.last_failure[:120]}) "
                            "— الردود محفوظة في ai_debug")
                    return plan
                except FatalProviderError:
                    raise
                except Exception as exc:
                    # السبب يُعرض في الواجهة لا في السجلّ وحده: المستخدم
                    # الذي يرى «تعذّرت» بلا سبب لا يستطيع فعل شيء، والمستخدم
                    # الذي يرى «مفتاح غير صالح» يُصلحها في دقيقة.
                    reason = str(exc).strip() or type(exc).__name__
                    logger.warning(
                        f"تعذّرت إعادة الصياغة — المتابعة بالنص الخام. السبب: {reason}")
                    self._plan_notes.append(
                        f"ai_unavailable: تعذّرت إعادة الصياغة ({reason[:160]}) — "
                        "المستند نصٌّ خام")
                    emit(Stage.MATCHING, 0.85,
                         f"تعذّرت إعادة الصياغة ({reason[:120]}) — "
                         "المتابعة بالنص الخام")

        emit(Stage.MATCHING, 0.9, "بناء بنية المستند…")
        return self.planner.build(transcript, keyframes, title, subtitle)
