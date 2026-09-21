"""الدفعات والمهامّ المحفوظة والدمج وإعادة البناء وفتح النتيجة — منقولة من ``ui/main_window.py``.

مزيج يرثه ``MainWindow``؛ لا تغيير في السلوك.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import (
    QFileDialog,
    QMessageBox,
)

from core.pipeline import VideoToDocPipeline
from utils.cancellation import CancellationToken


class JobsPanelMixin:
    # ------------------------------------------------------------------
    def choose_folder(self) -> None:
        """يعالج كل ملفات الوسائط في مجلد، واحدًا تلو الآخر."""
        from core.batch import iter_media

        folder = QFileDialog.getExistingDirectory(
            self, "اختر مجلد الوسائط", str(Path.home()))
        if not folder:
            return
        sources = iter_media(Path(folder))
        if not sources:
            QMessageBox.information(
                self, "لا توجد ملفات",
                "لم يُعثر على ملفات فيديو أو صوت في هذا المجلد.")
            return

        answer = QMessageBox.question(
            self, "معالجة دفعية",
            f"سيُعالَج {len(sources)} ملفًا واحدًا تلو الآخر:\n\n"
            + "\n".join(f"  • {p.name}" for p in sources[:8])
            + (f"\n  … و{len(sources) - 8} غيرها" if len(sources) > 8 else "")
            + "\n\nفشل ملف لا يوقف البقية. هل نبدأ؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_batch(sources)

    def _start_batch(self, sources) -> None:
        if not self._license_allows_processing():
            return
        from ui.worker import BatchWorker

        try:
            clip = self._current_clip()
        except ValueError as exc:
            QMessageBox.warning(self, "نطاق غير صالح", str(exc))
            return

        self.cancel_token = CancellationToken()
        self.thread = QThread(self)
        self.worker = BatchWorker(
            sources, self.config.application.output_dir, self.config,
            self.cancel_token,
            allow_model_download=False,
            transcript_only=self.transcript_only_check.isChecked(),
            clip=clip)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.file_done.connect(self.on_batch_file_done)
        self.worker.completed.connect(self.on_batch_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_buttons)

        self._set_running(True)
        self.append_log(f"بدأت معالجة {len(sources)} ملفًا…")
        self.thread.start()

    def on_batch_file_done(self, name: str, ok: bool, detail: str) -> None:
        self.append_log(f"{'✓' if ok else '✗'} {name} — {detail}")

    def on_batch_completed(self, succeeded: int, total: int) -> None:
        self.progress.setValue(100)
        self.status_label.setText(f"اكتملت الدفعة: {succeeded} من {total}.")
        QMessageBox.information(
            self, "اكتملت الدفعة",
            f"نجح {succeeded} من {total}.\n"
            f"المخرجات في:\n{self.config.application.output_dir}")

    # ------------------------------------------------------------------
    def show_jobs(self) -> None:
        from ui.jobs_dialog import JobsDialog

        dialog = JobsDialog(self.config.application.output_dir, self,
                            on_resume=self._resume_job,
                            on_rebuild=self._rebuild_job)
        dialog.exec()

    def _rebuild_job(self, job) -> None:
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            result = pipeline.rebuild_document(job.job_dir)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّرت إعادة البناء", str(exc))
            return
        self.append_log(f"أُعيد بناء المستند: {result}")
        QMessageBox.information(self, "تم", f"أُعيد بناء المستند:\n{result}")

    def _resume_job(self, job) -> None:
        """يعيد اختيار مصدر المهمة ويبدأ — الاستئناف يتكفّل بالباقي."""
        if job.source is None or not job.source.exists():
            QMessageBox.warning(self, "المصدر مفقود",
                                "لم يعد ملف المصدر في مكانه.")
            return
        from core.batch import is_audio

        self.video_path = job.source
        self.audio_only = is_audio(job.source)
        self.file_label.setText(
            f"{job.source.name}   ({'صوت' if self.audio_only else 'فيديو'})")
        self.start_button.setEnabled(True)
        self.append_log(f"استئناف: {job.name} (من {job.stage or 'البداية'})")
        self.start()

    def merge_jobs(self) -> None:
        """يدمج مهامّ منتهية في مستند واحد."""
        from core.job_registry import list_jobs

        jobs = [j for j in list_jobs(self.config.application.output_dir)
                if j.can_rebuild]
        if len(jobs) < 2:
            QMessageBox.information(
                self, "لا يكفي",
                "الدمج يحتاج مهمّتين منتهيتين على الأقل في مجلد الحفظ.")
            return

        from PyQt6.QtWidgets import QInputDialog

        names = [j.name for j in jobs]
        chosen, ok = QInputDialog.getItem(
            self, "دمج مهام",
            "الدمج يشمل كل المهام المنتهية بترتيب آخر تحديث.\n"
            "اختر اسم المستند الناتج:",
            [f"مستند مدموج — {len(jobs)} أجزاء"] + names, 0, True)
        if not ok or not chosen:
            return

        target = (self.config.application.output_dir
                  / f"{chosen.replace('/', '-')}.docx")
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            result = pipeline.merge_documents([j.job_dir for j in jobs], target)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّر الدمج", str(exc))
            return
        self.result_path = result
        self.open_button.setEnabled(True)
        self.append_log(f"مستند مدموج: {result}")
        QMessageBox.information(self, "تم", f"أُنشئ المستند المدموج:\n{result}")

    def rebuild_document(self) -> None:
        """يعيد توليد المستند من الخطة المحفوظة بالإعدادات الحالية."""
        if self.video_path is None:
            return
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            job_dir = pipeline.job_dir_for(self.video_path,
                                           self._current_clip())
        except ValueError:
            job_dir = pipeline.job_dir_for(self.video_path)
        self.rebuild_button.setEnabled(False)
        try:
            result = pipeline.rebuild_document(job_dir)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّرت إعادة البناء", str(exc))
            self.append_log(f"تعذّرت إعادة البناء: {exc}")
            return
        finally:
            self.rebuild_button.setEnabled(True)
        self.result_path = result
        self.open_button.setEnabled(True)
        self.append_log(f"أُعيد بناء المستند: {result}")
        QMessageBox.information(self, "تم", f"أُعيد بناء المستند:\n{result}")

    def _refresh_rebuild_button(self) -> None:
        """يُفعَّل الزر فقط إن وُجدت خطة محفوظة لهذا الفيديو."""
        enabled = False
        if self.video_path is not None:
            pipeline = VideoToDocPipeline(
                self.config.application.output_dir, self.config)
            try:
                job_dir = pipeline.job_dir_for(self.video_path,
                                               self._current_clip())
            except ValueError:
                job_dir = pipeline.job_dir_for(self.video_path)
            enabled = (job_dir / "plan.json").exists()
        self.rebuild_button.setEnabled(enabled)

    def open_result(self) -> None:
        if self.result_path is None or not self.result_path.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(self.result_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(self.result_path)])
        else:
            subprocess.run(["xdg-open", str(self.result_path)])
