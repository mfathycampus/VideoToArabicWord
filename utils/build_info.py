"""معرّف البناء — أيّ نسخة من الكود أنتجت هذا المخرج بالضبط؟

رُصد عند تقييم تشغيل حقيقي: ``audit.jsonl`` يقول ``1.15.1`` والمستودع عند
``1.16.3``، ولا شيء يقول هل التشغيل من الإصدار المنشور أم من تعديلٍ محلّي
لم يُرفع. رقم الإصدار وحده لا يفرّق بين بناءين بالرقم نفسه.

الترتيب: متغيّر البيئة ``MOEEN_BUILD_ID`` (يضبطه خطّ البناء)، ثم ملف
``BUILD_ID`` المشحون مع الحزمة (يكتبه ``build.spec``)، ثم رأس git عند
التشغيل من المصدر — مقروءًا من ملفات ``.git`` مباشرةً بلا تشغيل git،
فلا يتأخّر بدء البرنامج ولا يعتمد على وجوده في PATH.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

BUILD_ID_FILENAME = "BUILD_ID"
_ROOT = Path(__file__).resolve().parents[1]


def _git_head(root: Path) -> Optional[str]:
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7] or None
        ref = head.split(":", 1)[1].strip()
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()[:7] or None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:7]
    except OSError:
        return None
    return None


@lru_cache(maxsize=1)
def build_id() -> str:
    """``abc1234`` أو ``source:abc1234`` أو ``unknown`` — لا يرمي أبدًا."""
    env = os.environ.get("MOEEN_BUILD_ID", "").strip()
    if env:
        return env
    bases = [Path(getattr(sys, "_MEIPASS", _ROOT)), _ROOT]
    for base in bases:
        try:
            value = (base / BUILD_ID_FILENAME).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    head = _git_head(_ROOT)
    return f"source:{head}" if head else "unknown"
