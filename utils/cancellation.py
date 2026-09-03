from __future__ import annotations

import threading

from core.exceptions import PipelineCancelledError


class CancellationToken:
    """إلغاء وإيقاف مؤقت تعاوني وآمن بين الخيوط."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._resume.set()   # يحرر أي خيط منتظر في الإيقاف المؤقت

    def pause(self) -> None:
        if not self._cancelled.is_set():
            self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_paused(self) -> bool:
        return not self._resume.is_set()

    def raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise PipelineCancelledError("أُلغيت العملية بطلب المستخدم.")

    def wait_if_paused(self, poll_seconds: float = 0.2) -> None:
        while not self._resume.wait(timeout=poll_seconds):
            if self._cancelled.is_set():
                raise PipelineCancelledError("أُلغيت العملية أثناء الإيقاف المؤقت.")
        self.raise_if_cancelled()
