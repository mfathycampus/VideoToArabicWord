"""لوحة إعادة الصياغة والحزمة التعليمية ومفتاح المزوّد — منقولة من ``ui/main_window.py``.

مزيج يرثه ``MainWindow``؛ لا تغيير في السلوك.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
)

from ui.worker import (
    ProviderTestWorker,
)


class RewritePanelMixin:
    def _build_study_row(self):
        """تفعيل الحزمة التعليمية واختيار مزوّدها.

        المزوّد الافتراضي محلّي (‏Ollama) عمدًا: ميزةٌ يُفترض أن
        يجرّبها كل معلّم لا يصحّ أن تكون تجربتها مشروطة بمفتاح مدفوع.
        """
        from ai.providers import PROVIDER_CHOICES

        row = QHBoxLayout()
        self.study_check = QCheckBox("الحزمة التعليمية (أهداف · مسرد · أسئلة · بطاقات)")
        self.study_check.setToolTip(
            "يقرأ التفريغ ويكتب أهداف تعلّم وأسئلة تقييم ومسردًا وبطاقات\n"
            "مراجعة. كل عنصر يحمل مرجعه إلى المقطع الذي بُني منه، وما لا\n"
            "يُثبت مصدره يُسقَط.\n\n"
            "المزوّد المحلي (Ollama) مجاني ولا يغادر النصّ جهازك.\n"
            "وبلا أي مزوّد تخرج قائمة مصطلحات وحدها — مفيدة للمراجعة\n"
            "ولخانة «مصطلحات المادة».")
        self.study_check.setChecked(self.config.study.enabled)
        self.study_check.stateChanged.connect(self._on_study_toggled)
        row.addWidget(self.study_check)

        row.addWidget(QLabel("المزوّد:"))
        self.study_provider = QComboBox()
        for key, label in PROVIDER_CHOICES:
            self.study_provider.addItem(label, key)
        index = self.study_provider.findData(self.config.study.provider)
        self.study_provider.setCurrentIndex(max(0, index))
        self.study_provider.currentIndexChanged.connect(self._on_study_toggled)
        row.addWidget(self.study_provider)

        cloud_locked = self._policy_reason("allow_cloud_ai")
        if cloud_locked:
            # لا يكفي إسقاط الاختيار في الإعداد: مربّعٌ يعرض «Claude API»
            # ويُنتج تشغيلًا محليًّا كذبٌ في الواجهة. تُحذف الخيارات
            # السحابية من القائمة أصلًا ويُعلَن السبب.
            for position in range(self.study_provider.count() - 1, -1, -1):
                if self.study_provider.itemData(position) != "ollama":
                    self.study_provider.removeItem(position)
            note = QLabel("🔒")
            note.setToolTip(cloud_locked)
            row.addWidget(note)

        row.addStretch(1)
        self._refresh_study_row()
        return row

    def _on_study_toggled(self) -> None:
        self.config.study.enabled = self.study_check.isChecked()
        self.config.study.provider = (
            self.study_provider.currentData() or "ollama")
        self._refresh_study_row()
        self._save_config()

    def _refresh_study_row(self) -> None:
        self.study_provider.setEnabled(self.study_check.isChecked())

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

        # الاختبار على خيط خلفي: نداء الشبكة بمهلة 30 ثانية داخل معالج
        # الزر كان يجمّد الواجهة فعليًا (ADR-001 والمواصفة §15).
        self.test_button.setEnabled(False)
        self.test_button.setText("جارٍ الاختبار…")
        self.append_log("جارٍ اختبار الاتصال بالمزوّد…")

        self._test_thread = QThread(self)
        self._test_worker = ProviderTestWorker(provider)
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.succeeded.connect(self._on_key_test_ok)
        self._test_worker.failed.connect(self._on_key_test_failed)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.finished.connect(self._reset_test_button)
        self._test_thread.start()

    def _on_key_test_ok(self, reply: str) -> None:
        self._save_config()
        QMessageBox.information(
            self, "نجح الاتصال",
            f"المزوّد يعمل ورد بـ: {reply}\n\n"
            f"حُفظ المفتاح في:\n{self.config_path}")
        self.append_log("تم التحقق من المفتاح وحفظه.")

    def _on_key_test_failed(self, error: str) -> None:
        QMessageBox.critical(self, "فشل الاتصال", error)
        self.append_log(f"فشل اختبار المفتاح: {error}")

    def _reset_test_button(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("اختبار وحفظ")
        self._update_rewrite_note()

    def _update_rewrite_note(self) -> None:
        """يوضّح أثر الخيار على الخصوصية قبل تفعيله، لا بعده."""
        if not self.rewrite_check.isChecked():
            self.rewrite_note.setText(
                "معطّل — يُكتب نص التفريغ كما هو. لا اتصال بالإنترنت.")
            # الإخفاء لا التعطيل: العنصر المعطّل يشغل ارتفاعه كاملًا،
            # وثلاثة صفوف معطّلة تدفع بقية الواجهة خارج الشاشة.
            self.provider_row.setVisible(False)
            self.key_row.setVisible(False)
            self.workspace_row.setVisible(False)
            return
        needs_key = self._needs_key()
        self.provider_row.setVisible(True)
        self.key_row.setVisible(needs_key)
        self.workspace_row.setVisible(
            needs_key and self.provider_combo.currentData() == "anthropic")
        self.api_key_input.setEnabled(needs_key)
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
