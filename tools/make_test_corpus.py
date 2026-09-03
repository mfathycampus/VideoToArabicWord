"""يولّد مصفوفة فيديوهات اختبار تغطي الحاويات والترميزات والحالات الحدّية.

الهدف: إثبات أن الـ pipeline يعالج "كل أنواع الفيديوهات" — لا حالة سعيدة واحدة.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
FF = "ffmpeg"


def run(args: list[str]) -> bool:
    result = subprocess.run([FF, "-y", "-loglevel", "error", *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ! ffmpeg: {result.stderr.strip()[:200]}")
    return result.returncode == 0


def slide(index: int, w: int, h: int) -> np.ndarray:
    """شريحة نصية اصطناعية مميزة بصريًا."""
    rng = np.random.default_rng(index * 17 + 3)
    img = np.full((h, w, 3), 248, np.uint8)
    band = int(h * 0.12)
    cv2.rectangle(img, (0, 0), (w, band),
                  tuple(int(v) for v in rng.integers(40, 190, 3)), -1)
    line_h = max(4, int(h * 0.03))
    for i in range(int(rng.integers(5, 11))):
        y = band + int(h * 0.08) + i * int(h * 0.07)
        if y + line_h >= h:
            break
        cv2.rectangle(img, (int(w * 0.07), y),
                      (int(w * 0.07) + int(rng.integers(w * 0.2, w * 0.8)),
                       y + line_h), (35, 35, 35), -1)
    return img


def write_raw(path: Path, slides: int, w: int, h: int, fps: int,
              sec_per_slide: int, noise: bool = True) -> Path:
    """يكتب فيديو خام غير مضغوط ليُعاد ترميزه لاحقًا."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"),
                             fps, (w, h))
    rng = np.random.default_rng(1)
    for k in range(slides):
        base = slide(k, w, h)
        for _ in range(fps * sec_per_slide):
            frame = base
            if noise:
                frame = np.clip(base.astype(np.int16)
                                + rng.integers(-3, 4, (h, w, 3)), 0, 255
                                ).astype(np.uint8)
            writer.write(frame)
    writer.release()
    return path


