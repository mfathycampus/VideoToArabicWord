"""تقرير المقرّر من سطر الأوامر.

    python -m tools.course_report ~/VideoToDocOutput
    python -m tools.course_report ~/VideoToDocOutput --title "شبكات ٢٠١" --open
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.course import build_report            # noqa: E402
from document.course_report import render_html  # noqa: E402
from utils.console import enable_utf8_console   # noqa: E402


def main() -> int:
    enable_utf8_console()
    parser = argparse.ArgumentParser(
        description="فهرس المقرّر وتقرير التغطية من مجلد الإخراج")
    parser.add_argument("output_dir", type=Path,
                        help="مجلد الإخراج الذي يحوي مجلدات المهامّ")
    parser.add_argument("--title", default="", help="اسم المقرّر")
    parser.add_argument("--out", type=Path, default=None,
                        help="مسار الصفحة (الافتراضي: داخل مجلد الإخراج)")
    parser.add_argument("--open", action="store_true",
                        help="افتح الصفحة بعد كتابتها")
    args = parser.parse_args()

    if not args.output_dir.is_dir():
        print(f"المجلد غير موجود: {args.output_dir}", file=sys.stderr)
        return 2

    report = build_report(args.output_dir, args.title)
    # داخل مجلد الإخراج افتراضيًّا: الروابط إلى مخرجات المهامّ نسبيّة،
    # فالتقرير هناك يفتحها بنقرة. وحفظه في مكان آخر يكسر الروابط وحدها.
    target = args.out or (args.output_dir / "تقرير المقرّر.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(report, target.parent), encoding="utf-8")

    print(f"{report.completed} من {report.total} محاضرة مكتملة.")
    for remedy, count in sorted(report.remedies.items(),
                                key=lambda item: -item[1]):
        print(f"  ينقص {count}: {remedy}")
    print(f"التقرير: {target}")

    if args.open:
        import webbrowser

        webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
