"""ذيل سجلّ OBS لتشخيص فشل البدء دون أن يفتّش المستخدم عن الملفات."""
from __future__ import annotations

from pathlib import Path
from typing import List

from recording.obs_config import config_dir

_MARKERS = ("error", "failed", "unable", "cannot", "warning: ")
#: أسطر يكتبها OBS عند كل إقلاع على جهاز بلا بطاقة التقاط أو بطاقة NVIDIA —
#: لا علاقة لها بفشل التسجيل، وعرضها يوهم المستخدم أنها السبب.
_BENIGN = (
    "'aja", "aja-",            # بطاقات AJA
    "decklink",               # بطاقات Blackmagic DeckLink
    "nvidia corporation",     # NVIDIA Audio/Video Effects SDK
    "nvencodeapi",            # NVENC (التسجيل يستعمل x264 فلا يحتاجه)
    "obs-nvenc",
    "nvvideoeffects", "nvaudioeffects",
)


def _relevant(line: str) -> bool:
    low = line.lower()
    return (any(m in low for m in _MARKERS)
            and not any(b in low for b in _BENIGN))


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
                       if _relevant(ln)]
    return "\n".join(hits[-max_lines:])
