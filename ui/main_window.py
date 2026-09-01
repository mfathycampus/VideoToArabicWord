"""النافذة الرئيسية — واجهة عربية RTL بلا أي معالجة داخل خيط الواجهة."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QTextEdit,
    QLineEdit, QVBoxLayout, QWidget,
)

from audio.model_manager import (
    AVAILABLE_MODELS, ModelManager, estimate_processing_seconds)
from config.settings import AppConfig
from core.pipeline import VideoToDocPipeline
from ui.worker import PipelineWorker
from utils.cancellation import CancellationToken
from utils.gpu_manager import GPUManager
from utils.media_probe import extract_video_facts, probe_raw
from utils.timestamps import humanize_duration

VIDEO_FILTER = (
    "ملفات الفيديو (*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.ts *.m4v *.mpg "
    "*.mpeg *.3gp);;كل الملفات (*)")


class MainWindow(QMainWindow):
    def __init__(self, config: Optional[AppConfig] = None,
                 config_path: Optional[Path] = None) -> None:
        super().__init__()
        self.config = config or AppConfig()
        from config.settings import default_config_path
        self.config_path = config_path or default_config_path()
        self.model_manager = ModelManager(self.config.whisper.download_root)
        self.video_path: Optional[Path] = None
        self.thread: Optional[QThread] = None
        self.worker: Optional[PipelineWorker] = None
        self.cancel_token: Optional[CancellationToken] = None
        self.result_path: Optional[Path] = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("محوّل الفيديو إلى مستند Word — محلي بالكامل")
        self.setMinimumSize(760, 560)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # --- اختيار الملف ---
        file_box = QGroupBox("١ · ملف الفيديو")
        file_layout = QHBoxLayout(file_box)
        self.file_label = QLabel("لم يُختَر ملف بعد")
        self.file_label.setWordWrap(True)
        browse = QPushButton("اختيار ملف…")
        browse.clicked.connect(self.choose_file)
        file_layout.addWidget(self.file_label, 1)
        file_layout.addWidget(browse)
        layout.addWidget(file_box)

        # --- النموذج ---
        model_box = QGroupBox("٢ · نموذج التفريغ")
        model_layout = QHBoxLayout(model_box)
        self.model_combo = QComboBox()
        for spec in AVAILABLE_MODELS:
            status = "✓ مثبّت" if self.model_manager.is_available(spec.name) \
                else f"يحتاج تنزيل ~{spec.approx_size_gb:.1f}GB"
            self.model_combo.addItem(f"{spec.label_ar}  ({status})", spec.name)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_layout.addWidget(QLabel("النموذج:"))
        model_layout.addWidget(self.model_combo, 1)
        layout.addWidget(model_box)

        # --- صياغة النص ---
        rewrite_box = QGroupBox("٣ · صياغة النص (اختياري)")
        rewrite_layout = QVBoxLayout(rewrite_box)
        self.rewrite_check = QCheckBox(
            "إعادة صياغة التفريغ إلى نص وثيقة احترافي بالذكاء الاصطناعي")
        self.rewrite_check.setChecked(self.config.rewrite.enabled)
        self.rewrite_check.stateChanged.connect(self._on_rewrite_toggled)
        rewrite_layout.addWidget(self.rewrite_check)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("المزوّد:"))
        self.provider_combo = QComboBox()
        from ai.providers import PROVIDER_CHOICES
        for value, label in PROVIDER_CHOICES:
            self.provider_combo.addItem(label, value)
        current = self.provider_combo.findData(self.config.rewrite.provider)
        if current >= 0:
            self.provider_combo.setCurrentIndex(current)
        self.provider_combo.currentIndexChanged.connect(self._on_rewrite_toggled)
        provider_row.addWidget(self.provider_combo, 1)
        rewrite_layout.addLayout(provider_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("مفتاح الوصول:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-ant-api03-…  (يُحفظ على جهازك)")
        self.api_key_input.setText(self.config.rewrite.api_key)
        self.api_key_input.textChanged.connect(self._on_key_typed)
        key_row.addWidget(self.api_key_input, 1)
        self.test_button = QPushButton("اختبار وحفظ")
        self.test_button.clicked.connect(self.test_and_save_key)
        key_row.addWidget(self.test_button)
        rewrite_layout.addLayout(key_row)

        workspace_row = QHBoxLayout()
        workspace_row.addWidget(QLabel("معرّف مساحة العمل:"))
        self.workspace_input = QLineEdit()
        self.workspace_input.setPlaceholderText(
            "wrkspc_…  (اتركه فارغًا إلا إن طلبه الخطأ)")
        self.workspace_input.setText(self.config.rewrite.workspace_id)
        self.workspace_input.textChanged.connect(
            lambda t: setattr(self.config.rewrite, "workspace_id", t.strip()))
        workspace_row.addWidget(self.workspace_input, 1)
        rewrite_layout.addLayout(workspace_row)

        self.rewrite_note = QLabel("")
        self.rewrite_note.setWordWrap(True)
        self.rewrite_note.setStyleSheet("color: #555; font-size: 11px;")
        rewrite_layout.addWidget(self.rewrite_note)
        layout.addWidget(rewrite_box)
        self._update_rewrite_note()

        # --- التشغيل ---
        run_box = QGroupBox("٤ · المعالجة")
        run_layout = QVBoxLayout(run_box)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("ابدأ المعالجة")
        self.start_button.clicked.connect(self.start)
        self.start_button.setEnabled(False)
        self.pause_button = QPushButton("إيقاف مؤقت")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        self.cancel_button = QPushButton("إلغاء")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton("فتح المستند")
        self.open_button.clicked.connect(self.open_result)
        self.open_button.setEnabled(False)
        for b in (self.start_button, self.pause_button, self.cancel_button,
                  self.open_button):
            buttons.addWidget(b)
        run_layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("%p%")
        run_layout.addWidget(self.progress)

        self.status_label = QLabel("جاهز")
        run_layout.addWidget(self.status_label)

        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setStyleSheet("color: #555; font-size: 11px;")
        run_layout.addWidget(self.estimate_label)
        layout.addWidget(run_box)

        # --- السجل ---
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self.setCentralWidget(root)

    # ------------------------------------------------------------------
    def _on_rewrite_toggled(self) -> None:
        enabled = self.rewrite_check.isChecked()
        self.config.rewrite.enabled = enabled
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.provider_combo.setEnabled(enabled)
        self._update_rewrite_note()

    def _on_key_typed(self, text: str) -> None:
        self.config.rewrite.api_key = text.strip()

    def _needs_key(self) -> bool:
        return self.provider_combo.currentData() != "ollama"

    def test_and_save_key(self) -> None:
        """يختبر المفتاح باستدعاء حقيقي قصير ثم يحفظه.

        فحص وجود المفتاح وحده لا يكفي: مفتاح خاطئ يبدو «جاهزًا» حتى أول
        استدعاء بعد ساعة من التفريغ. الاختبار هنا يكشفه في ثانيتين.
        """
        from ai.providers import build_provider

        self.config.rewrite.api_key = self.api_key_input.text().strip()
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.config.rewrite.workspace_id = self.workspace_input.text().strip()
        provider = build_provider(
            self.config.rewrite.provider, self.config.rewrite.model,
            self.config.rewrite.base_url, self.config.rewrite.api_key_env,
            self.config.rewrite.api_key, self.config.rewrite.workspace_id)

        if not provider.is_available():
            QMessageBox.warning(self, "مفتاح مفقود",
                                "ألصق المفتاح في الحقل أولًا.")
            return

        self.test_button.setEnabled(False)
        self.test_button.setText("جارٍ الاختبار…")
        QApplication.processEvents()
        try:
            reply = provider.complete(
                "أجب بكلمة واحدة فقط.", "قل: جاهز", max_tokens=16, timeout=30)
            self._save_config()
            QMessageBox.information(
                self, "نجح الاتصال",
                f"المزوّد يعمل ورد بـ: {reply.strip()[:40]}\n\n"
                f"حُفظ المفتاح في:\n{self.config_path}")
            self.append_log("تم التحقق من المفتاح وحفظه.")
        except Exception as exc:
            QMessageBox.critical(self, "فشل الاتصال", str(exc))
            self.append_log(f"فشل اختبار المفتاح: {exc}")
        finally:
            self.test_button.setEnabled(True)
            self.test_button.setText("اختبار وحفظ")
            self._update_rewrite_note()

    def _save_config(self) -> None:
        try:
            self.config.save(self.config_path)
        except Exception as exc:
            self.append_log(f"تعذّر حفظ الإعداد: {exc}")

    def _update_rewrite_note(self) -> None:
        """يوضّح أثر الخيار على الخصوصية قبل تفعيله، لا بعده."""
        if not self.rewrite_check.isChecked():
            self.rewrite_note.setText(
                "معطّل — يُكتب نص التفريغ كما هو. لا اتصال بالإنترنت.")
            self.provider_combo.setEnabled(False)
            self.api_key_input.setEnabled(False)
            self.workspace_input.setEnabled(False)
            self.test_button.setEnabled(False)
            return
        needs_key = self._needs_key()
        self.api_key_input.setEnabled(needs_key)
        self.workspace_input.setEnabled(
            needs_key and self.provider_combo.currentData() == "anthropic")
        self.test_button.setEnabled(True)
        name = self.provider_combo.currentData()
        try:
            from ai.providers import build_provider
            provider = build_provider(
                name, self.config.rewrite.model, self.config.rewrite.base_url,
                self.config.rewrite.api_key_env, self.config.rewrite.api_key,
                self.config.rewrite.workspace_id)
            available = provider.is_available()
            status = "جاهز ✓" if available else "غير مهيّأ ✗"
            note = provider.info.privacy_note
            hint = ""
            if not available:
                if name == "ollama":
                    hint = "\nثبّت Ollama ثم:  ollama pull qwen2.5:7b-instruct"
                else:
                    hint = ("\nألصق المفتاح في الحقل أعلاه ثم اضغط "
                            "«اختبار وحفظ».")
            model = getattr(provider, "model", "")
            model_line = f"  النموذج: {model}" if model else ""
        except Exception as exc:
            status, note, hint, model_line = f"خطأ: {exc}", "", "", ""
        self.rewrite_note.setText(
            f"{status} · {note}{model_line}{hint}\n"
            "النص الخام يُحفظ دائمًا، ويمكن توليد المستند منه لاحقًا.")

    def _on_model_changed(self) -> None:
        self.config.whisper.model_size = self.model_combo.currentData()
        self._refresh_estimate()

    def append_log(self, message: str) -> None:
        self.log.append(message)

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر ملف فيديو", str(Path.home()), VIDEO_FILTER)
        if path:
            self.video_path = Path(path)
            self.file_label.setText(self.video_path.name)
            self.start_button.setEnabled(True)
            self.append_log(f"تم اختيار: {self.video_path.name}")
            self._refresh_estimate()

    def _refresh_estimate(self) -> None:
        """يعرض المدة المتوقعة قبل الضغط على «ابدأ».

        تفريغ محاضرة ساعة على المعالج يستغرق ساعات. بلا هذا التقدير يظن
        المستخدم أن البرنامج متجمد ويلغي عملًا سليمًا.
        """
        if self.video_path is None:
            self.estimate_label.setText("")
            return
        try:
            facts = extract_video_facts(probe_raw(self.video_path))
        except Exception:
            self.estimate_label.setText("")
            return

        duration = facts["duration_seconds"]
        on_gpu = GPUManager.is_cuda_available()
        seconds = estimate_processing_seconds(
            self.model_combo.currentData(), duration, on_gpu=on_gpu)
        device = "كرت الشاشة" if on_gpu else "المعالج"
        self.estimate_label.setText(
            f"مدة الفيديو {humanize_duration(duration)} — "
            f"المعالجة على {device} تستغرق ~{humanize_duration(seconds)}")

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.video_path is None:
            return
        model_name = self.model_combo.currentData()
        allow_download = False

        if not self.model_manager.is_available(model_name):
            spec = self.model_manager.spec(model_name)
            answer = QMessageBox.question(
                self, "تنزيل النموذج",
                f"النموذج «{spec.label_ar}» غير مثبّت.\n\n"
                f"الحجم: ~{spec.approx_size_gb:.1f} جيجابايت\n"
                f"المسار: {self.model_manager.cache_root}\n\n"
                "هذه هي المرة الوحيدة التي يتصل فيها التطبيق بالإنترنت.\n"
                "بعدها يعمل بالكامل دون اتصال.\n\nهل تسمح بالتنزيل الآن؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer != QMessageBox.StandardButton.Yes:
                self.append_log("أُلغي التنزيل — لا يمكن المتابعة بلا نموذج.")
                return
            allow_download = True

        self.config.whisper.model_size = model_name
        self.config.rewrite.enabled = self.rewrite_check.isChecked()
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.config.rewrite.api_key = self.api_key_input.text().strip()

        if self.config.rewrite.enabled:
            from ai.providers import build_provider
            provider = build_provider(
                self.config.rewrite.provider, self.config.rewrite.model,
                self.config.rewrite.base_url, self.config.rewrite.api_key_env,
                self.config.rewrite.api_key, self.config.rewrite.workspace_id)
            if not provider.is_available():
                QMessageBox.warning(
                    self, "مزوّد غير مهيّأ",
                    f"«{provider.info.label_ar}» غير جاهز.\n\n"
                    "ألصق مفتاح الوصول في حقل «مفتاح الوصول» أعلاه، "
                    "ثم اضغط «اختبار وحفظ».\n\n"
                    "ستتم المعالجة الآن بالنص الخام دون إعادة صياغة.")
            elif not provider.info.is_local:
                answer = QMessageBox.question(
                    self, "تأكيد الإرسال إلى خدمة خارجية",
                    f"سيُرسل نص التفريغ كاملًا إلى "
                    f"«{provider.info.label_ar}» لإعادة صياغته.\n\n"
                    "الفيديو والصوت والصور لا تُرسل إطلاقًا.\n"
                    "النص الخام يبقى محفوظًا على جهازك.\n\nهل توافق؟",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    self.config.rewrite.enabled = False
                    self.rewrite_check.setChecked(False)
                    self.append_log("أُلغيت إعادة الصياغة — المتابعة بالنص الخام.")

        self.cancel_token = CancellationToken()
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)

        self.thread = QThread(self)
        self.worker = PipelineWorker(pipeline, self.video_path,
                                     self.cancel_token, allow_download)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_buttons)

        self._set_running(True)
        self.append_log("بدأت المعالجة…")
        if self.estimate_label.text():
            self.append_log(self.estimate_label.text())
        self.append_log("يمكنك الإيقاف المؤقت أو الإلغاء في أي وقت — "
                        "المراحل المكتملة تُحفظ وتُستأنف لاحقًا.")
        self.thread.start()

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.cancel_button.setEnabled(running)
        if running:
            self.open_button.setEnabled(False)
            self.progress.setValue(0)

    def _reset_buttons(self) -> None:
        self._set_running(False)
        self.start_button.setEnabled(self.video_path is not None)
        self.pause_button.setText("إيقاف مؤقت")

    def toggle_pause(self) -> None:
        if self.cancel_token is None:
            return
        if self.cancel_token.is_paused():
            self.cancel_token.resume()
            self.pause_button.setText("إيقاف مؤقت")
            self.append_log("استُؤنفت المعالجة.")
        else:
            self.cancel_token.pause()
            self.pause_button.setText("استئناف")
            self.append_log("أُوقفت المعالجة مؤقتًا.")

    def cancel(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.status_label.setText("جارٍ الإلغاء…")

    # ------------------------------------------------------------------
    def on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(int(percent))
        self.status_label.setText(message)

    def on_completed(self, path: str) -> None:
        self.result_path = Path(path)
        self.progress.setValue(100)
        self.status_label.setText("اكتملت المعالجة بنجاح.")
        self.open_button.setEnabled(True)
        self.append_log(f"تم إنشاء المستند: {path}")
        QMessageBox.information(self, "اكتملت المعالجة",
                                f"تم إنشاء المستند:\n{path}")

    def on_failed(self, error: str) -> None:
        self.status_label.setText("فشلت المعالجة.")
        self.append_log(f"خطأ: {error}")
        QMessageBox.critical(self, "خطأ", error)

    def on_cancelled(self) -> None:
        self.status_label.setText("أُلغيت المعالجة. يمكن استئنافها لاحقًا.")
        self.append_log("أُلغيت المعالجة — المراحل المكتملة محفوظة.")

    def open_result(self) -> None:
        if self.result_path is None or not self.result_path.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(self.result_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(self.result_path)])
        else:
            subprocess.run(["xdg-open", str(self.result_path)])

    def closeEvent(self, event) -> None:
        """إغلاق نظيف: إلغاء تعاوني وانتظار الخيط، لا terminate()."""
        if self.thread is not None and self.thread.isRunning():
            if self.cancel_token is not None:
                self.cancel_token.cancel()
            self.thread.quit()
            self.thread.wait(10000)
        event.accept()
