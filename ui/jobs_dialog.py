"""لوحة المهام السابقة.

الاستئناف مبنيّ منذ الإصدار 1.2 ومختبَر، لكنه كان غير مرئي: لا شيء يقول
للمستخدم ما عالجه، ولا ما توقّف عند 60%، ولا أين ذهب المستند. هذه النافذة
تجعل تلك القدرة قابلة للاستعمال.

القراءة فقط، إلا زرًّا واحدًا يكتب: «أعد بناء المستند» — وهو يكتب المستند
لا حالة المهمة.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.job_registry import JobSummary, list_jobs

COLUMNS = ("المهمة", "الحالة", "المرحلة", "التقدّم", "آخر تحديث")


def open_path(path: Path) -> None:
    """يفتح ملفًا أو مجلدًا بمستعرض النظام."""
    if sys.platform == "win32":
        os.startfile(str(path))          # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class JobsDialog(QDialog):
    def __init__(self, output_dir: Path, parent=None,
                 on_resume: Optional[Callable[[JobSummary], None]] = None,
                 on_rebuild: Optional[Callable[[JobSummary], None]] = None
                 ) -> None:
        super().__init__(parent)
        self.output_dir = Path(output_dir)
        self.on_resume = on_resume
        self.on_rebuild = on_rebuild
        self.jobs: List[JobSummary] = []

        self.setWindowTitle("المهام السابقة")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(820, 460)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self.path_label = QLabel(str(self.output_dir))
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self.path_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_document())
        layout.addWidget(self.table, 1)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self.detail)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("فتح المستند")
        self.open_button.clicked.connect(self.open_document)
        self.folder_button = QPushButton("فتح المجلد")
        self.folder_button.clicked.connect(self.open_folder)
        self.rebuild_button = QPushButton("أعد بناء المستند")
        self.rebuild_button.setToolTip(
            "يعيد التوليد من الخطة المحفوظة بالإعدادات الحالية — ثوانٍ.")
        self.rebuild_button.clicked.connect(self.rebuild)
        self.resume_button = QPushButton("أكمل المعالجة")
        self.resume_button.setToolTip(
            "يتابع من أول مرحلة غير مكتملة — لا يعيد ما أُنجز.")
        self.resume_button.clicked.connect(self.resume)
        refresh = QPushButton("تحديث")
        refresh.clicked.connect(self.refresh)
        close = QPushButton("إغلاق")
        close.clicked.connect(self.accept)

        for button in (self.open_button, self.folder_button,
                       self.rebuild_button, self.resume_button, refresh,
                       close):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.jobs = list_jobs(self.output_dir)
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            updated = (job.updated_at.strftime("%Y-%m-%d %H:%M")
                       if job.updated_at else "—")
            values = (job.name, job.status_label, job.stage or "—",
                      f"{job.progress:.0f}%", updated)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        if self.jobs:
            self.table.selectRow(0)
        self._update_buttons()

    def selected(self) -> Optional[JobSummary]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self.jobs[index] if 0 <= index < len(self.jobs) else None

    def _update_buttons(self) -> None:
        job = self.selected()
        self.open_button.setEnabled(bool(job and job.document))
        self.folder_button.setEnabled(job is not None)
        self.rebuild_button.setEnabled(bool(job and job.can_rebuild))
        self.resume_button.setEnabled(
            bool(job and job.can_resume and self.on_resume))

        if job is None:
            self.detail.setText(
                "لا توجد مهام في مجلد الحفظ بعد." if not self.jobs else "")
            return
        parts = [f"المصدر: {job.source.name if job.source else '—'}"]
        if job.document:
            parts.append(f"المخرج: {job.document.name}")
        if job.last_error:
            parts.append(f"آخر خطأ: {job.last_error.splitlines()[0][:160]}")
        if job.source and not job.source.exists():
            parts.append("⚠ الملف المصدر لم يعد في مكانه — المتابعة متعذّرة.")
        self.detail.setText("   ·   ".join(parts))

    # ------------------------------------------------------------------
    def open_document(self) -> None:
        job = self.selected()
        if job and job.document and job.document.exists():
            open_path(job.document)

    def open_folder(self) -> None:
        job = self.selected()
        if job and job.job_dir.exists():
            open_path(job.job_dir)

    def rebuild(self) -> None:
        job = self.selected()
        if job and self.on_rebuild:
            self.on_rebuild(job)
            self.refresh()
        elif job:
            QMessageBox.information(self, "غير متاح",
                                    "إعادة البناء غير متاحة من هنا.")

    def resume(self) -> None:
        job = self.selected()
        if job and self.on_resume:
            self.on_resume(job)
            self.accept()
