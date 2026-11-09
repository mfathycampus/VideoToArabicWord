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


def _date(timestamp) -> str:
    from datetime import datetime, timezone

    if not timestamp:
        return "بلا تاريخ انتهاء"
    return (datetime.fromtimestamp(timestamp, tz=timezone.utc)
            .astimezone().strftime("%Y-%m-%d"))


def _mask(code: str) -> str:
    return f"…{code[-4:]}" if code and len(code) > 4 else (code or "")


class LicenseDialog(QDialog):
    """``replacing=True``: تغيير كود والبرنامج مفعّل (1.22.0).

    كان الحوار يُعرض فقط والترخيص غير فعّال، فمن فعّل تجربة أسبوع ثم
    اشترى ستة أشهر لا يجد أين يدخل الكود الجديد حتى تنتهي التجربة. الكود
    الجديد يحلّ محلّ القديم فورًا، ومدّته من لحظة تفعيله (الخادم يحسبها
    كذلك)، وكل ما يتبع الباقة — «معين المُدار» وعلامة التجربة — يتبعه.
    """

    def __init__(self, verdict: Verdict, app_version: str = "", parent=None,
                 replacing: bool = False) -> None:
        super().__init__(parent)
        self.replacing = replacing
        self.setWindowTitle("تغيير كود التفعيل" if replacing else "تفعيل البرنامج")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(560)
        self.app_version = app_version
        self.verdict = verdict

        layout = QVBoxLayout(self)
        message = verdict.message
        if replacing:
            current = app_gate.current_lease()
            if current is not None:
                message = (f"الكود الحالي {_mask(current.code)} · {current.plan or '—'} · "
                           f"حتى {_date(current.expires_at)}.\n")
            else:
                message = ""
            message += ("أدخل الكود الجديد: يحلّ محلّ الحالي فورًا، وتبدأ مدّته "
                        "من الآن، وينتهي الكود القديم على هذا الجهاز.")
        self.status = QLabel(message)
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
        # يُنسخ الكود مع البصمة معًا حين يكون مكتوبًا: لصقةٌ واحدة يرسلها
        # المعلّم بدل قيمتين يُخطئ أحدهما بالنسخ أو النسيان.
        code = self.code_input.text().strip()
        device = app_gate.device_id()
        text = f"{code}  {device}" if code else device
        QApplication.clipboard().setText(text)
        if code:
            self.status.setText(
                "نُسخ الكود وبصمة الجهاز معًا. أرسلهما إلى المزوّد، "
                "والصق ما يردّه في الصندوق أسفله.")
        else:
            self.status.setText(
                "نُسخت بصمة الجهاز. اكتب كود التفعيل أيضًا وأرسلهما "
                "معًا إلى المزوّد.")

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
            if self.replacing:
                self._replace(app_gate.fetch_lease(code, self.app_version))
            else:
                self._finish(app_gate.activate(code, self.app_version))
        except (ActivationError, LeaseError) as exc:
            self.status.setText(str(exc))
        finally:
            self.activate_button.setEnabled(True)
            self.activate_button.setText("تفعيل")

    def _activate_offline(self) -> None:
        text = self.offline_input.toPlainText().strip()
        if not text:
            # جهازٌ بلا إنترنت لا يُرسِل شيئًا تلقائيًّا عند الضغط — الرسالة
            # توضّح ذلك بدل أن تبدو كأن الزرّ لا يفعل شيئًا.
            code = self.code_input.text().strip()
            missing = "لم يصلك ردّ المزوّد بعد"
            if code:
                missing += f" على كود {code}"
            self.status.setText(
                f"{missing}. هذا الجهاز بلا إنترنت فلا يُرسَل شيء تلقائيًّا: "
                "اضغط «نسخ» بجانب بصمة الجهاز، أرسلها (مع الكود) إلى "
                "المزوّد، والصق ما يردّه هنا ثم اضغط «تفعيل بلا إنترنت».")
            return
        try:
            if self.replacing:
                from licensing.signing import verify
                verify(text)                             # يرفع LeaseError إن فسد
                self._replace(text)
            else:
                self._finish(app_gate.activate_offline(text, self.app_version))
        except LeaseError as exc:
            self.status.setText(f"عقدٌ غير صالح: {exc}")

    def _replace(self, lease_text: str) -> None:
        """يثبّت العقد الجديد مكان الحالي — بعد تأكيدٍ إن كان أقصر."""
        from licensing.signing import verify

        new = verify(lease_text)
        old = app_gate.current_lease()
        if old is not None and new.code == old.code:
            self.status.setText("هذا هو الكود المفعّل حاليًا — لم يتغيّر شيء.")
            return
        if (old is not None and old.expires_at and new.expires_at
                and new.expires_at < old.expires_at):
            answer = QMessageBox.question(
                self, "الكود الجديد أقصر",
                f"الكود الجديد ينتهي في {_date(new.expires_at)}، والحالي في "
                f"{_date(old.expires_at)}.\nاستبدال الحالي به على أي حال؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.status.setText("أُبقي الكود الحالي.")
                return
        self._finish(app_gate.install_lease(lease_text, self.app_version))


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
