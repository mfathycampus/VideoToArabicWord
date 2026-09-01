"""واجهة موحّدة لكل استدعاءات FFmpeg — لا يُستدعى ffmpeg خارج هذه الوحدة."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.exceptions import FFmpegExecutionError, MediaValidationError
from utils.logger import logger
from utils.media_probe import _CREATE_NO_WINDOW, resolve_ffmpeg


class FFmpegService:
    def __init__(self) -> None:
        self._exe: Optional[str] = None

    @property
    def executable(self) -> str:
        if self._exe is None:
            self._exe = resolve_ffmpeg()
        return self._exe

    def _run(self, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
        cmd = [self.executable, "-y", "-hide_banner", "-loglevel", "error", *args]
        logger.debug(f"ffmpeg: {' '.join(cmd[:8])} …")
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=_CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegExecutionError(
                f"تجاوز FFmpeg المهلة ({timeout}s).") from exc

    def extract_audio(self, video_path: Path, output_path: Path,
                      sample_rate: int = 16000) -> Path:
        """يستخرج صوتًا أحاديًا 16kHz PCM — الصيغة التي يتوقعها Whisper."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run([
            "-i", str(video_path),
            "-vn",                       # تجاهل الفيديو
            "-map", "0:a:0",             # أول مسار صوتي فقط
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",                  # أحادي
            str(output_path),
        ])
        if result.returncode != 0 or not output_path.exists():
            raise FFmpegExecutionError(
                f"فشل استخراج الصوت: {result.stderr.strip()[:400]}")
        if output_path.stat().st_size == 0:
            raise FFmpegExecutionError("الملف الصوتي الناتج فارغ.")
        return output_path

    def extract_frame(self, video_path: Path, timestamp: float,
                      output_path: Path, width: Optional[int] = None) -> Path:
        """يستخرج إطارًا واحدًا عند زمن محدد.

        يحترم بيانات الدوران تلقائيًا (بعكس OpenCV)، ويُستخدم كمسار
        احتياطي عندما يعجز OpenCV عن فك ترميز معيّن.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filters = ["scale=%d:-2" % width] if width else []
        args = ["-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video_path),
                "-frames:v", "1"]
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-q:v", "2", str(output_path)]
        result = self._run(args, timeout=120)
        if result.returncode != 0 or not output_path.exists():
            raise FFmpegExecutionError(
                f"فشل استخراج الإطار عند {timestamp:.2f}s: "
                f"{result.stderr.strip()[:300]}")
        return output_path

    def validate_readable(self, video_path: Path) -> None:
        """يتحقق من إمكانية فك ترميز الفيديو فعليًا قبل بدء الـ pipeline."""
        result = self._run(["-i", str(video_path), "-frames:v", "1",
                            "-f", "null", "-"], timeout=120)
        if result.returncode != 0:
            raise MediaValidationError(
                f"تعذر فك ترميز الفيديو: {result.stderr.strip()[:300]}")
