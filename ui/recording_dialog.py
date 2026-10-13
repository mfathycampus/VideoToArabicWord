"""نافذة «تسجيل الشاشة»: تثبيت أداة التسجيل بإذن، ثم بدء التسجيل وإيقافه.

ثلاث حالات في نافذة واحدة (لا معالج متعدد الشاشات — معلّم في عجلة قبل
اجتماع لا يحتمل خمس نقرات):

1. الأداة غير مثبّتة ← زر «تثبيت» يسأل أولًا (الحجم والمصدر والتحقق).
2. جاهزة ← إقرار موافقة المشاركين ثم «ابدأ التسجيل».
3. تسجيل جارٍ ← مؤقّت و«إيقاف وحفظ»؛ والإغلاق ممنوع حتى الإيقاف.

كل عمل بطيء (تنزيل، تشغيل OBS، إيقاف) على خيط خلفي بنمط Worker +
moveToThread (ADR-001).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from config.settings import AppConfig
from core.exceptions import PipelineCancelledError
from recording.session import MeetingRecorder, RecorderState
from ui import theme
from utils.cancellation import CancellationToken
from utils.error_reporting import format_error_for_user
from utils.logger import logger

CONSENT_TEXT = (
    "أقرّ بأن كل من يظهر أو يُسمع في هذا التسجيل (إن وُجد: مشاركو اجتماع أو "
    "حصة) على علمٍ به وموافق عليه، وأن لديّ الصلاحية لتسجيله.")


class _TaskWorker(QObject):
    """ينفّذ دالة حاجزة على خيط خلفي ويعيد نتيجتها بإشارة."""

    progress = pyqtSignal(float, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, task: Callable[[Callable[[float, str], None]], object]):
        super().__init__()
        self._task = task

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._task(self.progress.emit)
        except PipelineCancelledError:
            self.failed.emit("أُلغيت العملية.")
        except Exception as exc:  # noqa: BLE001 - تُعرض للمستخدم
            logger.exception("فشل عمل في نافذة التسجيل")
            self.failed.emit(format_error_for_user(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


def _clock_text(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class RecordingDialog(QDialog):
    # ⚠ لا تسمِّها ``event``: هذا اسم دالة QObject.event التي تستدعيها Qt
    # لكل حدث، وإشارةٌ بهذا الاسم تُسقط البرنامج بـ«native Qt signal is not callable».
    log_event = pyqtSignal(str)

    def __init__(self, config: AppConfig, parent=None,
                 recorder: Optional[MeetingRecorder] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.result_path: Optional[Path] = None
        self.cancel_token = CancellationToken()
        self.recorder = recorder or MeetingRecorder(
            config, on_event=self.log_event.emit)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_TaskWorker] = None
        self._busy = False

        self.setWindowTitle("تسجيل الشاشة")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("تسجيل الشاشة (شرح · اجتماع Teams / Zoom / Meet · أي نافذة)")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(title)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.consent_check = QCheckBox(CONSENT_TEXT)
        self.consent_check.toggled.connect(self._refresh)
        layout.addWidget(self.consent_check)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.install_button = QPushButton("تثبيت أداة التسجيل…")
        self.install_button.clicked.connect(self._on_install)
        self.start_button = QPushButton("ابدأ التسجيل")
        self.start_button.clicked.connect(self._on_start)
        self.stop_button = QPushButton("إيقاف وحفظ")
        self.stop_button.clicked.connect(self._on_stop)
        self.close_button = QPushButton("إغلاق")
        self.close_button.clicked.connect(self.reject)
        for button in (self.install_button, self.start_button,
                       self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.log_event.connect(self._on_event)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._refresh()

    # -- الحالة ------------------------------------------------------
    def _recording(self) -> bool:
        return self.recorder.state is RecorderState.RECORDING

    def _refresh(self, *_args) -> None:
        rec = self.config.recording
        ready = self.recorder.tool_ready()
        recording = self._recording()
        idle = not self._busy and not recording

        if not rec.enabled:
            self.info.setText("تسجيل الشاشة معطّل بسياسة الجهاز. "
                              "راجع مسؤول تقنية المعلومات.")
        elif recording:
            self.info.setText(
                "التسجيل جارٍ. اترك هذه النافذة مفتوحة (يمكنك تصغيرها) "
                "وأوقف التسجيل بعد انتهاء الشرح أو الاجتماع.")
        elif ready:
            self.info.setText(
                "يسجّل البرنامج شاشتك الرئيسية وصوت الجهاز وميكروفونك، ثم "
                "يعالج التسجيل كأي فيديو. أغلق الإشعارات الصوتية قبل البدء.\n"
                f"مكان الحفظ: {rec.resolved_output_dir()}\n"
                "قد يطلب ويندوز السماح لأداة التسجيل بالشبكة عند أول مرة؛ "
                "التسجيل يعمل محليًّا في الحالتين.")
        elif self.recorder.can_install():
            release = self.recorder.release
            self.info.setText(
                "لتسجيل الشاشة يحتاج البرنامج أداة التسجيل OBS Studio "
                f"(مجانية ومفتوحة المصدر، نحو {release.size_mb} ميغابايت). "
                "تُنزَّل مرة واحدة من موقعها الرسمي وتُثبَّت داخل مجلد "
                "البرنامج بلا صلاحيات مدير، ولا تتأثر نسخة OBS عندك إن وُجدت.")
        else:
            self.info.setText(
                "أداة التسجيل غير متاحة: التنزيل ممنوع بسياسة الجهاز أو المسار "
                "المحدَّد في الإعداد غير صالح. اطلب من مسؤول تقنية المعلومات "
                "نشر OBS وتحديد مساره.")

        self.install_button.setVisible(
            rec.enabled and not ready and self.recorder.can_install())
        self.install_button.setEnabled(idle)
        self.consent_check.setVisible(rec.enabled and ready and not recording)
        self.consent_check.setEnabled(idle)
        self.start_button.setVisible(rec.enabled and ready and not recording)
        self.start_button.setEnabled(idle and self.consent_check.isChecked())
        self.stop_button.setVisible(recording)
        self.stop_button.setEnabled(recording and not self._busy)
        self.close_button.setEnabled(not recording)

    # -- العمل الخلفي -------------------------------------------------
    def _run(self, task, on_success, busy_message: str) -> None:
        self._busy = True
        self.status.setText(busy_message)
        self._refresh()
        self._thread = QThread(self)
        self._worker = _TaskWorker(task)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_task_finished)
        self._thread.start()

    def _on_task_finished(self) -> None:
        self._busy = False
        self.progress.hide()
        self._refresh()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.show()
        self.progress.setValue(int(max(0.0, min(fraction, 1.0)) * 1000))
        self.status.setText(message)

    def _on_event(self, message: str) -> None:
        self.status.setText(message)

    def _on_failed(self, message: str) -> None:
        self.status.setText("")
        QMessageBox.warning(self, "تسجيل الشاشة", message)

    # -- الإجراءات -----------------------------------------------------
    def _on_install(self) -> None:
        release = self.recorder.release
        answer = QMessageBox.question(
            self, "تنزيل أداة التسجيل",
            f"سيُنزَّل OBS Studio {release.version} (~{release.size_mb} ميغابايت) "
            "من الموقع الرسمي للمشروع (github.com/obsproject/obs-studio).\n\n"
            "• يُفحص الملف بالبصمة SHA-256 قبل فكّه، ولا يُشغَّل إن لم يطابق.\n"
            "• يُثبَّت داخل مجلد البرنامج دون صلاحيات مدير.\n"
            "• هذا اتصالٌ بالإنترنت لهذا الغرض فقط، وبعده يعمل التسجيل بلا اتصال.\n\n"
            "هل تسمح بالتنزيل الآن؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("أُلغي التنزيل.")
            return
        self.cancel_token = CancellationToken()
        self._run(lambda cb: self.recorder.install(
                      progress=cb, cancel=self.cancel_token),
                  lambda _exe: self.status.setText("أداة التسجيل جاهزة."),
                  "بدء التنزيل…")

    def _on_start(self) -> None:
        if not self.consent_check.isChecked():
            return
        self._run(lambda cb: self.recorder.start(consent_confirmed=True),
                  self._on_started, "تشغيل أداة التسجيل… (قد يستغرق دقيقة)")

    def _on_started(self, _result) -> None:
        self._timer.start()
        self._tick()
        self._refresh()

    def _tick(self) -> None:
        if self._recording():
            self.status.setText(
                f"● جارٍ التسجيل   {_clock_text(self.recorder.elapsed())}")

    def _on_stop(self) -> None:
        self._timer.stop()
        self._run(lambda cb: self.recorder.stop(), self._on_stopped,
                  "إيقاف التسجيل وحفظ الملف…")

    def _on_stopped(self, result) -> None:
        self.result_path = result.path
        note = ("\n\nملاحظة: لم يُنهِ OBS التسجيل بنفسه فاسُترجع الملف؛ "
                "قد يكون ناقصًا في آخره." if result.recovered else "")
        QMessageBox.information(
            self, "تم حفظ التسجيل",
            f"المدة: {_clock_text(result.duration_seconds)}\n"
            f"الملف: {result.path}\n\n"
            "سيُحدَّد الملف مصدرًا للمعالجة؛ اضغط «ابدأ» في النافذة الرئيسية."
            + note)
        self.accept()

    # -- الإغلاق -------------------------------------------------------
    def reject(self) -> None:
        if self._recording() or (self._busy and not self._can_cancel_busy()):
            QMessageBox.information(
                self, "تسجيل جارٍ",
                "أوقف التسجيل أولًا (زر «إيقاف وحفظ») ثم أغلق النافذة.")
            return
        if self._busy:                    # تنزيل جارٍ: إلغاء تعاوني
            self.cancel_token.cancel()
            if self._thread is not None:
                self._thread.wait(5000)
        super().reject()

    def _can_cancel_busy(self) -> bool:
        """الإلغاء ممكن أثناء التنزيل فقط، لا أثناء تشغيل OBS أو إيقافه."""
        return self.recorder.state is RecorderState.IDLE

    def closeEvent(self, event) -> None:
        if self._recording():
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
