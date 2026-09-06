"""تشغيل الـ pipeline من سطر الأوامر (بلا واجهة)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.profiles import profile_choices        # noqa: E402
from utils.console import enable_utf8_console     # noqa: E402
from utils.deps import require_ready              # noqa: E402
from utils.error_reporting import format_error_for_user  # noqa: E402


def main() -> int:
    enable_utf8_console()
    require_ready()

    from config.settings import AppConfig
    from core.pipeline import VideoToDocPipeline
    from utils.logger import setup_logger

    parser = argparse.ArgumentParser(description="فيديو → مستند Word عربي")
    parser.add_argument("--video", type=Path,
                        help="ملف الفيديو المراد معالجته")
    parser.add_argument("--audio", type=Path,
                        help="ملف صوتي (أي امتداد) — مستند نصّي بلا لقطات")
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "VideoToDocOutput")
    parser.add_argument("--config", type=Path, default=None,
                        help="ملف إعداد YAML (الافتراضي: إعداد المستخدم)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--engine", default=None,
                        choices=["faster-whisper", "cohere-arabic"],
                        help="محرّك التفريغ (ADR-017)")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cpu", "cuda"])
    parser.add_argument("--rewrite", action="store_true",
                        help="إعادة صياغة النص بالذكاء الاصطناعي (يُرسل النص)")
    parser.add_argument("--rebuild", type=Path, default=None, metavar="JOB_DIR",
                        help="أعد بناء المستند من خطة محفوظة بلا إعادة معالجة")
    parser.add_argument("--batch", type=Path, default=None, metavar="DIR",
                        help="عالج كل ملفات الوسائط في مجلد، واحدًا تلو الآخر")
    parser.add_argument("--merge", type=Path, nargs="+", default=None,
                        metavar="JOB_DIR",
                        help="ادمج مهامّ منتهية في مستند واحد")
    parser.add_argument("--merge-output", type=Path, default=None,
                        help="مسار المستند المدموج")
    parser.add_argument("--from", dest="clip_start", default=None,
                        help="بداية المقطع: ثوانٍ أو MM:SS أو HH:MM:SS")
    parser.add_argument("--to", dest="clip_end", default=None,
                        help="نهاية المقطع")
    parser.add_argument("--transcript-only", action="store_true",
                        help="أخرج النص وSRT وVTT وMarkdown بلا مستند Word")
    parser.add_argument("--denoise", action="store_true",
                        help="نظّف الصوت بمرشّحات ffmpeg قبل التفريغ")
    parser.add_argument(
        "--profile", default=None,
        choices=[key for key, _ in profile_choices()],
        help="نوع المحتوى: يحدّد كشف المشاهد واختيار الصور "
             "(انظر config/profiles.py)")
    parser.add_argument("--glossary", default=None,
                        help="مصطلحات المادة، مفصولة بفواصل")
    parser.add_argument("--allow-download", action="store_true",
                        help="السماح بتنزيل النموذج إن لم يكن مثبّتًا")
    args = parser.parse_args()

    if not (args.video or args.audio or args.rebuild or args.batch
            or args.merge):
        parser.error(
            "مطلوب أحد: --video أو --audio أو --batch أو --rebuild أو --merge")
    if args.video and args.audio:
        parser.error("اختر --video أو --audio، لا الاثنين معًا")

    from config.settings import default_config_path
    config = AppConfig.load(args.config or default_config_path())
    if config.load_error:
        print(config.load_error, file=sys.stderr)
    if args.model:
        config.whisper.model_size = args.model
    if args.engine:
        config.whisper.engine = args.engine
    if args.rewrite:
        config.rewrite.enabled = True
    if args.denoise:
        config.application.denoise_audio = True
    if args.profile:
        config.application.content_profile = args.profile
    if args.glossary:
        config.whisper.glossary = args.glossary
    config.whisper.device = args.device
    config.application.output_dir = args.output
    setup_logger(args.output / "logs")

    from config.settings import ClipRange

    try:
        clip = ClipRange.parse(args.clip_start, args.clip_end)
    except ValueError as exc:
        parser.error(str(exc))

    # --- الدمج في مستند واحد ---
    if args.merge:
        pipeline = VideoToDocPipeline(args.output, config)
        target = args.merge_output or (args.output / "مستند مدموج.docx")
        try:
            result = pipeline.merge_documents(args.merge, target)
        except Exception as exc:
            print(f"فشل الدمج: {format_error_for_user(exc)}", file=sys.stderr)
            return 1
        print(f"تم: {result}")
        return 0

    # --- معالجة مجلد كامل ---
    if args.batch:
        from core.batch import iter_media, process_folder

        sources = iter_media(args.batch)
        if not sources:
            print(f"لا توجد ملفات وسائط في {args.batch}", file=sys.stderr)
            return 1
        print(f"سيُعالَج {len(sources)} ملفًا…")
        results = process_folder(
            sources, args.output, config,
            allow_model_download=args.allow_download,
            transcript_only=args.transcript_only,
            clip=clip,
            on_progress=lambda i, total, name, pct: print(
                f"\r[{i}/{total}] {name[:40]:<40s} {pct:5.1f}%",
                end="", flush=True))
        print()
        failed = [r for r in results if not r.ok]
        for result in results:
            mark = "✓" if result.ok else "✗"
            detail = result.output.name if result.ok else result.error
            print(f"  {mark} {result.source.name} — {detail}")
        print(f"\nنجح {len(results) - len(failed)} من {len(results)}")
        return 1 if failed else 0

    # --- إعادة البناء: ثوانٍ بدل ساعات ---
    if args.rebuild:
        pipeline = VideoToDocPipeline(args.output, config)
        try:
            result = pipeline.rebuild_document(args.rebuild)
        except Exception as exc:
            print(f"فشل: {format_error_for_user(exc)}", file=sys.stderr)
            return 1
        print(f"تم: {result}")
        return 0

    pipeline = VideoToDocPipeline(args.output, config)
    last = [-1]

    def progress(pct: float, message: str) -> None:
        step = int(pct)
        if step != last[0]:
            last[0] = step
            print(f"\r[{step:3d}%] {message[:70]:<70s}", end="", flush=True)

    source = args.audio or args.video
    try:
        result = pipeline.run(source, progress_callback=progress,
                              allow_model_download=args.allow_download,
                              allow_audio_only=bool(args.audio),
                              clip=clip,
                              transcript_only=args.transcript_only)
    except Exception as exc:
        print(f"\nفشل: {format_error_for_user(exc)}", file=sys.stderr)
        return 1
    print(f"\nتم: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
