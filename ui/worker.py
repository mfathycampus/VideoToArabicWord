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
from utils.logger import logger


class PipelineWorker(QObject):
    progress = pyqtSignal(float, str)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, pipeline: VideoToDocPipeline, video_path: Path,
                 cancel_token: CancellationToken,
                 allow_model_download: bool = False) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.video_path = video_path
        self.cancel_token = cancel_token
        self.allow_model_download = allow_model_download

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.pipeline.run(
                self.video_path,
                cancel_token=self.cancel_token,
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
                allow_model_download=self.allow_model_download)
            self.completed.emit(str(result))
        except PipelineCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("فشل المعالجة في العامل الخلفي")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()
