"""يقيس زمن المرحلتين البصريّتين — كشف المشاهد واختيار اللقطات.

    python tools/bench_visual.py --video lecture.mp4

سبب وجود هذا الملف: على تسجيل 88 ثانية كانت المرحلتان 22 ثانية من أصل
‏187 — أي 12٪، ومن السهل تجاهلهما أمام 85٪ التفريغ. لكنّ سلوكهما مع
طول الفيديو **كان غير مقيس**، والاثنتان تقرآن الفيديو من أوّله:

* كشف المشاهد يمرّ تمريرة تسلسلية واحدة (``grab`` سريع، و``retrieve``
  كل ثلث ثانية).
* اختيار اللقطات يفتح الفيديو **من جديد** ويقفز قفزًا عشوائيًا إلى
  خمس لحظات في كل مشهد — وكل قفزة تُجبر المفكِّك على البدء من أقرب
  إطار مفتاحي قبلها.

ما أخرجه على محاضرة حقيقية مدّتها 8:15 (‏1280×720):

    كشف المشاهد   33.4 ث   73 مشهدًا        RTF 0.067
    اللقطات       39.4 ث   26 صورة من 365 مرشّحًا   RTF 0.079
    المجموع       72.8 ث                    RTF 0.147

**والخلاصة أن الدمج لا يستحقّ الثمن، وهذا قياسٌ لا رأي.** المرحلتان
معًا 15٪ من زمن التشغيل. وأفضل ما يعطيه الدمج — نقل التقييم إلى
التمريرة التي تفكّ الإطارات أصلًا، ثم استخراج الفائزين وحدهم — يوفّر
نحو 40٪ منها، أي **6٪ من الزمن الكلّي**. وثمنه أن تُقيَّم حدّة الصورة
على نسخة بعرض 480 بكسل بدل الأصل، وحدّة الصورة تتبع المقياس. أي
مخاطرةٌ في **الجزء الذي يراه المستخدم بعينه** مقابل ستّة بالمئة.

بقي هذا الملفّ ليُعاد القياس عليه إن تغيّر شيء — لا ليُبنى عليه دمج.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import AppConfig  # noqa: E402
from utils.frames import resolve_rotation_plan  # noqa: E402
from utils.media_probe import extract_video_facts, probe_raw  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--images", type=Path,
                        default=Path("/tmp/bench_frames"))
    args = parser.parse_args()

    from video.keyframe_selector import KeyframeSelector
    from video.scene_detector import SceneDetector

    config = AppConfig()
    facts = extract_video_facts(probe_raw(args.video))
    duration = facts["duration_seconds"]
    print(f"الفيديو {duration:.0f} ثانية، {facts['width']}×{facts['height']}")

    plan = resolve_rotation_plan(
        args.video, facts["rotation"],
        facts["stored_width"] or facts["width"],
        facts["stored_height"] or facts["height"],
        facts["width"], facts["height"])

    started = time.monotonic()
    scenes = SceneDetector(config.scene_detection).detect(
        args.video, fps_hint=facts["fps"], duration_hint=duration,
        rotation_plan=plan)
    scene_seconds = time.monotonic() - started
    print(f"كشف المشاهد: {scene_seconds:.1f} ث — {len(scenes)} مشهدًا "
          f"(‏{scene_seconds / duration:.3f} من زمن الفيديو)")

    args.images.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    keyframes = KeyframeSelector(config.frames).extract(
        args.video, scenes, args.images, fps_hint=facts["fps"],
        rotation_plan=plan)
    frame_seconds = time.monotonic() - started
    candidates = len(scenes) * config.frames.candidates_per_scene
    print(f"اللقطات: {frame_seconds:.1f} ث — {len(keyframes)} صورة من "
          f"~{candidates} مرشّحًا "
          f"(‏{frame_seconds / duration:.3f} من زمن الفيديو)")
    if candidates:
        print(f"كلفة المرشّح الواحد: "
              f"{frame_seconds / candidates * 1000:.0f} ms")

    total = scene_seconds + frame_seconds
    print(f"\nالمجموع البصري {total:.1f} ث = {total / duration:.3f} من زمن "
          f"الفيديو. على محاضرة ثلاث ساعات: "
          f"~{total / duration * 3 * 60:.0f} دقيقة.")
    return 0


if __name__ == "__main__":
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
