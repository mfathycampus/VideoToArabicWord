"""تشخيص بيئة التشغيل.

    python tools/doctor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.deps import diagnose, format_report  # noqa: E402


def _report_engines() -> None:
    """حالة كل محرّك تفريغ مع سبب عدم الجاهزية إن وُجد.

    المحرّكات الاختيارية ليست شرط تشغيل، فلا تُحسب في رمز الخروج —
    لكنها أكثر ما يُربك المستخدم حين تقول الواجهة «غير مثبّت» بعد
    تثبيت ناجح ظاهريًا.
    """
    from audio.engines.registry import ENGINE_ORDER, build_engine

    print("\nمحرّكات التفريغ  /  ASR engines")
    print("=" * 62)
    print(f" المفسّر: {sys.executable}")
    for name in ENGINE_ORDER:
        try:
            engine = build_engine(name)
            ready, reason = engine.diagnose()
        except Exception as exc:
            ready, reason = False, f"{type(exc).__name__}: {exc}"
        mark = "OK  " if ready else "----"
        print(f" [{mark}] {name}")
        for line in str(reason).splitlines():
            print(f"          {line}")
    print("=" * 62)


def main() -> int:
    diagnosis = diagnose()
    print(format_report(diagnosis))
    try:
        _report_engines()
    except Exception as exc:      # لا يمنع تقرير البيئة الأساسي
        print(f"\nتعذّر فحص المحرّكات: {exc}")
    return 0 if diagnosis.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
