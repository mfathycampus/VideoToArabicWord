"""خدمة المجلد الوارد.

    python -m tools.watch_folder --inbox "\\\\srv\\محاضرات\\وارد" --output D:\\مخرجات

يُسقط المعلّم تسجيله في مجلد الوارد وينصرف؛ يعالجه هذا الجهاز ويكتب
مخرجاته في مجلد الإخراج، ويحدّث تقرير المقرّر بعد كل ملفّ.

للتشغيل ليلًا وحده:  ``--once`` مع مهمّة مجدولة في ويندوز عند 11 مساءً.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import AppConfig, default_config_path  # noqa: E402
from utils.cancellation import CancellationToken            # noqa: E402
from utils.console import enable_utf8_console               # noqa: E402
from utils.logger import setup_logger                       # noqa: E402


def main() -> int:
    enable_utf8_console()
    parser = argparse.ArgumentParser(description="معالجة تلقائية لمجلد وارد")
    parser.add_argument("--inbox", type=Path, required=True,
                        help="المجلد الذي يُسقط فيه المعلّمون تسجيلاتهم")
    parser.add_argument("--output", type=Path, required=True,
                        help="مجلد المخرجات")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--once", action="store_true",
                        help="دورة واحدة ثم خروج (لمهمّة مجدولة ليلية)")
    parser.add_argument("--poll", type=float, default=20.0,
                        help="ثوانٍ بين دورات الفحص (الافتراضي 20)")
    parser.add_argument("--study", action="store_true",
                        help="ولّد الحزمة التعليمية لكل ملفّ")
    parser.add_argument("--export", default=None,
                        help="صيغ الإخراج مفصولة بفواصل")
    parser.add_argument("--allow-download", action="store_true",
                        help="اسمح بتنزيل النموذج عند أول تشغيل")
    parser.add_argument("--no-report", action="store_true",
                        help="لا تُحدِّث تقرير المقرّر بعد كل دورة")
    args = parser.parse_args()

    setup_logger(args.output / "logs")
    config = AppConfig.load(args.config or default_config_path())
    if config.load_error:
        print(config.load_error, file=sys.stderr)
    if args.study:
        config.study.enabled = True
    if args.export is not None:
        chosen = [f.strip().lower() for f in args.export.split(",") if f.strip()]
        config.document.export_formats = [] if chosen == ["none"] else chosen
    config.application.output_dir = args.output
    # الخدمة تعمل بلا مشرف: السجلّ هو الأثر الوحيد لما جرى ليلًا.
    config.application.audit_log = True

    from core.watcher import watch

    token = CancellationToken()
    try:
        processed = watch(
            args.inbox, args.output, config,
            allow_model_download=args.allow_download,
            poll_seconds=args.poll,
            max_cycles=1 if args.once else None,
            cancel_token=token,
            on_event=lambda message: print(message, flush=True))
    except KeyboardInterrupt:
        # ‏Ctrl+C على خدمة تعمل ليلًا ليس عطلًا — هو طريقة إيقافها.
        print("\nأُوقفت المراقبة.")
        return 0

    if not args.no_report and processed:
        _write_report(args.output)
    print(f"عولج {processed} ملفًّا.")
    return 0


def _write_report(output_dir: Path) -> None:
    try:
        from core.course import build_report
        from document.course_report import render_html

        report = build_report(output_dir)
        target = output_dir / "تقرير المقرّر.html"
        target.write_text(render_html(report, output_dir), encoding="utf-8")
        print(f"حُدِّث التقرير: {target}")
    except Exception as exc:
        # التقرير مخرج إضافي لا شرط نجاح — عقد المُصيِّرات نفسه.
        print(f"تعذّر تحديث التقرير: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
