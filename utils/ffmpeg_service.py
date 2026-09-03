"""واجهة موحّدة لكل استدعاءات FFmpeg — لا يُستدعى ffmpeg خارج هذه الوحدة."""
from __future__ import annotations

import subprocess
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

    # سلسلة تنظيف محافظة: تُزيل ما يضرّ التفريغ ولا تمسّ الكلام.
    #   highpass=80   : يقطع هدير المكيّف وضجيج المروحة تحت 80Hz
    #   afftdn        : خفض ضوضاء طيفي خفيف
    #   loudnorm      : توحيد المستوى — تسجيل يتذبذب فيه بُعد الميكروفون
    #                   يُربك VAD فيبتلع مقاطع كاملة
    DENOISE_FILTER = "highpass=f=80,afftdn=nf=-25,loudnorm=I=-16:TP=-1.5:LRA=11"

    def extract_audio(self, video_path: Path, output_path: Path,
                      sample_rate: int = 16000,
                      start_seconds: float = 0.0,
                      end_seconds: Optional[float] = None,
                      denoise: bool = False) -> Path:
        """يستخرج صوتًا أحاديًا 16kHz PCM — الصيغة التي يتوقعها Whisper.

        ``-ss`` قبل ``-i`` للبحث السريع (يقفز بلا فكّ ترميز)، و ``-to``
        بعده لأنه يُحسب من نقطة البدء.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        if start_seconds > 0:
            args += ["-ss", f"{start_seconds:.3f}"]
        args += ["-i", str(video_path)]
        if end_seconds is not None:
            args += ["-to", f"{max(0.0, end_seconds - start_seconds):.3f}"]
        args += [
            "-vn",                       # تجاهل الفيديو
            "-map", "0:a:0",             # أول مسار صوتي فقط
        ]
        if denoise:
            args += ["-af", self.DENOISE_FILTER]
        args += [
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",                  # أحادي
            str(output_path),
        ]
        result = self._run(args)
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

    def validate_readable(self, media_path: Path,
                          expect_video: bool = True) -> None:
        """يتحقق من إمكانية فك ترميز المصدر فعليًا قبل بدء الـ pipeline.

        ‏``-frames:v 1`` يفشل حتمًا على ملف صوتي سليم، فلا يصلح فحصًا
        موحّدًا. لمصدر صوتي نفكّ عيّنة من الصوت بدل الإطار.
        """
        args = (["-i", str(media_path), "-frames:v", "1", "-f", "null", "-"]
                if expect_video else
                ["-i", str(media_path), "-t", "1", "-f", "null", "-"])
        result = self._run(args, timeout=120)
        if result.returncode != 0:
            kind = "الفيديو" if expect_video else "الملف الصوتي"
            raise MediaValidationError(
                f"تعذر فك ترميز {kind}: {result.stderr.strip()[:300]}")
