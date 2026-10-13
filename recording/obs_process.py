"""تشغيل OBS وإغلاقه كعملية فرعية."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from core.exceptions import RecordingToolUnavailableError
from utils.logger import logger


class ObsProcess:
    def __init__(self, exe: Path, args: List[str], *,
                 popen: Callable = subprocess.Popen) -> None:
        self.exe = Path(exe)
        self.args = list(args)
        self._popen = popen
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        flags = 0
        if sys.platform.startswith("win"):
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            # مجلد العمل = مجلد التنفيذي: OBS يبحث عن data وplugins نسبةً إليه.
            self._proc = self._popen(
                self.args, cwd=str(self.exe.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags)
        except OSError as exc:
            raise RecordingToolUnavailableError(
                f"تعذّر تشغيل أداة التسجيل ({exc}). قد يكون مضاد الفيروسات "
                "حجبها — أضف مجلدها إلى الاستثناءات أو ثبّت OBS يدويًا.") from exc

    @property
    def pid(self) -> Optional[int]:
        return None if self._proc is None else self._proc.pid

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self, grace: float = 8.0) -> None:
        """إغلاق لطيف أولًا ثم قسري. آمن لو لم تكن العملية تعمل."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform.startswith("win"):
                # بلا /F: يرسل WM_CLOSE فيُغلق OBS إغلاقًا نظيفًا.
                subprocess.run(["taskkill", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10)
            else:
                proc.terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"إغلاق OBS اللطيف فشل: {exc}")
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.2)
        if proc.poll() is None:
            logger.warning("OBS لم يُغلق في المهلة — إغلاق قسري.")
            proc.kill()
