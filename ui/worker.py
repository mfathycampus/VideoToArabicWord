"""العامل الخلفي بنمط Worker Object + moveToThread (ADR-001).

لا معالجة داخل خيط الواجهة إطلاقًا، ولا ``terminate()`` — الإلغاء تعاوني
عبر ``CancellationToken``.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from core.exceptions import PipelineCancelledError
from core.pipeline import VideoToDocPipeline
from utils.cancellation import CancellationToken
from utils.error_reporting import format_error_for_user
from utils.logger import logger


class BatchWorker(QObject):
    """يعالج مجلدًا كاملًا على خيط خلفي.

    فشل ملف لا يوقف الدفعة: من يشغّل مقرّرًا ليلًا يجب أن يجد صباحًا كل
    ما نجح، وتقريرًا بما فشل.
    """

    progress = pyqtSignal(float, str)
    file_done = pyqtSignal(str, bool, str)     # الاسم، نجح؟، التفصيل
    completed = pyqtSignal(int, int)           # نجح، الإجمالي
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, sources, output_dir, config, cancel_token,
                 allow_model_download: bool = False,
                 transcript_only: bool = False, clip=None) -> None:
        super().__init__()
        self.sources = list(sources)
        self.output_dir = output_dir
        self.config = config
        self.cancel_token = cancel_token
        self.allow_model_download = allow_model_download
        self.transcript_only = transcript_only
        self.clip = clip

    @pyqtSlot()
    def run(self) -> None:
        try:
            from core.batch import process_folder

            def on_progress(index, total, name, pct):
                overall = ((index - 1) + pct / 100.0) / max(1, total) * 100.0
                self.progress.emit(
                    overall, f"[{index}/{total}] {name} — {pct:.0f}%")

            def on_done(result):
                self.file_done.emit(
                    result.source.name, result.ok,
                    result.output.name if result.ok else (result.error or ""))

            results = process_folder(
                self.sources, self.output_dir, self.config,
                allow_model_download=self.allow_model_download,
                transcript_only=self.transcript_only,
                clip=self.clip,
                cancel_token=self.cancel_token,
                on_progress=on_progress,
                on_file_done=on_done)
            self.completed.emit(sum(1 for r in results if r.ok), len(results))
        except Exception as exc:
            logger.exception("فشلت المعالجة الدفعية")
            self.failed.emit(format_error_for_user(exc))
        finally:
            self.finished.emit()


class ProviderTestWorker(QObject):
    """اختبار مفتاح المزوّد خارج خيط الواجهة.

    كان الاختبار يُنفَّذ داخل معالج الزر بمهلة 30 ثانية مع
    ``processEvents()`` لإخفاء الأثر — أي تجميد فعلي للواجهة يخالف
    ADR-001 والمواصفة §15، وهو الموضع الوحيد الذي يخرقهما في المشروع.
    """

    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    @pyqtSlot()
    def run(self) -> None:
        try:
            reply = self.provider.complete(
                "أجب بكلمة واحدة فقط.", "قل: جاهز",
                max_tokens=16, timeout=30)
            self.succeeded.emit((reply or "").strip()[:40])
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class UpdateCheckWorker(QObject):
    """فحص وجود إصدار أحدث خارج خيط الواجهة.

    ست ثوانٍ مهلةً داخل معالج الزرّ تجميدٌ للواجهة تمامًا كنداء المزوّد
    الذي أنتج ``ProviderTestWorker`` — والمهلة تُستهلك كاملةً بالضبط في
    الحال الذي نتوقّعه: جهازٌ بلا إنترنت.
    """

    done = pyqtSignal(object)       # utils.updater.UpdateCheck
    finished = pyqtSignal()

    @pyqtSlot()
    def run(self) -> None:
        from utils.updater import check_for_update

        try:
            self.done.emit(check_for_update())
        except Exception as exc:            # حزامٌ فوق حمّالة
            logger.info("فحص التحديث: استثناء غير متوقّع — %s", exc)
        finally:
            self.finished.emit()


class PipelineWorker(QObject):
    progress = pyqtSignal(float, str)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, pipeline: VideoToDocPipeline, video_path: Path,
                 cancel_token: CancellationToken,
                 allow_model_download: bool = False,
                 allow_audio_only: bool = False,
                 clip=None, transcript_only: bool = False) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.video_path = video_path
        self.cancel_token = cancel_token
        self.allow_model_download = allow_model_download
        self.allow_audio_only = allow_audio_only
        self.clip = clip
        self.transcript_only = transcript_only

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.pipeline.run(
                self.video_path,
                cancel_token=self.cancel_token,
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
                allow_model_download=self.allow_model_download,
                allow_audio_only=self.allow_audio_only,
                clip=self.clip,
                transcript_only=self.transcript_only)
            self.completed.emit(str(result))
        except PipelineCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("فشل المعالجة في العامل الخلفي")
            self.failed.emit(format_error_for_user(exc))
        finally:
            self.finished.emit()


class ScreenTermsWorker(QObject):
    """مسح إطارات الفيديو لاقتراح مصطلحات — خارج خيط الواجهة.

    المسح يستغرق نحو دقيقة (‏14 إطارًا × OCR)، وتنفيذه في خيط الواجهة
    تجميدٌ يخالف ADR-001. والسبب نفسه الذي أخرج ``ProviderTestWorker``.
    """

    progressed = pyqtSignal(int, int)
    succeeded = pyqtSignal(list)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, video_path, duration_seconds: float) -> None:
        super().__init__()
        self.video_path = video_path
        self.duration_seconds = duration_seconds

    @pyqtSlot()
    def run(self) -> None:
        try:
            from utils.ffmpeg_service import FFmpegService
            from video.screen_terms import scan_video

            terms = scan_video(
                self.video_path, FFmpegService(), self.duration_seconds,
                progress=lambda done, total: self.progressed.emit(done, total))
            self.succeeded.emit(terms)
        except Exception as exc:                            # noqa: BLE001
            logger.exception("فشل مسح مصطلحات الشاشة")
            self.failed.emit(format_error_for_user(exc))
        finally:
            self.finished.emit()
