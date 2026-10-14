"""ذيل سجلّ OBS لتشخيص فشل البدء دون أن يفتّش المستخدم عن الملفات."""
from __future__ import annotations

from pathlib import Path
from typing import List

from recording.obs_config import config_dir

_MARKERS = ("error", "failed", "unable", "cannot", "warning: ")


def log_tail(exe: Path, max_lines: int = 8) -> str:
    """آخر أسطر الخطأ في أحدث سجلّ لـ OBS، أو نص فارغ."""
    try:
        logs = sorted((config_dir(exe) / "logs").glob("*.txt"),
                      key=lambda p: p.stat().st_mtime)
        if not logs:
            return ""
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    hits: List[str] = [ln.strip() for ln in text.splitlines()
                       if any(m in ln.lower() for m in _MARKERS)]
    return "\n".join(hits[-max_lines:])
