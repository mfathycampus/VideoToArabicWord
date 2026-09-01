"""تشخيص بيئة التشغيل.

    python tools/doctor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.deps import diagnose, format_report  # noqa: E402


def main() -> int:
    diagnosis = diagnose()
    print(format_report(diagnosis))
    return 0 if diagnosis.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