SPECS = [
    # (اسم, وصف, وسائط ffmpeg إضافية, امتداد)
    ("h264_mp4",    "H.264 / MP4 — الحالة المرجعية",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"], "mp4"),
    ("h265_mp4",    "H.265 / HEVC",
     ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"], "mp4"),
    ("h264_mkv",    "H.264 في حاوية MKV",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"], "mkv"),
    ("h264_mov",    "H.264 في حاوية MOV",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"], "mov"),
    ("h264_ts",     "H.264 في حاوية MPEG-TS (بلا فهرس)",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"], "ts"),
    # pix_fmt إلزامي: مصدر FFV1 قد يُقرأ بقناة ألفا فيرفض libvpx الترميز
    # برسالة "Transparency encoding with auto_alt_ref does not work"
    ("vp8_webm",    "VP8 / WebM",
     ["-c:v", "libvpx", "-b:v", "800k", "-pix_fmt", "yuv420p",
      "-auto-alt-ref", "0", "-deadline", "realtime", "-cpu-used", "8"],
     "webm"),
    ("av1_mkv",     "AV1 (ترميز حديث)",
     ["-c:v", "libsvtav1", "-preset", "12", "-crf", "40"], "mkv"),
    ("mpeg4_avi",   "MPEG-4 Part 2 / AVI (ملفات قديمة)",
     ["-c:v", "mpeg4", "-q:v", "5"], "avi"),
    ("mjpeg_avi",   "MJPEG (كاميرات ومسجلات شاشة)",
     ["-c:v", "mjpeg", "-q:v", "3"], "avi"),
    ("msmpeg4_wmv", "MS-MPEG4 / WMV",
     ["-c:v", "msmpeg4v2", "-q:v", "5"], "wmv"),
]


def build() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    raw = OUT / "_raw.mkv"
    print("توليد المصدر الخام (5 شرائح × 4s @ 640x360)...")
    write_raw(raw, slides=5, w=640, h=360, fps=25, sec_per_slide=4)
    tone = ["-f", "lavfi", "-i", "sine=frequency=330:duration=20"]

    # --- 1. مصفوفة الحاويات والترميزات ---
    for name, desc, vargs, ext in SPECS:
        path = OUT / f"{name}.{ext}"
        acodec = ["-c:a", "libvorbis"] if ext == "webm" else \
                 ["-c:a", "mp2"] if ext in ("avi", "wmv", "ts") else ["-c:a", "aac"]
        ok = run(["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
                  *vargs, *acodec, "-shortest", str(path)])
        if ok and path.exists():
            manifest.append({"file": path.name, "desc": desc,
                             "expect_scenes": 5, "expect_audio": True,
                             "category": "codec_container"})
            print(f"  ✓ {name}.{ext}")

    # --- 2. الحالات الحدّية ---
    edge: list[tuple] = [
        ("no_audio.mp4", "بلا مسار صوتي إطلاقًا",
         ["-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"],
         {"expect_scenes": 5, "expect_audio": False}),

        ("portrait.mp4", "عمودي 720×1280 (فيديو هاتف)",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "scale=720:1280", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        # ملاحظة: حالة الدوران تُولَّد في القسم «2ب» أدناه عبر
        # ``-display_rotation``. كان هنا مدخل ثانٍ لنفس الملف يستخدم
        # ``-metadata rotate=90`` — وهو **مهمل ولا يكتب شيئًا** في ffmpeg
        # الحديث (6.x). النتيجة كانت عطلين صامتين: مدخل مكرّر لنفس الملف
        # في manifest.json (فتُعالَج الحالة مرتين وتختل الأعداد)، وملف
        # بلا بيانات دوران إن توقّف التوليد قبل القسم 2ب — فيفشل اختبار
        # الدوران برسالة غامضة.

        ("vfr.mp4", "معدل إطارات متغيّر (VFR)",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "select='not(mod(n,3))',setpts=N/(12*TB)", "-fps_mode", "vfr",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("tiny_2s.mp4", "قصير جدًا — ثانيتان بلا أي قطع",
         ["-i", str(raw), "-t", "2", *tone, "-map", "0:v", "-map", "1:a",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 1, "expect_audio": True}),

        ("static_single_slide.mp4", "شريحة واحدة ثابتة — لا قطعات مطلقًا",
         ["-f", "lavfi", "-i", "color=c=0xeeeeee:s=640x360:d=15", *tone,
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-r", "25", "-shortest"],
         {"expect_scenes": 1, "expect_audio": True}),

        ("dark.mp4", "معتم جدًا — يفشل فحص السطوع",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "eq=brightness=-0.62", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("grayscale.mp4", "أحادي اللون — الهيستوجرام الملوّن عديم الفائدة",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "hue=s=0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("odd_dims.mp4", "أبعاد فردية 641×361",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "scale=642:362,crop=641:361", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("high_fps.mp4", "60 إطارًا/ثانية",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a", "-r", "60",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("low_fps.mp4", "5 إطارات/ثانية (أقل من معدل التحليل)",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a", "-r", "5",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("uhd_4k.mp4", "دقة 4K — ضغط الذاكرة",
         ["-i", str(raw), "-t", "8", *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "scale=3840:2160", "-c:v", "libx264", "-preset", "ultrafast",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 2, "expect_audio": True}),

        ("stereo_48k.mp4", "صوت ستيريو 48kHz",
         ["-i", str(raw), "-f", "lavfi", "-i",
          "sine=frequency=330:duration=20", "-map", "0:v", "-map", "1:a",
          "-af", "aformat=channel_layouts=stereo,aresample=48000",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),

        ("gradual_fade.mp4", "انتقالات تلاشٍ تدريجي لا قطعات حادة",
         ["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
          "-vf", "fade=t=in:st=0:d=2,fade=t=out:st=18:d=2",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
         {"expect_scenes": 5, "expect_audio": True}),
    ]

    for name, desc, args, expect in edge:
        path = OUT / name
        if run([*args, str(path)]) and path.exists():
            manifest.append({"file": name, "desc": desc,
                             "category": "edge_case", **expect})
            print(f"  ✓ {name}")

    # --- 2ب. الدوران: يُكتب عبر مصفوفة العرض، وهي الطريقة التي
    #        تستخدمها الهواتف فعليًا (وسم rotate= مهمل في ffmpeg الحديث) ---
    reference = OUT / "h264_mp4.mp4"
    if reference.exists():
        for angle in (90, 180):
            name = f"rotated_{angle}.mp4"
            path = OUT / name
            ok = run(["-display_rotation", str(angle), "-i", str(reference),
                      "-c", "copy", str(path)])
            if ok and path.exists():
                manifest.append({
                    "file": name,
                    "desc": f"دوران {angle}° في مصفوفة العرض (فيديو هاتف)",
                    "category": "edge_case", "expect_scenes": 5,
                    "expect_audio": True})
                print(f"  ✓ {name}")

    # --- 3. ملفات تالفة / غير صالحة (يجب أن تفشل بأمان) ---
    (OUT / "corrupt.mp4").write_bytes(b"\x00\x01\x02not a video at all" * 500)
    manifest.append({"file": "corrupt.mp4", "desc": "ملف تالف — يجب الفشل برسالة واضحة",
                     "category": "must_fail", "expect_failure": True})
    (OUT / "empty.mp4").write_bytes(b"")
    manifest.append({"file": "empty.mp4", "desc": "ملف فارغ (0 بايت)",
                     "category": "must_fail", "expect_failure": True})
    (OUT / "audio_only.m4a")
    if run(["-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-c:a", "aac", str(OUT / "audio_only.m4a")]):
        manifest.append({"file": "audio_only.m4a", "desc": "صوت فقط بلا مسار فيديو",
                         "category": "audio", "expect_failure": False})

    # --- 4. أسماء ملفات عربية وبمسافات ---
    arabic = OUT / "محاضرة الذكاء الاصطناعي 01.mp4"
    if run(["-i", str(raw), *tone, "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(arabic)]):
        manifest.append({"file": arabic.name,
                         "desc": "اسم ملف عربي بمسافات وأرقام",
                         "category": "edge_case", "expect_scenes": 5,
                         "expect_audio": True})
        print(f"  ✓ {arabic.name}")

    raw.unlink(missing_ok=True)
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nتم توليد {len(manifest)} ملف اختبار في {OUT}")
    return manifest


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from utils.console import enable_utf8_console
    enable_utf8_console()
    sys.exit(0 if build() else 1)
