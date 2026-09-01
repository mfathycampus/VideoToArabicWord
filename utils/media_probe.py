"""قراءة الميتاداتا عبر ffprobe حقيقي.

سبب وجود هذا الملف:
    ``imageio_ffmpeg.get_ffmpeg_exe()`` يعيد ثنائي **ffmpeg** فقط، والحزمة
    لا تشحن ffprobe إطلاقًا. تمرير خيارات ffprobe إليه يفشل مباشرة بـ:

        Unrecognized option 'print_format'  (returncode 8)

    لذلك يجب حلّ مسار ffprobe بشكل صريح، مع مسار بديل (fallback) يقرأ
    الميتاداتا من ffmpeg نفسه عند غياب ffprobe.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from core.exceptions import (
    FFmpegExecutionError,
    FFprobeNotFoundError,
    MediaValidationError,
)

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _bundled_dir() -> Path:
    """مجلد الثنائيات المشحونة مع التطبيق (يعمل داخل PyInstaller أيضًا)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1] / "bin"


@lru_cache(maxsize=1)
def resolve_ffmpeg() -> str:
    """يعيد مسار ffmpeg: المشحون ← PATH ← imageio-ffmpeg."""
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bundled = _bundled_dir() / name
    if bundled.is_file():
        return str(bundled)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise FFprobeNotFoundError(
            "تعذر العثور على ffmpeg. ضع ffmpeg في مجلد bin/ بجانب التطبيق."
        ) from exc


@lru_cache(maxsize=1)
def resolve_ffprobe() -> Optional[str]:
    """يعيد مسار ffprobe، أو ``None`` إن لم يوجد.

    ترتيب البحث:
        1. ``bin/`` المشحون مع التطبيق (المسار المعتمد للإنتاج)
        2. ``PATH`` النظام
        3. نفس مجلد ffmpeg المكتشَف
    """
    name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

    bundled = _bundled_dir() / name
    if bundled.is_file():
        return str(bundled)

    found = shutil.which("ffprobe")
    if found:
        return found

    sibling = Path(resolve_ffmpeg()).parent / name
    if sibling.is_file():
        return str(sibling)

    return None


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",          # مسارات/ميتاداتا عربية على Windows
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def _parse_fraction(value: str, default: float = 0.0) -> float:
    """يحوّل ``30000/1001`` إلى float بأمان."""
    if not value:
        return default
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else default
        return float(value)
    except (ValueError, ZeroDivisionError):
        return default


def probe_with_ffprobe(video_path: Path, ffprobe: str) -> dict:
    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
    ]
    result = _run(cmd)
    if result.returncode != 0:
        raise FFmpegExecutionError(
            f"فشل ffprobe (code={result.returncode}): {result.stderr.strip()[:500]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegExecutionError("مخرجات ffprobe ليست JSON صالحًا.") from exc


def probe_with_ffmpeg_fallback(video_path: Path, ffmpeg: str) -> dict:
    """مسار احتياطي: تحليل ``ffmpeg -i`` من stderr عند غياب ffprobe.

    أقل دقة من ffprobe (لا يعطي duration دقيقًا لبعض الحاويات)، ويُستخدم
    فقط لتفادي فشل كامل. الإنتاج يجب أن يشحن ffprobe.
    """
    result = _run([ffmpeg, "-hide_banner", "-i", str(video_path)])
    text = result.stderr  # ffmpeg يكتب معلومات الملف على stderr

    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mnt, s = m.groups()
        duration = int(h) * 3600 + int(mnt) * 60 + float(s)

    streams: list[dict] = []
    v = re.search(
        r"Stream #\d+:\d+.*?: Video:\s*([\w0-9]+).*?,\s*(\d+)x(\d+).*?,\s*"
        r"([\d.]+)\s*fps", text, re.S)
    if v:
        codec, w, h, fps = v.groups()
        streams.append({
            "codec_type": "video", "codec_name": codec,
            "width": int(w), "height": int(h),
            "r_frame_rate": fps, "duration": str(duration),
        })
    a = re.search(r"Stream #\d+:\d+.*?: Audio:\s*(\w+).*?,\s*(\d+)\s*Hz", text)
    if a:
        codec, rate = a.groups()
        streams.append({
            "codec_type": "audio", "codec_name": codec, "sample_rate": rate,
        })

    return {"format": {"duration": str(duration)}, "streams": streams}


def probe_raw(video_path: Path) -> dict:
    """يقرأ الميتاداتا الخام، مفضّلًا ffprobe وإلا فالمسار الاحتياطي."""
    if not video_path.exists():
        raise MediaValidationError(f"الملف غير موجود: {video_path}")
    if video_path.stat().st_size == 0:
        raise MediaValidationError(f"الملف فارغ: {video_path}")

    ffprobe = resolve_ffprobe()
    if ffprobe:
        return probe_with_ffprobe(video_path, ffprobe)
    return probe_with_ffmpeg_fallback(video_path, resolve_ffmpeg())


def _rotation_degrees(video_stream: dict) -> int:
    """يستخرج زاوية الدوران من الوسوم أو من مصفوفة العرض (Display Matrix).

    مصيدة كلاسيكية: OpenCV **يتجاهل** بيانات الدوران، فيقرأ فيديو الهاتف
    المسجَّل عموديًا كأنه أفقي مقلوب. المشغّلات تحترمها، فالمستخدم يرى
    الفيديو صحيحًا ويرى صورنا مائلة 90°.
    """
    tags = {k.lower(): v for k, v in (video_stream.get("tags") or {}).items()}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            pass
    for side in video_stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                # ffprobe يعطي الدوران بإشارة معاكسة لاتجاه التصحيح
                return int(-float(side["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    return 0


def extract_video_facts(data: dict) -> dict:
    """يستخرج الحقول التي يحتاجها ``VideoMetadata`` من مخرجات ffprobe."""
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise MediaValidationError("لا يوجد مسار فيديو صالح داخل الملف.")

    # avg_frame_rate أدق من r_frame_rate للفيديو متغير الإطارات (VFR)
    fps = _parse_fraction(video.get("avg_frame_rate", ""), 0.0)
    if fps <= 0:
        fps = _parse_fraction(video.get("r_frame_rate", ""), 0.0)
    if fps <= 0:
        raise MediaValidationError(
            "تعذر تحديد معدل الإطارات (fps). الملف قد يكون تالفًا."
        )

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise MediaValidationError("مدة الفيديو غير صالحة (0 ثانية).")

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width <= 0 or height <= 0:
        raise MediaValidationError("أبعاد الفيديو غير صالحة.")

    rotation = _rotation_degrees(video)
    # الأبعاد المعروضة تنقلب عند الدوران 90/270
    display_width, display_height = ((height, width) if rotation in (90, 270)
                                     else (width, height))

    return {
        "duration_seconds": duration,
        "width": display_width,
        "height": display_height,
        "fps": fps,
        "codec": video.get("codec_name", "unknown"),
        "has_audio": audio is not None,
        "audio_sample_rate": int(audio["sample_rate"])
        if audio and audio.get("sample_rate") else None,
        "rotation": rotation,
        "stored_width": width,
        "stored_height": height,
        "nb_frames": int(video["nb_frames"])
        if str(video.get("nb_frames", "")).isdigit() else None,
    }
