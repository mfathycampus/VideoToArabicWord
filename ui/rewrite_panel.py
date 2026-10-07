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
    BalanceWorker,
    ProviderTestWorker,
    UsageWorker,
)

#: دقائق أقل من هذا تُنبّه المستخدم أن الرصيد يوشك على النفاد.
LOW_CREDIT_MINUTES = 30


def format_usage_rows(info: dict) -> str:
    """نصّ سجل الاستهلاك للعرض: الرصيد ثم آخر العمليات (الأحدث أولًا)."""
    from datetime import datetime

    lines = [f"الرصيد المتبقي: {float(info.get('credit_minutes') or 0):g} دقيقة"]
    calls = list(info.get("calls") or [])
    total = sum(float(c.get("minutes") or 0) for c in calls)
    lines.append(f"عدد العمليات المسجّلة: {len(calls)} · "
                 f"مجموعها: {total:.2f} دقيقة\n")
    for call in reversed(calls[-60:]):
        stamp = datetime.fromtimestamp(int(call.get("at") or 0)).strftime(
            "%Y-%m-%d %H:%M")
        lines.append(f"{stamp}   {float(call.get('minutes') or 0):.2f} دقيقة")
    return "\n".join(lines)


class RewritePanelMixin:
    # ------------------------------------------------------------------
    # موافقة الخصوصية للباقة المُدارة: مرّة واحدة قبل أول إرسال.
    def _managed_consent_given(self) -> bool:
        from PyQt6.QtCore import QSettings

        return QSettings("VideoToArabicWord", "app").value(
            "managed_consent_v1", False, type=bool)

    def ensure_managed_consent(self) -> bool:
        """يعرض ما يُرسَل وما لا يُرسَل ويطلب الموافقة. يعيد False عند الرفض."""
        if (not self.rewrite_check.isChecked()
                or self.provider_combo.currentData() != "maeen_managed"
                or self._managed_consent_given()):
            return True
        from PyQt6.QtCore import QSettings

        box = QMessageBox(self)
        box.setWindowTitle("الباقة المُدارة — الخصوصية")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            "لإعادة الصياغة تُرسَل إلى خادم «معين» ثم إلى Claude:\n"
            "• نصّ التفريغ، ولقطات الشاشة إن فُعّل الوضع المرئي.\n\n"
            "لا يُرسَل الفيديو ولا الصوت، ولا يحفظ الخادم المحتوى؛ يسجّل "
            "فقط وقت العملية وحجمها لخصم الرصيد.\n\n"
            "إن كانت المادة سرّية فاختر النموذج المحلي (Ollama) فلا يغادر "
            "النصّ جهازك.\n\n"
            "سياسة الخصوصية والشروط في صفحتي /privacy و/terms على الخادم "
            "(تصلك من مزوّد الخدمة).")
        agree = box.addButton("أوافق وأتابع", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("إلغاء", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not agree:
            return False
        QSettings("VideoToArabicWord", "app").setValue("managed_consent_v1", True)
        return True

    # ------------------------------------------------------------------
    # استهلاك المهمة الواحدة: الفرق بين الرصيد قبلها وبعدها.
    def mark_job_credit_start(self) -> None:
        self._job_credit_start = getattr(self, "_credit_minutes", None)

    def report_job_usage(self) -> None:
        if getattr(self, "_job_credit_start", None) is None:
            return
        self._job_report_pending = True
        self._credit_busy = False
        self._fetch_credit()

    def _emit_job_usage(self, minutes: float) -> None:
        start = getattr(self, "_job_credit_start", None)
        if start is None or not getattr(self, "_job_report_pending", False):
            return
        self._job_report_pending = False
        self._job_credit_start = None
        used = max(0.0, start - minutes)
        self.append_log(f"استهلكت هذه المهمة نحو {used:.1f} دقيقة من رصيدك "
                        f"(المتبقي {minutes:g}).")

    def show_usage_dialog(self) -> None:
        thread = QThread(self)
        worker = UsageWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._display_usage)
        worker.failed.connect(
            lambda msg: QMessageBox.warning(self, "سجل الاستهلاك", msg))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._usage_thread = thread
        thread.start()

    def _display_usage(self, info: dict) -> None:
        QMessageBox.information(self, "سجل الاستهلاك", format_usage_rows(info))

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
        if hasattr(self, "vision_check"):
            self.vision_check.setVisible(enabled)
        self._update_rewrite_note()

    def _on_key_typed(self, text: str) -> None:
        self.config.rewrite.api_key = text.strip()

    def _needs_key(self) -> bool:
        return self.provider_combo.currentData() not in ("ollama", "maeen_managed")

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
        available = False
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
                elif name == "maeen_managed":
                    hint = ("\nفعّل كودًا من باقة «مُدار» من نافذة التفعيل، "
                            "ثم أعد فتح هذه اللوحة.")
                else:
                    hint = ("\nألصق المفتاح في الحقل أعلاه ثم اضغط "
                            "«اختبار وحفظ».")
            model = getattr(provider, "model", "")
            model_line = f"  النموذج: {model}" if model else ""
        except Exception as exc:
            status, note, hint, model_line = f"خطأ: {exc}", "", "", ""
        self._sync_credit(name, available)
        self.rewrite_note.setText(
            f"{status} · {note}{model_line}{hint}{self._credit_line()}\n"
            "النص الخام يُحفظ دائمًا، ويمكن توليد المستند منه لاحقًا.")

    # ------------------------------------------------------------------
    def _auto_pick_managed(self) -> None:
        """يختار «معين المُدار» وحده إن كان الكود المفعّل من باقة مُدار.

        الشرط: لا مفتاح محفوظ، والمزوّد الحالي ليس المُدار ولا المحلي، وثمّة
        كود مفعّل. التحقق بقراءة الرصيد من الخادم (لا تستهلك شيئًا)؛ نجاحها
        يعني أن الكود مُدار فعلًا، وفشلها يترك اختيار المستخدم كما هو.
        """
        try:
            from licensing import app_gate

            if (self.provider_combo.currentData() in ("maeen_managed", "ollama")
                    or self.config.rewrite.api_key.strip()
                    or not app_gate.current_code()):
                return
        except Exception:                              # noqa: BLE001
            return
        thread = QThread(self)
        worker = BalanceWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._select_managed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._probe_thread = thread
        thread.start()

    def _select_managed(self, _minutes: float) -> None:
        index = self.provider_combo.findData("maeen_managed")
        if index >= 0 and self.provider_combo.currentIndex() != index:
            self.provider_combo.setCurrentIndex(index)   # يحفظ ويحدّث الواجهة
            self.append_log("كودك من باقة «مُدار»: اختير «معين المُدار» تلقائيًا.")

    # ------------------------------------------------------------------
    # الرصيد المتبقي (باقة «مُدار») — يُقرأ دون استهلاك، خارج خيط الواجهة.
    def _credit_line(self) -> str:
        text = getattr(self, "_credit_text", "")
        return f"\n{text}" if text else ""

    def _sync_credit(self, provider_name: str, available: bool) -> None:
        """يبدأ تتبّع الرصيد حين يُختار المزوّد المُدار، ويوقفه عند تركه."""
        if provider_name != "maeen_managed" or not available:
            self._credit_text = ""
            self._credit_for = None
            self._credit_minutes = None
            self._refresh_header_credit()
            return
        if getattr(self, "_credit_timer", None) is None:
            from PyQt6.QtCore import QTimer

            self._credit_timer = QTimer(self)
            self._credit_timer.setInterval(4000)
            self._credit_timer.timeout.connect(self._credit_tick)
            self._credit_timer.start()
            self._credit_ticks = 0
            self._credit_seen = 0.0
        if getattr(self, "_credit_for", None) != provider_name:
            self._credit_for = provider_name
            self._fetch_credit()

    def _credit_tick(self) -> None:
        """كل 4 ثوانٍ: آخر رصيد التقطه المزوّد بعد نداء، وكل دقيقة قراءة من الخادم."""
        from ai.providers import managed_credit_state

        if (not self.rewrite_check.isChecked()
                or self.provider_combo.currentData() != "maeen_managed"):
            return
        state = managed_credit_state()
        if state and state.get("at") != self._credit_seen:
            self._credit_seen = state["at"]
            self._set_credit(state["minutes"])
        self._credit_ticks += 1
        if self._credit_ticks % 15 == 0:
            self._fetch_credit()

    def _fetch_credit(self) -> None:
        if getattr(self, "_credit_busy", False):
            return
        self._credit_busy = True
        thread = QThread(self)
        worker = BalanceWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._set_credit)
        worker.failed.connect(self._credit_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._credit_done)
        self._credit_thread = thread
        thread.start()

    def _credit_done(self) -> None:
        self._credit_busy = False

    def _refresh_header_credit(self) -> None:
        """يُعيد رسم شريط الترخيص أعلى النافذة ليشمل الرصيد المتبقي."""
        refresh = getattr(self, "_refresh_license_badge", None)
        if refresh:
            refresh()

    def _set_credit(self, minutes: float) -> None:
        self._credit_minutes = minutes
        self._emit_job_usage(minutes)
        self._refresh_header_credit()
        if minutes <= 0:
            text = "الرصيد المتبقي: نفد ✗ — اطلب شحن الرصيد من المزوّد."
        elif minutes < LOW_CREDIT_MINUTES:
            text = f"الرصيد المتبقي: {minutes:g} دقيقة ⚠ يوشك على النفاد."
        else:
            text = f"الرصيد المتبقي: {minutes:g} دقيقة"
        self._apply_credit_text(text)

    def _credit_failed(self, error: str) -> None:
        self._apply_credit_text(f"تعذّرت قراءة الرصيد: {error[:90]}")

    def _apply_credit_text(self, text: str) -> None:
        if getattr(self, "_credit_text", "") == text:
            return
        self._credit_text = text
        self._update_rewrite_note()
