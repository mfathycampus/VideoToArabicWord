"""شاشة التفعيل — تُعرض عند الإقلاع حين لا يكون الترخيص فعّالًا.

ثلاثة مسارات: كودٌ عبر الإنترنت (الأغلب)، وعقدٌ يُلصَق نصًّا لجهازٍ بلا
إنترنت، وبصمة الجهاز تُنسخ بزرّ لتُرسَل إلى المزوّد. وكلها في نافذة
واحدة: معلّمٌ أمام برنامجٍ لا يفتح لا يُرسَل إلى قائمة خطوات.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from licensing import app_gate
from licensing.client import ActivationError
from licensing.gate import Verdict
from licensing.lease import LeaseError


class LicenseDialog(QDialog):
    def __init__(self, verdict: Verdict, app_version: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تفعيل البرنامج")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(560)
        self.app_version = app_version
        self.verdict = verdict

        layout = QVBoxLayout(self)
        self.status = QLabel(verdict.message)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        form = QFormLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("VTAW-XXXX-XXXX-XXXX-XXXX")
        form.addRow("كود التفعيل:", self.code_input)

        device_row = QHBoxLayout()
        self.device_label = QLineEdit(app_gate.device_id())
        self.device_label.setReadOnly(True)
        copy = QPushButton("نسخ")
        copy.clicked.connect(self._copy_device)
        device_row.addWidget(self.device_label)
        device_row.addWidget(copy)
        form.addRow("بصمة هذا الجهاز:", device_row)
        layout.addLayout(form)

        hint = QLabel(
            "أدخل الكود واضغط «تفعيل». وإن كان الجهاز بلا إنترنت، أرسل "
            "بصمة الجهاز إلى المزوّد والصق ما يرسله لك في الصندوق أدناه.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.offline_input = QPlainTextEdit()
        self.offline_input.setPlaceholderText("VTAW1.… (تفعيل بلا إنترنت)")
        self.offline_input.setMaximumHeight(70)
        layout.addWidget(self.offline_input)

        buttons = QDialogButtonBox()
        self.activate_button = buttons.addButton(
            "تفعيل", QDialogButtonBox.ButtonRole.AcceptRole)
        self.offline_button = buttons.addButton(
            "تفعيل بلا إنترنت", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("إغلاق", QDialogButtonBox.ButtonRole.RejectRole)
        self.activate_button.clicked.connect(self._activate)
        self.offline_button.clicked.connect(self._activate_offline)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _copy_device(self) -> None:
        QApplication.clipboard().setText(app_gate.device_id())
        self.status.setText("نُسخت بصمة الجهاز. أرسلها إلى المزوّد.")

    def _finish(self, verdict: Verdict) -> None:
        self.verdict = verdict
        if verdict.can_process:
            QMessageBox.information(self, "تم التفعيل", verdict.message)
            self.accept()
        else:
            self.status.setText(verdict.message)

    def _activate(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            self.status.setText("اكتب كود التفعيل أولًا.")
            return
        self.activate_button.setEnabled(False)
        self.activate_button.setText("جارٍ التفعيل…")
        QApplication.processEvents()
        try:
            self._finish(app_gate.activate(code, self.app_version))
        except (ActivationError, LeaseError) as exc:
            self.status.setText(str(exc))
        finally:
            self.activate_button.setEnabled(True)
            self.activate_button.setText("تفعيل")

    def _activate_offline(self) -> None:
        text = self.offline_input.toPlainText().strip()
        if not text:
            self.status.setText("الصق العقد الذي أرسله لك المزوّد.")
            return
        try:
            self._finish(app_gate.activate_offline(text, self.app_version))
        except LeaseError as exc:
            self.status.setText(f"عقدٌ غير صالح: {exc}")


def ensure_licensed(app_version: str, parent=None) -> Verdict:
    """يفحص، ويجدّد عند الحاجة، ويعرض الشاشة إن لزم. يعيد الحكم النهائي."""
    verdict = app_gate.check(app_version)
    if verdict.status.value in ("needs_recheck", "blocked", "clock_tampered"):
        verdict = app_gate.refresh_online(app_version)
    if verdict.can_process:
        return verdict
    dialog = LicenseDialog(verdict, app_version, parent)
    dialog.exec()
    return dialog.verdict
