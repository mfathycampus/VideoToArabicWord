"""تشغيل الـ pipeline من سطر الأوامر (بلا واجهة)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.deps import require_ready              # noqa: E402


def main() -> int:
    require_ready()

    from config.settings import AppConfig
    from core.pipeline import VideoToDocPipeline
    from utils.logger import setup_logger

    parser = argparse.ArgumentParser(description="فيديو → مستند Word عربي")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path.home() / "VideoToDocOutput")
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--allow-download", action="store_true",
                        help="السماح بتنزيل النموذج إن لم يكن مثبّتًا")
    args = parser.parse_args()

    config = AppConfig()
    if args.model:
        config.whisper.model_size = args.model
    config.whisper.device = args.device
    config.application.output_dir = args.output
    setup_logger(args.output / "logs")

    pipeline = VideoToDocPipeline(args.output, config)
    last = [-1]

    def progress(pct: float, message: str) -> None:
        step = int(pct)
        if step != last[0]:
            last[0] = step
            print(f"\r[{step:3d}%] {message[:70]:<70s}", end="", flush=True)

    try:
        result = pipeline.run(args.video, progress_callback=progress,
                              allow_model_download=args.allow_download)
    except Exception as exc:
        print(f"\nفشل: {exc}", file=sys.stderr)
        return 1
    print(f"\nتم: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
