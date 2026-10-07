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


class BalanceWorker(QObject):
    """يقرأ الرصيد المتبقي (باقة «مُدار») خارج خيط الواجهة. لا يستهلك شيئًا."""

    succeeded = pyqtSignal(float)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    @pyqtSlot()
    def run(self) -> None:
        try:
            from licensing import app_gate, client

            code = app_gate.current_code()
            if not code:
                raise client.ActivationError(
                    "لا كود مُدار مفعّل — فعّل الكود من نافذة التفعيل.")
            info = client.managed_balance(code, app_gate.device_id())
            self.succeeded.emit(float(info.get("credit_minutes") or 0))
        except Exception as exc:                       # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


def _run_with_timeout(fn, seconds: float):
    """ينفّذ ``fn`` في خيط جانبي ويعيد ``(ok, value_or_error)`` أو ``(None, "timeout")``.

    ``urlopen(timeout=…)`` لا يغطي حلّ الأسماء (DNS) ولا خادمًا وسيطًا يقطّر
    البيانات؛ فالفحص الذي يعلّق دقائق بلا تقرير هو بالضبط ما يُراد كشفه.
    """
    import threading

    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as exc:                           # noqa: BLE001
            box["value"] = f"{type(exc).__name__}: {exc}"
            box["ok"] = False

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        return None, "timeout"
    return box.get("ok"), box.get("value")


class ManagedCheckWorker(QObject):
    """يفحص الباقة المُدارة خطوةً خطوة ويُظهر كل خطوة فور انتهائها.

    الغرض: جهازٌ يعمل عليه كل شيء وآخر يخرج منه مستندٌ خام بلا تفسير. لكل
    خطوة مهلة صارمة، فلا يعلّق الفحص بلا تقرير، وتُكتب النتائج في السجل
    أيضًا. لا يطبع عنوان الخادم.
    """

    line = pyqtSignal(str)            # يصل فور انتهاء كل خطوة
    report = pyqtSignal(str)
    finished = pyqtSignal()

    def _emit(self, lines: list, text: str) -> None:
        import re
        import time as _time

        text = re.sub(r"https?://\S+", "[الخادم]", text)
        lines.append(text)
        logger.info(f"فحص الباقة المُدارة: {text}")
        self.line.emit(text)
        _time.sleep(0)

    @pyqtSlot()
    def run(self) -> None:
        import socket
        import time as _time
        from urllib.parse import urlparse

        lines: list = []
        try:
            from ai.providers import MaeenManagedProvider
            from licensing import app_gate, client

            def timed(label, fn, seconds):
                start = _time.time()
                ok, value = _run_with_timeout(fn, seconds)
                took = _time.time() - start
                if ok is None:
                    self._emit(lines, f"✗ {label}: لا ردّ خلال {seconds:.0f} ثانية "
                                      "(انقطاع/حجب في الشبكة على هذا الجهاز؟)")
                    return None, False
                if not ok:
                    self._emit(lines, f"✗ {label}: {value}")
                    return None, False
                return (value, took), True

            res, ok = timed("بصمة الجهاز", app_gate.device_id, 20)
            if not ok:
                return
            device = res[0]
            self._emit(lines, f"✓ بصمة الجهاز {device[:8]}… ({res[1]:.1f} ث)")

            code = app_gate.current_code()
            if not code:
                self._emit(lines, "✗ لا كود مفعّل على هذا الجهاز — فعّل الكود من نافذة التفعيل.")
                return
            self._emit(lines, f"✓ الكود {code[:9]}…")

            host = urlparse(client.server_url()).hostname or ""
            res, ok = timed("حلّ اسم الخادم (DNS)",
                            lambda: socket.getaddrinfo(host, 443), 12)
            if not ok:
                return
            self._emit(lines, f"✓ DNS يعمل ({res[1]:.1f} ث)")

            res, ok = timed("الاتصال بالخادم/ارتباط الجهاز",
                            lambda: client.managed_balance(code, device), 25)
            if not ok:
                return
            info = res[0]
            self._emit(lines, f"✓ الخادم يردّ والجهاز مرتبط — الرصيد "
                              f"{float(info.get('credit_minutes') or 0):g} دقيقة "
                              f"({res[1]:.1f} ث)")

            res, ok = timed(
                "نداء Claude",
                lambda: MaeenManagedProvider().complete(
                    "أجب بكلمة واحدة فقط.", "قل: جاهز", max_tokens=16, timeout=40), 60)
            if ok:
                self._emit(lines, f"✓ نداء Claude يعمل — ردّ: {str(res[0]).strip()[:30]} "
                                  f"({res[1]:.1f} ث)")
        except Exception as exc:                           # noqa: BLE001
            self._emit(lines, f"✗ خطأ غير متوقّع: {type(exc).__name__}: {exc}")
        finally:
            self.report.emit("\n".join(lines))
            self.finished.emit()


class UsageWorker(QObject):
    """يقرأ سجل استهلاك الكود المُدار (آخر العمليات) خارج خيط الواجهة."""

    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    @pyqtSlot()
    def run(self) -> None:
        try:
            from licensing import app_gate, client

            code = app_gate.current_code()
            if not code:
                raise client.ActivationError("لا كود مُدار مفعّل.")
            self.succeeded.emit(client.managed_usage(code, app_gate.device_id()))
        except Exception as exc:                       # noqa: BLE001
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
    warned = pyqtSignal(list)            # تنبيهات لا تُفشل المهمة (الصياغة سقطت…)
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
            notes = list(getattr(self.pipeline, "last_notes", []) or [])
            if notes:
                self.warned.emit(notes)
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
