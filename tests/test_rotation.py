"""اتجاه الصور المستخرجة — يُقاس مقابل FFmpeg كمرجع حقيقة.

مصيدة حقيقية: بعض بُنى OpenCV تطبّق دوران الحاوية وبعضها يتجاهله.
افتراض أيّ منهما ينتج صورًا مقلوبة على نصف الأجهزة، والـ XML يبقى سليمًا
تمامًا فلا يكشف الاختبارُ البنيويُّ العطلَ. المقارنة بالبكسل هي الفحص
الوحيد الذي يمسك هذا.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from config.settings import AppConfig
from core.pipeline import VideoToDocPipeline
from tests.test_corpus_matrix import StubModelManager, StubTranscriber
from utils.ffmpeg_service import FFmpegService
from utils.frames import RotationPlan, apply_rotation, resolve_rotation_plan
from utils.media_probe import extract_video_facts, probe_raw

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
ROTATED = ["rotated_90.mp4", "rotated_180.mp4", "h264_mp4.mp4", "portrait.mp4"]


def ffmpeg_reference(video: Path, timestamp: float, out: Path) -> np.ndarray:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{timestamp}",
                    "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return cv2.imread(str(out))


@pytest.mark.parametrize("name", ROTATED)
def test_resolved_frame_matches_ffmpeg_pixelwise(tmp_path, name):
    video = CORPUS / name
    if not video.exists():
        pytest.skip(f"{name} غير موجود")
    facts = extract_video_facts(probe_raw(video))
    plan = resolve_rotation_plan(
        video, facts["rotation"], facts["stored_width"], facts["stored_height"],
        facts["width"], facts["height"], ffmpeg=FFmpegService())

    timestamp = 2.0
    reference = ffmpeg_reference(video, timestamp, tmp_path / "ref.png")
    capture = cv2.VideoCapture(str(video))
    capture.set(cv2.CAP_PROP_POS_FRAMES, int(timestamp * facts["fps"]))
    ok, raw = capture.read()
    capture.release()
    assert ok, "تعذر قراءة الإطار"

    corrected = plan.apply(raw)
    assert corrected.shape == reference.shape, (
        f"{name}: أبعاد مختلفة {corrected.shape} مقابل {reference.shape} "
        f"(خطة={plan.correction}°, {plan.reason})")
    diff = float(np.mean(np.abs(corrected.astype(np.int16)
                                - reference.astype(np.int16))))
    assert diff < 12.0, (
        f"{name}: الصورة لا تطابق مرجع FFmpeg (فرق={diff:.1f}, "
        f"خطة={plan.correction}°, {plan.reason})")


@pytest.mark.parametrize("name", ROTATED)
def test_extracted_keyframes_match_ffmpeg_orientation(tmp_path, name):
    """الفحص النهائي: الصور المحفوظة فعلًا في المستند بالاتجاه الصحيح."""
    video = CORPUS / name
    if not video.exists():
        pytest.skip(f"{name} غير موجود")
    pipeline = VideoToDocPipeline(tmp_path / "out", AppConfig(),
                                  transcriber=StubTranscriber(),
                                  model_manager=StubModelManager())
    pipeline.run(video)
    job_dir = pipeline.job_dir_for(video)

    import json
    keyframes = json.loads(
        (job_dir / "keyframes.json").read_text(encoding="utf-8"))["keyframes"]
    assert keyframes, "لا توجد صور"

    kf = keyframes[0]
    saved = cv2.imread(str(job_dir / "keyframes" / kf["filename"]))
    reference = ffmpeg_reference(video, kf["timestamp"], tmp_path / "ref2.png")
    if saved.shape != reference.shape:
        reference = cv2.resize(reference, (saved.shape[1], saved.shape[0]))
    diff = float(np.mean(np.abs(saved.astype(np.int16)
                                - reference.astype(np.int16))))
    assert diff < 20.0, f"{name}: الصورة المحفوظة باتجاه خاطئ (فرق={diff:.1f})"


def test_rotation_plan_is_explicit_about_its_reason():
    for name in ROTATED:
        video = CORPUS / name
        if not video.exists():
            continue
        facts = extract_video_facts(probe_raw(video))
        plan = resolve_rotation_plan(
            video, facts["rotation"], facts["stored_width"],
            facts["stored_height"], facts["width"], facts["height"],
            ffmpeg=FFmpegService())
        assert plan.reason, "كل خطة يجب أن تشرح سبب قرارها"
        assert plan.correction in (0, 90, 180, 270)


def test_apply_rotation_is_exact():
    frame = np.zeros((4, 6, 3), np.uint8)
    frame[0, 0] = 255
    assert apply_rotation(frame, 0).shape == (4, 6, 3)
    assert apply_rotation(frame, 90).shape == (6, 4, 3)
    assert apply_rotation(frame, 180).shape == (4, 6, 3)
    assert apply_rotation(frame, 270).shape == (6, 4, 3)
    # دورتان بـ 180 تعيدان الأصل
    assert np.array_equal(apply_rotation(apply_rotation(frame, 180), 180), frame)


def test_no_rotation_metadata_means_no_correction():
    plan = resolve_rotation_plan(CORPUS / "h264_mp4.mp4", 0, 640, 360, 640, 360)
    assert plan.correction == 0 and plan.reason == "no_rotation_metadata"
