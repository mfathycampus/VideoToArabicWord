"""نافذة «سجّلني — دليل خطوة بخطوة».

ثلاث صفحات في نافذة واحدة، بالمدخلات والمخرجات نفسها في WebDoc:

1. **الإعداد** — اسم الدليل (جديد أو موجود)، واسم الإجراء («إضافة مستخدم
   جديد»: يصير عنوانه في الدليل)، ولغة الدليل، وتنبيه الخصوصية.
2. **التسجيل** — البرنامج يتنحّى جانبًا، وشريطٌ أعلى الشاشة فيه إيقاف مؤقت
   وتراجع عن آخر خطوة وإنهاء وحفظ (أو Ctrl+Shift+F9 / Ctrl+Shift+F10).
3. **المراجعة** — إجراءات الدليل، وخطوات كلٍّ منها: تعديل النصّ، حذف الصورة،
   حذف الخطوة؛ وعنوان الإنفوجرافيك وأسماء الأنظمة. ثم «حفظ وإعادة الإخراج».

كل عمل بطيء على خيط خلفي (نمط Worker + moveToThread — ADR-001).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config.settings import AppConfig
from screenguide import screen_guide_supported
from screenguide.guide import Guide, GuideOutputs, list_guides
from ui import theme
from ui.recording_dialog import _TaskWorker
from utils.cancellation import CancellationToken

HINT = ("تنفّذ العمل بنفسك — على موقع في متصفّحك أو في أي برنامج على جهازك "
        "(Excel وOutlook ونظام نور وPowerSchool وإعدادات Windows…). كل نقرة تصبح "
        "خطوة مرقّمة بصورتها، والزرّ مسمّى باسمه في البرنامج نفسه. ويتنحّى معين "
        "جانبًا أثناء عملك.")
PRIVACY = ("كل ما يظهر على الشاشة يدخل في الصور، مثل الإشعارات أو بريد مفتوح. "
           "أوقف التسجيل مؤقتًا قبل فتح أي شيء خاص (من الشريط أعلى الشاشة أو "
           "Ctrl+Shift+F9). ما تكتبه لا يُسجَّل أبدًا: الخطوة تذكر فقط أن الحقل تم "
           "ملؤه، وحقل كلمة المرور يُغطّى في الصورة.")
LIVE = ("نفّذ العمل في أي برنامج، وكل نقرة تصبح خطوة مرقّمة. الشريط أعلى الشاشة "
        "فيه إيقاف مؤقت وتراجع عن آخر خطوة وإنهاء وحفظ، أو استخدم Ctrl+Shift+F9 "
        "للإيقاف المؤقت وCtrl+Shift+F10 للإنهاء. البرنامج الذي يعمل «كمسؤول» "
        "تُسجَّل خطواته باسم نافذته وصورتها فقط.")

PAGE_SETUP, PAGE_LIVE, PAGE_REVIEW = 0, 1, 2


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.MUTED};")
    return label


class ScreenGuideDialog(QDialog):
    log_event = pyqtSignal(str)

    def __init__(self, config: AppConfig, parent=None, *, save_config=None) -> None:
        super().__init__(parent)
        self.config = config
        self._save_config = save_config or (lambda: None)
        self.output_dir = Path(config.application.output_dir)
        self.guide: Optional[Guide] = None
        self.outputs: Optional[GuideOutputs] = None
        self._source = None
        self._cancel = CancellationToken()
        self._thread: Optional[QThread] = None
        self._worker: Optional[_TaskWorker] = None
        self._busy = False
        self._recording = False
        self._loading_review = False

        self.setWindowTitle("سجّلني — دليل خطوة بخطوة")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(720, 560)

        layout = QVBoxLayout(self)
        self._built = False
        self.status = _muted("")
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_setup())
        self.pages.addWidget(self._build_live())
        self.pages.addWidget(self._build_review())
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.close_button = QPushButton("إغلاق")
        self.close_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._built = True
        self._refresh_setup()

    # ================================================================ الإعداد
    def _build_setup(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        title = QLabel("سجّلني — دليل خطوة بخطوة")
        title.setStyleSheet("font-weight: 700; font-size: 16px;")
        box.addWidget(title)
        box.addWidget(_muted(HINT))

        row = QHBoxLayout()
        row.addWidget(QLabel("اسم الدليل:"))
        self.guide_combo = QComboBox()
        self.guide_combo.setEditable(True)
        self.guide_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.guide_combo.lineEdit().setPlaceholderText("دليل نظام نور")
        self.guide_combo.currentTextChanged.connect(self._refresh_setup)
        row.addWidget(self.guide_combo, 1)
        box.addLayout(row)
        box.addWidget(_muted("الدليل يجمع إجراءات كثيرة: تسجيل إجراءٍ آخر يضيفه إلى "
                             "الدليل نفسه، وتسجيله ثانيةً بالاسم نفسه يستبدل الأقدم."))

        row = QHBoxLayout()
        row.addWidget(QLabel("اسم الإجراء:"))
        self.journey_edit = QLineEdit()
        self.journey_edit.setPlaceholderText("إضافة مستخدم جديد")
        self.journey_edit.textChanged.connect(self._refresh_setup)
        row.addWidget(self.journey_edit, 1)
        box.addLayout(row)
        box.addWidget(_muted("ماذا يحقّق هذا الإجراء — يصبح عنوانه في الدليل."))

        row = QHBoxLayout()
        row.addWidget(QLabel("لغة الدليل:"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("العربية", "ar")
        self.language_combo.addItem("English", "en")
        index = self.language_combo.findData(self.config.screen_guide.language or "ar")
        self.language_combo.setCurrentIndex(max(0, index))
        row.addWidget(self.language_combo)
        row.addStretch(1)
        box.addLayout(row)

        self.infographic_check = QCheckBox("إنفوجرافيك لكل إجراء (صورة واحدة جاهزة للواتساب والطباعة)")
        self.infographic_check.setChecked(self.config.screen_guide.infographic)
        box.addWidget(self.infographic_check)

        privacy = QLabel(PRIVACY)
        privacy.setWordWrap(True)
        privacy.setStyleSheet(
            f"background: {theme.WARNING_BG}; border: 1px solid {theme.WARNING_BORDER};"
            f"color: {theme.WARNING}; border-radius: 6px; padding: 8px;")
        box.addWidget(privacy)
        box.addStretch(1)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("ابدأ التسجيل")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._on_start)
        self.open_button = QPushButton("مراجعة الدليل وتعديله…")
        self.open_button.clicked.connect(self._on_open_existing)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        box.addLayout(buttons)

        guides = list_guides(self.output_dir)
        self.guide_combo.addItems(guides)
        last = self.config.screen_guide.last_guide
        self.guide_combo.setCurrentText(last if last else (guides[0] if guides else ""))
        return page

    def _guide_title(self) -> str:
        return " ".join(self.guide_combo.currentText().split())

    def _refresh_setup(self, *_args) -> None:
        if not self._built:
            return
        enabled = self.config.screen_guide.enabled
        supported = screen_guide_supported()
        ready = enabled and supported and not self._busy
        self.start_button.setEnabled(
            ready and bool(self._guide_title()) and bool(self.journey_edit.text().strip()))
        existing = self._guide_title() in list_guides(self.output_dir)
        self.open_button.setEnabled(existing and not self._busy)
        if not enabled:
            self.status.setText("«سجّلني» معطّل بسياسة الجهاز. راجع مسؤول تقنية المعلومات.")
        elif not supported:
            self.status.setText("تسجيل الشاشة كاملة خطوةً خطوة يحتاج معين على Windows.")

    # ================================================================ التسجيل
    def _build_live(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        self.live_title = QLabel()
        self.live_title.setWordWrap(True)
        self.live_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        box.addWidget(self.live_title)
        box.addWidget(_muted(LIVE))
        self.live_count = QLabel("الخطوات المسجّلة: 0")
        self.live_count.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {theme.ACCENT};")
        box.addWidget(self.live_count)
        self.live_last = _muted("")
        box.addWidget(self.live_last)
        box.addStretch(1)
        row = QHBoxLayout()
        self.finish_button = QPushButton("إنهاء وحفظ")
        self.finish_button.clicked.connect(self._on_finish)
        row.addWidget(self.finish_button)
        row.addStretch(1)
        box.addLayout(row)
        return page

    def _on_start(self) -> None:
        title = self._guide_title()
        journey = " ".join(self.journey_edit.text().split())
        if not (title and journey):
            return
        language = self.language_combo.currentData() or "ar"
        guide = Guide.open(self.output_dir, title, language)
        if not guide.order:
            guide.language = language
        self.guide = guide
        cfg = self.config.screen_guide
        cfg.last_guide, cfg.language = title, language
        cfg.infographic = self.infographic_check.isChecked()
        self._save_config()

        self.live_title.setText(
            f"الشاشة بين يديك: نفّذ «{journey}» خطوة بخطوة ثم اضغط «إنهاء وحفظ» في الشريط")
        self.live_count.setText("الخطوات المسجّلة: 0")
        self.live_last.setText("")
        self.pages.setCurrentIndex(PAGE_LIVE)
        self._recording = True
        self._cancel = CancellationToken()
        self._run(lambda cb: self._record(cb, guide, journey), self._on_recorded,
                  "جارٍ التجهيز لمتابعة الشاشة…")

    def _record(self, progress, guide: Guide, journey: str):
        """على الخيط الخلفي: التسجيل، ثم إضافة الإجراء، ثم كتابة الدليل."""
        from screenguide import open_screen_recorder
        from screenguide.guide import write_guide
        from screenguide.recorder import ScreenGuideRecorder

        cfg = self.config.screen_guide
        temp = guide.folder / "journeys" / f"_new-{uuid.uuid4().hex[:8]}"
        source = open_screen_recorder(language=guide.language, title=journey)
        self._source = source
        try:
            progress(0.0, f"الشاشة بين يديك: نفّذ «{journey}» خطوة بخطوة")
            recording = ScreenGuideRecorder(
                max_steps=cfg.max_steps,
                double_click_ms=int(getattr(source, "double_click_ms", 500) or 500),
            ).record(
                source, title=journey, folder=temp, language=guide.language,
                on_step=lambda p: progress(
                    0.0, f"step\t{p.steps}\t{p.label}\t{p.action}"),
                should_stop=self._cancel.is_cancelled)
        except BaseException:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        finally:
            self._source = None
            source.close()
        if not recording.steps:
            shutil.rmtree(temp, ignore_errors=True)
            return {"recording": recording, "outputs": None, "replaced": None}
        final = guide.new_journey_folder(recording.id)
        temp.rename(final)
        replaced = guide.add(recording)
        progress(1.0, "كتابة الدليل…")
        outputs = write_guide(guide, self.config, formats=cfg.export_formats,
                              infographics=cfg.infographic,
                              log=lambda m: progress(1.0, m))
        return {"recording": recording, "outputs": outputs, "replaced": replaced}

    def _on_finish(self) -> None:
        source = self._source
        if source is not None:
            source.request_finish()
            self.finish_button.setEnabled(False)

    def _on_recorded(self, result) -> None:
        self._recording = False
        recording = result["recording"]
        if not recording.steps:
            self.pages.setCurrentIndex(PAGE_SETUP)
            self.status.setText("لم تُسجَّل أي خطوة.")
            self._refresh_setup()
            return
        self.outputs = result["outputs"]
        note = (f" · استُبدل التسجيل الأقدم «{result['replaced']}»"
                if result.get("replaced") else "")
        self._show_review(
            f"سُجّلت {len(recording.steps)} خطوة · الدليل يضم "
            f"{self.outputs.procedures if self.outputs else 0} إجراء{note}")
        self.log_event.emit(f"سجّلني: «{recording.title}» — {len(recording.steps)} خطوة")

    def _on_open_existing(self) -> None:
        self.open_guide(self._guide_title())

    def open_guide(self, title: str) -> None:
        """يفتح دليلًا موجودًا على صفحة المراجعة (من هنا أو من المهام السابقة)."""
        self.guide_combo.setCurrentText(title)
        self.guide = Guide.open(self.output_dir, title,
                                self.language_combo.currentData() or "ar")
        self._show_review("")

    # ================================================================ المراجعة
    def _build_review(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        self.review_title = QLabel()
        self.review_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        box.addWidget(self.review_title)
        self.review_note = _muted("")
        box.addWidget(self.review_note)

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("الإجراءات المسجّلة:"))
        self.journey_list = QListWidget()
        self.journey_list.currentRowChanged.connect(self._load_steps)
        left.addWidget(self.journey_list, 1)
        self.delete_journey_button = QPushButton("حذف هذا الإجراء…")
        self.delete_journey_button.clicked.connect(self._on_delete_journey)
        left.addWidget(self.delete_journey_button)
        top.addLayout(left, 1)

        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("العنوان على الإنفوجرافيك:"))
        self.poster_title = QLineEdit()
        row.addWidget(self.poster_title, 1)
        right.addLayout(row)
        right.addWidget(QLabel("نصوص الخطوات:"))
        self.steps_table = QTableWidget(0, 3)
        self.steps_table.setHorizontalHeaderLabels(["نصّ الخطوة", "بلا صورة", "حذف"])
        header = self.steps_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.steps_table.setWordWrap(True)
        self.steps_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        right.addWidget(self.steps_table, 1)
        right.addWidget(_muted("كلماتك تحلّ محلّ النصّ المولَّد في كل الصيغ. امسح نصّ "
                               "الخطوة للعودة إلى النصّ المولَّد."))
        top.addLayout(right, 2)
        box.addLayout(top, 1)

        box.addWidget(QLabel("أسماء الأنظمة (كما تظهر في عناوين الأقسام «أولاً: في …»):"))
        self.systems_table = QTableWidget(0, 2)
        self.systems_table.setHorizontalHeaderLabels(["البرنامج كما سُجّل", "الاسم المطبوع"])
        self.systems_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.systems_table.setMaximumHeight(130)
        box.addWidget(self.systems_table)

        buttons = QHBoxLayout()
        self.rebuild_button = QPushButton("حفظ وإعادة الإخراج")
        self.rebuild_button.clicked.connect(self._on_rebuild)
        self.open_doc_button = QPushButton("فتح المستند")
        self.open_doc_button.clicked.connect(self._open_document)
        self.open_folder_button = QPushButton("فتح المجلد")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.another_button = QPushButton("تسجيل إجراء آخر…")
        self.another_button.clicked.connect(self._on_another)
        for button in (self.rebuild_button, self.open_doc_button,
                       self.open_folder_button, self.another_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        return page

    def _show_review(self, note: str) -> None:
        guide = self.guide
        if guide is None:
            return
        self.review_title.setText(f"الدليل: {guide.title}")
        self.review_note.setText(note or self._outputs_text())
        self._current_journey: Optional[str] = None
        self.journey_list.blockSignals(True)
        self.journey_list.clear()
        for journey in guide.journeys():
            item = QListWidgetItem(
                f"{journey.recording.title}  ({len(journey.recording.steps)})")
            item.setData(Qt.ItemDataRole.UserRole, journey.id)
            self.journey_list.addItem(item)
        self.journey_list.blockSignals(False)
        self.systems_table.setRowCount(0)
        apps: List[str] = []
        for journey in guide.journeys():
            for step in journey.recording.steps:
                if step.app and step.app not in apps:
                    apps.append(step.app)
        for app in apps:
            row = self.systems_table.rowCount()
            self.systems_table.insertRow(row)
            original = QTableWidgetItem(app)
            original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.systems_table.setItem(row, 0, original)
            self.systems_table.setItem(row, 1, QTableWidgetItem(guide.systems.get(app, "")))
        self.pages.setCurrentIndex(PAGE_REVIEW)
        if self.journey_list.count():
            self.journey_list.setCurrentRow(self.journey_list.count() - 1)
        else:
            self._load_steps(-1)
        self._refresh_review()

    def _outputs_text(self) -> str:
        guide = self.guide
        if guide is None:
            return ""
        docs = sorted(p.name for p in guide.folder.glob("*.docx"))
        return f"المجلد: {guide.folder}" + (f" · {docs[0]}" if docs else "")

    def _journey_id(self, row: int) -> Optional[str]:
        item = self.journey_list.item(row) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _load_steps(self, row: int) -> None:
        self._store_steps()
        guide = self.guide
        jid = self._journey_id(row)
        self._current_journey = jid
        self._loading_review = True
        self.steps_table.setRowCount(0)
        self.poster_title.setText("")
        if guide is None or jid is None:
            self._loading_review = False
            return
        journey = next((j for j in guide.journeys() if j.id == jid), None)
        if journey is None:
            self._loading_review = False
            return
        self.poster_title.setPlaceholderText(journey.recording.title)
        self.poster_title.setText(guide.poster_titles.get(jid, ""))
        from screenguide import prose

        generated = prose.instructions(journey.recording)
        edits = guide.edits.get(jid, {})
        for step, text in zip(journey.recording.steps, generated):
            edit = edits.get(str(step.index))
            row_i = self.steps_table.rowCount()
            self.steps_table.insertRow(row_i)
            item = QTableWidgetItem((edit.text if edit and edit.text else "") or text)
            item.setData(Qt.ItemDataRole.UserRole, (step.index, text))
            item.setToolTip(f"النصّ المولَّد: {text}")
            self.steps_table.setItem(row_i, 0, item)
            self.steps_table.setVerticalHeaderItem(row_i, QTableWidgetItem(str(step.index)))
            for col, flag in ((1, bool(edit and edit.no_picture)),
                              (2, bool(edit and edit.deleted))):
                cell = QTableWidgetItem()
                cell.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                cell.setCheckState(Qt.CheckState.Checked if flag else Qt.CheckState.Unchecked)
                if col == 1 and not step.shot:
                    cell.setFlags(Qt.ItemFlag.NoItemFlags)
                self.steps_table.setItem(row_i, col, cell)
        self.steps_table.resizeRowsToContents()
        self._loading_review = False

    def _store_steps(self) -> None:
        """ما في الجدول ← تعديلات الدليل (في الذاكرة؛ تُحفظ مع «حفظ»)."""
        guide = self.guide
        jid = getattr(self, "_current_journey", None)
        if guide is None or jid is None or self._loading_review:
            return
        title = self.poster_title.text().strip()
        if title:
            guide.poster_titles[jid] = title
        else:
            guide.poster_titles.pop(jid, None)
        for row in range(self.steps_table.rowCount()):
            item = self.steps_table.item(row, 0)
            if item is None:
                continue
            index, generated = item.data(Qt.ItemDataRole.UserRole)
            edit = guide.edit(jid, index)
            text = " ".join(item.text().split())
            edit.text = "" if text == generated else text
            edit.no_picture = self.steps_table.item(row, 1).checkState() == Qt.CheckState.Checked
            edit.deleted = self.steps_table.item(row, 2).checkState() == Qt.CheckState.Checked

    def _store_systems(self) -> None:
        guide = self.guide
        if guide is None:
            return
        for row in range(self.systems_table.rowCount()):
            app = self.systems_table.item(row, 0).text()
            name = (self.systems_table.item(row, 1).text()
                    if self.systems_table.item(row, 1) else "").strip()
            if name and name != app:
                guide.systems[app] = name
            else:
                guide.systems.pop(app, None)

    def _refresh_review(self) -> None:
        idle = not self._busy
        has = self.journey_list.count() > 0
        for button in (self.rebuild_button, self.delete_journey_button):
            button.setEnabled(idle and has)
        self.another_button.setEnabled(idle and screen_guide_supported()
                                       and self.config.screen_guide.enabled)
        docs = list(self.guide.folder.glob("*.docx")) if self.guide else []
        self.open_doc_button.setEnabled(bool(docs))
        self.open_folder_button.setEnabled(bool(self.guide and self.guide.folder.exists()))

    def _on_rebuild(self) -> None:
        guide = self.guide
        if guide is None:
            return
        self._store_steps()
        self._store_systems()
        guide.save()
        cfg = self.config.screen_guide

        def task(progress):
            from screenguide.guide import write_guide

            return write_guide(guide, self.config, formats=cfg.export_formats,
                               infographics=cfg.infographic,
                               log=lambda m: progress(1.0, m))

        self._run(task, self._on_rebuilt, "إعادة إخراج الدليل…")

    def _on_rebuilt(self, outputs: GuideOutputs) -> None:
        self.outputs = outputs
        names = "، ".join(p.name for p in outputs.all if p.suffix != ".html"
                         or p.parent == self.guide.folder)
        self.review_note.setText(f"حُفظت التعديلات · {outputs.steps} خطوة في "
                                 f"{outputs.procedures} إجراء · {names}")
        row = self.journey_list.currentRow()
        self._show_review(self.review_note.text())
        if 0 <= row < self.journey_list.count():
            self.journey_list.setCurrentRow(row)

    def _on_delete_journey(self) -> None:
        guide = self.guide
        jid = self._journey_id(self.journey_list.currentRow())
        if guide is None or jid is None:
            return
        answer = QMessageBox.question(
            self, "حذف إجراء",
            "حذف هذا الإجراء من الدليل؟ تُحذف صوره المسجّلة، ويبقى المستند بنسخته "
            "الحالية حتى «حفظ وإعادة الإخراج».")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._current_journey = None
        guide.remove(jid)
        self._show_review("حُذف الإجراء — اضغط «حفظ وإعادة الإخراج» لتحديث المستند.")

    def _on_another(self) -> None:
        if self.guide is not None:
            self._store_steps()
            self._store_systems()
            self.guide.save()
            self.guide_combo.setCurrentText(self.guide.title)
        self.journey_edit.clear()
        self.pages.setCurrentIndex(PAGE_SETUP)
        self.journey_edit.setFocus()
        self._refresh_setup()

    def _open_document(self) -> None:
        from ui.jobs_dialog import open_path

        if self.guide is None:
            return
        docs = sorted(self.guide.folder.glob("*.docx"))
        if docs:
            open_path(docs[0])

    def _open_folder(self) -> None:
        from ui.jobs_dialog import open_path

        if self.guide is not None and self.guide.folder.exists():
            open_path(self.guide.folder)

    # ============================================================ العمل الخلفي
    def _run(self, task, on_success, busy_message: str) -> None:
        self._busy = True
        self.status.setText(busy_message)
        self._refresh_buttons()
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

    def _refresh_buttons(self) -> None:
        self._refresh_setup()
        if self.guide is not None:
            self._refresh_review()
        self.finish_button.setEnabled(self._recording)
        self.close_button.setEnabled(not self._recording)

    def _on_task_finished(self) -> None:
        self._busy = False
        self._thread = None
        self._worker = None
        self._refresh_buttons()

    def _on_progress(self, _fraction: float, message: str) -> None:
        if message.startswith("step\t"):
            _, count, label, action = (message.split("\t") + ["", "", ""])[:4]
            self.live_count.setText(f"الخطوات المسجّلة: {count}")
            if action == "undo":
                self.live_last.setText("تراجعتَ عن آخر خطوة.")
            elif label:
                self.live_last.setText(f"آخر خطوة: {label}")
            return
        self.status.setText(message)

    def _on_failed(self, message: str) -> None:
        if self._recording:
            self._recording = False
            self.pages.setCurrentIndex(PAGE_SETUP)
        self.status.setText("")
        QMessageBox.warning(self, "سجّلني", message)

    # ================================================================ الإغلاق
    def reject(self) -> None:
        if self._recording:
            QMessageBox.information(
                self, "تسجيل جارٍ",
                "أنهِ التسجيل أولًا (زر «إنهاء وحفظ» في الشريط أعلى الشاشة أو هنا).")
            return
        if self._busy and self._thread is not None:
            self._thread.wait(15000)
        if self.guide is not None and self.pages.currentIndex() == PAGE_REVIEW:
            self._store_steps()
            self._store_systems()
            self.guide.save()
        super().reject()

    def closeEvent(self, event) -> None:
        if self._recording:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
