"""معايرة عتبات كشف المشاهد على مادتك أنت.

    python tools/calibrate_scenes.py محاضرة.mp4 [ملف آخر …]
    python tools/calibrate_scenes.py مجلد_الفيديوهات/ --apply

لماذا هذه الأداة إلزامية (ADR-011): لا يوجد رقم عتبة صالح لكل محتوى.
تسجيل شاشة لشرائح ثابتة توزيع درجاته مختلف تمامًا عن محاضرة مصوَّرة
بكاميرا فيها حركة مستمرة. لذلك العتبة تُشتق من توزيع درجات كل فيديو
(``median + k·MAD``)، ويبقى ``k`` وحده هو ما يحتاج معايرة.

ما تفعله الأداة: تشغّل مرحلة التحليل فقط (بلا استخراج صور ولا تفريغ)،
وتعرض توزيع الدرجات، وعدد المشاهد الناتج عند قيم ``k`` مختلفة، ثم توصي
بقيمة تقع في «المنطقة المستقرة» — النطاق الذي لا يتغيّر فيه عدد المشاهد
كثيرًا، وهو أقوى مؤشر على أن العتبة تفصل القطعات الحقيقية عن الضجيج.

‏``--apply`` يكتب القيمة الموصى بها في ملف إعدادك.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".ts",
                  ".m4v", ".mpg", ".mpeg", ".3gp", ".flv"}

# نطاق البحث عن k. القيمة الافتراضية 6.0 وسط هذا النطاق.
K_VALUES = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 14.0)


def _collect(paths: List[Path]) -> List[Path]:
    videos: List[Path] = []
    for path in paths:
        if path.is_dir():
            videos.extend(sorted(p for p in path.iterdir()
                                 if p.suffix.lower() in VIDEO_SUFFIXES))
        elif path.suffix.lower() in VIDEO_SUFFIXES:
            videos.append(path)
    return videos


def _scores_for(video: Path, detector, ffmpeg) -> tuple[List[float], float]:
    """يعيد (درجات العيّنات، مدة الفيديو) بتشغيل مرحلة التحليل وحدها."""
    from utils.frames import resolve_rotation_plan
    from utils.media_probe import extract_video_facts, probe_raw

    facts = extract_video_facts(probe_raw(video))
    plan = resolve_rotation_plan(
        video, facts["rotation"],
        facts["stored_width"], facts["stored_height"],
        facts["width"], facts["height"], ffmpeg=ffmpeg)

    # نلتقط العيّنات من الكاشف نفسه بدل إعادة كتابة حلقة القراءة:
    # أي تحسين لاحق في الكاشف ينعكس على المعايرة تلقائيًا.
    captured: List[float] = []
    original = detector._build_scenes

    def spy(samples, duration):
        captured.extend(s.score for s in samples)
        return original(samples, duration)

    detector._build_scenes = spy          # type: ignore[method-assign]
    try:
        detector.detect(video, fps_hint=facts["fps"],
                        duration_hint=facts["duration_seconds"],
                        rotation_plan=plan)
    finally:
        detector._build_scenes = original  # type: ignore[method-assign]
    return captured, facts["duration_seconds"]


def _threshold(scores: List[float], k: float, floor: float) -> float:
    if len(scores) < 4:
        return floor
    median = statistics.median(scores)
    mad = statistics.median([abs(s - median) for s in scores]) * 1.4826
    return max(floor, median + k * mad)


def _scene_count(scores: List[float], threshold: float,
                 min_gap: float, analysis_fps: float) -> int:
    """عدد القطعات عند عتبة معيّنة، بتطبيق قاعدة الفجوة الزمنية نفسها."""
    step = 1.0 / max(analysis_fps, 0.1)
    count = 0
    last = -1e9
    for index, score in enumerate(scores):
        moment = index * step
        if score >= threshold and moment - last >= min_gap:
            count += 1
            last = moment
    return count + 1        # المشهد الأول قبل أي قطع


def _recommend(counts: dict[float, int], duration: float) -> Optional[float]:
    """أفضل k: أعرض هضبة مستقرة يقع عددها في نطاق معقول.

    «معقول» = صورة كل 20–120 ثانية تقريبًا. أقل من ذلك مستند بلا صور،
    وأكثر منه مستند لا يُقرأ.
    """
    low = max(2, int(duration / 120))
    high = max(low + 1, int(duration / 20))
    plateaus: list[tuple[int, float]] = []
    ordered = sorted(counts)
    run_start, run_len = ordered[0], 1
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if counts[current] == counts[previous]:
            run_len += 1
        else:
            plateaus.append((run_len, run_start))
            run_start, run_len = current, 1
    plateaus.append((run_len, run_start))

    valid = [(length, k) for length, k in plateaus if low <= counts[k] <= high]
    if not valid:
        return None
    length, k = max(valid)
    return k


def main() -> int:
    parser = argparse.ArgumentParser(
        description="معايرة عتبة كشف المشاهد على مادتك (ADR-011)")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="ملفات فيديو أو مجلدات")
    parser.add_argument("--apply", action="store_true",
                        help="اكتب القيمة الموصى بها في ملف الإعداد")
    args = parser.parse_args()

    videos = _collect(args.paths)
    if not videos:
        print("لا توجد ملفات فيديو في المسارات المعطاة.", file=sys.stderr)
        return 2

    from config.settings import AppConfig, default_config_path
    from utils.ffmpeg_service import FFmpegService
    from video.scene_detector import SceneDetector

    config = AppConfig.load(default_config_path())
    scene_config = config.scene_detection
    ffmpeg = FFmpegService()
    detector = SceneDetector(scene_config)

    all_scores: List[float] = []
    per_video_best: List[float] = []

    for video in videos:
        print(f"\n▸ {video.name}")
        try:
            scores, duration = _scores_for(video, detector, ffmpeg)
        except Exception as exc:
            print(f"   تعذّر التحليل: {exc}")
            continue
        if len(scores) < 4:
            print("   قصير جدًا للمعايرة (أقل من 4 عيّنات).")
            continue

        all_scores.extend(scores)
        median = statistics.median(scores)
        mad = statistics.median([abs(s - median) for s in scores]) * 1.4826
        print(f"   عيّنات: {len(scores)} · المدة: {duration:.0f}s")
        print(f"   الوسيط: {median:.4f} · MAD: {mad:.4f} · "
              f"الأعلى: {max(scores):.4f}")

        counts = {}
        for k in K_VALUES:
            threshold = _threshold(scores, k, scene_config.absolute_floor)
            counts[k] = _scene_count(scores, threshold,
                                     scene_config.minimum_gap_seconds,
                                     scene_config.analysis_fps)
        print("   " + "  ".join(f"k={k:<4g}→{counts[k]:>3d}" for k in K_VALUES))

        best = _recommend(counts, duration)
        if best is not None:
            per_video_best.append(best)
            print(f"   الموصى به لهذا الملف: k = {best:g} "
                  f"({counts[best]} مشهدًا)")
        else:
            print("   لا توجد قيمة مريحة — راجع الأرقام يدويًا.")

    if not per_video_best:
        print("\nلم تُستنتج توصية. جرّب عيّنات أطول أو أكثر تنوّعًا.")
        return 1

    recommended = statistics.median(per_video_best)
    print(f"\n{'=' * 58}")
    print(f"التوصية على {len(per_video_best)} ملف:  adaptive_k = "
          f"{recommended:g}   (الحالي: {scene_config.adaptive_k:g})")
    print("=" * 58)

    if args.apply:
        config.scene_detection.adaptive_k = float(recommended)
        path = default_config_path()
        config.save(path)
        print(f"حُفظت في: {path}")
    else:
        print("لتطبيقها:  أضف --apply  أو اضبط يدويًا في ملف الإعداد:")
        print("  scene_detection:")
        print(f"    adaptive_k: {recommended:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
