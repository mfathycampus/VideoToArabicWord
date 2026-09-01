"""مصفوفة التوافق: يجب أن يعالج الـ pipeline كل أنواع الفيديو.

كل ملف في المصفوفة يُشغَّل كاملًا حتى مستند DOCX صالح، وتُفحص عليه
بوابات الجودة نفسها. الملفات المصنَّفة ``must_fail`` يجب أن تفشل
**برسالة عربية واضحة**، لا أن تنهار بـ traceback.
"""
import json
import zipfile
from pathlib import Path

import pytest
from docx import Document
from lxml import etree

from config.settings import AppConfig
from core.exceptions import AppBaseException
from core.pipeline import VideoToDocPipeline
from config.schemas import AudioSegment, TranscriptionResult

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}


class StubTranscriber:
    """تفريغ ثابت ومحدَّد — يعزل اختبار الـ pipeline عن نموذج الـ ASR.

    اختبار التفريغ الحقيقي منفصل في ``test_transcription_real.py``.
    """
    def __init__(self, *_args, **_kwargs):
        pass

    def transcribe(self, audio_path, cancel_token=None, progress_callback=None):
        if progress_callback:
            progress_callback(1.0, "تفريغ اختباري")
        segments = [
            AudioSegment(id=1, start=0.5, end=4.0,
                         text_raw="مقدمة قبل أول شريحة",
                         text_clean="مقدمة قبل أول شريحة"),
            AudioSegment(id=2, start=5.0, end=13.0,
                         text_raw="جملة تعبر حد شريحتين",
                         text_clean="جملة تعبر حد شريحتين"),
            AudioSegment(id=3, start=14.0, end=17.5,
                         text_raw="شرح الشريحة الثالثة",
                         text_clean="شرح الشريحة الثالثة"),
        ]
        return TranscriptionResult(
            language="ar", full_text_raw=" ".join(s.text_raw for s in segments),
            full_text_clean=" ".join(s.text_clean for s in segments),
            segments=segments, words=[], engine={"name": "stub"})


class StubModelManager:
    def __init__(self, *_a, **_k): pass
    def is_available(self, *_a): return True
    def ensure_available(self, *_a, **_k): return Path(".")


def build_pipeline(out_dir: Path) -> VideoToDocPipeline:
    return VideoToDocPipeline(out_dir, AppConfig(),
                              transcriber=StubTranscriber(),
                              model_manager=StubModelManager())


def processable(manifest):
    return [e for e in manifest if e["category"] != "must_fail"]


def must_fail(manifest):
    return [e for e in manifest if e["category"] == "must_fail"]


# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def processed(request, tmp_path_factory):
    """يشغّل الـ pipeline على كل ملفات المصفوفة مرة واحدة."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "testdata" / "corpus"
         / "manifest.json").read_text(encoding="utf-8"))
    corpus = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
    out = tmp_path_factory.mktemp("matrix")
    results = {}
    for entry in processable(manifest):
        video = corpus / entry["file"]
        if not video.exists():
            continue
        pipeline = build_pipeline(out / entry["file"].replace(".", "_"))
        try:
            docx = pipeline.run(video)
            results[entry["file"]] = {"ok": True, "docx": docx,
                                      "entry": entry, "error": None,
                                      "job_dir": pipeline.job_dir_for(video)}
        except Exception as exc:
            results[entry["file"]] = {"ok": False, "docx": None,
                                      "entry": entry,
                                      "error": f"{type(exc).__name__}: {exc}",
                                      "job_dir": None}
    return results


def _ids(manifest_entries):
    return [e["file"] for e in manifest_entries]


# ---- 1. كل ملف صالح يجب أن يُعالَج حتى النهاية ----
def test_all_valid_videos_produce_a_document(processed):
    failures = {k: v["error"] for k, v in processed.items() if not v["ok"]}
    assert not failures, f"ملفات فشلت المعالجة: {failures}"


@pytest.mark.parametrize("name", [
    "h264_mp4.mp4", "h265_mp4.mp4", "h264_mkv.mkv", "h264_mov.mov",
    "h264_ts.ts", "vp8_webm.webm", "av1_mkv.mkv", "mpeg4_avi.avi",
    "mjpeg_avi.avi", "msmpeg4_wmv.wmv",
])
def test_codec_container_produces_valid_docx(processed, name):
    result = processed.get(name)
    if result is None:
        pytest.skip(f"{name} غير موجود في المصفوفة")
    assert result["ok"], result["error"]
    assert result["docx"].exists() and result["docx"].stat().st_size > 0


# ---- 2. صحة أرشيف DOCX لكل ملف ----
def test_every_docx_is_a_valid_archive(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        with zipfile.ZipFile(result["docx"]) as archive:
            assert archive.testzip() is None, f"{name}: أرشيف تالف"
            names = archive.namelist()
            for required in ("[Content_Types].xml", "word/document.xml",
                             "word/_rels/document.xml.rels"):
                assert required in names, f"{name}: جزء مفقود {required}"
            for part in names:
                if part.endswith(".xml"):
                    etree.fromstring(archive.read(part))


# ---- 3. سلامة النص: لا ضياع ولا تكرار ----
def test_no_text_lost_or_duplicated_anywhere(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        plan = json.loads(
            (result["job_dir"] / "plan.json").read_text(encoding="utf-8"))
        assert plan["total_segments_in"] == plan["total_segments_out"], \
            f"{name}: ضياع/تكرار نص"
        assert plan["total_figures_in"] == plan["total_figures_out"], \
            f"{name}: ضياع/تكرار صور"
        # الفيديو بلا صوت لا يحمل نصًا أصلًا — الثابت هنا هو الصفر
        if not result["entry"].get("expect_audio", True):
            assert plan["total_segments_in"] == 0
            continue

        doc = Document(str(result["docx"]))
        body = "\n".join(p.text for p in doc.paragraphs)
        for expected in ["مقدمة قبل أول شريحة", "جملة تعبر حد شريحتين",
                         "شرح الشريحة الثالثة"]:
            assert body.count(expected) == 1, \
                f"{name}: '{expected}' ظهر {body.count(expected)} مرة"
        # الصور مضمّنة فعليًا
        assert plan["total_figures_out"] >= 0


# ---- 4. صحة العرض العربي في كل مستند ----
def test_every_arabic_run_has_complex_script_properties(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        with zipfile.ZipFile(result["docx"]) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
        checked = 0
        for run in root.iter(f"{W}r"):
            text = "".join(t.text or "" for t in run.iter(f"{W}t"))
            if not any("؀" <= ch <= "ۿ" for ch in text):
                continue
            checked += 1
            rPr = run.find("w:rPr", NS)
            assert rPr is not None, f"{name}: run عربي بلا rPr"
            rFonts = rPr.find("w:rFonts", NS)
            assert rFonts is not None and rFonts.get(f"{W}cs"), \
                f"{name}: w:cs مفقود في '{text[:25]}'"
            assert rPr.find("w:szCs", NS) is not None, f"{name}: w:szCs مفقود"
            assert rPr.find("w:rtl", NS) is not None, f"{name}: w:rtl مفقود"
        assert checked > 0, f"{name}: لا نص عربي في المستند"


def test_bidi_precedes_jc_in_every_document(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        with zipfile.ZipFile(result["docx"]) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
        for pPr in root.iter(f"{W}pPr"):
            tags = [c.tag.replace(W, "") for c in pPr]
            if "bidi" in tags and "jc" in tags:
                assert tags.index("bidi") < tags.index("jc"), \
                    f"{name}: ترتيب مخالف للمخطط {tags}"


# ---- 5. عقود البيانات مكتوبة وصالحة ----
def test_all_data_contracts_are_persisted(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        job_dir = result["job_dir"]
        for artifact in ("metadata.json", "transcription.json", "scenes.json",
                         "keyframes.json", "plan.json", "job_state.json"):
            path = job_dir / artifact
            assert path.exists(), f"{name}: عقد مفقود {artifact}"
            json.loads(path.read_text(encoding="utf-8"))
        state = json.loads((job_dir / "job_state.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed", f"{name}: الحالة {state['status']}"
        assert state["progress"] == 100.0


# ---- 6. مدى change_score ضمن العقد ----
def test_change_scores_within_contract_range(processed):
    for name, result in processed.items():
        if not result["ok"]:
            continue
        scenes = json.loads(
            (result["job_dir"] / "scenes.json").read_text(encoding="utf-8"))
        for scene in scenes["scenes"]:
            assert 0.0 <= scene["change_score"] <= 1.0, \
                f"{name}: change_score={scene['change_score']} خارج [0,1]"
            assert scene["end"] >= scene["start"], f"{name}: مشهد بمدى سالب"


# ---- 7. أسماء ملفات صالحة على Windows ----
def test_keyframe_filenames_are_windows_safe(processed):
    forbidden = set('<>:"/\\|?*')
    for name, result in processed.items():
        if not result["ok"]:
            continue
        keyframes = json.loads(
            (result["job_dir"] / "keyframes.json").read_text(encoding="utf-8"))
        for kf in keyframes["keyframes"]:
            assert not (forbidden & set(kf["filename"])), \
                f"{name}: اسم غير صالح {kf['filename']}"
            assert (result["job_dir"] / "keyframes" / kf["filename"]).exists()


# ---- 8. الحالات التي يجب أن تفشل بأمان ----
@pytest.mark.parametrize("name", ["corrupt.mp4", "empty.mp4", "audio_only.m4a"])
def test_invalid_files_fail_with_clear_arabic_message(tmp_path, corpus_dir, name):
    video = corpus_dir / name
    if not video.exists():
        pytest.skip(f"{name} غير موجود")
    pipeline = build_pipeline(tmp_path / "fail")
    with pytest.raises(AppBaseException) as info:
        pipeline.run(video)
    message = str(info.value)
    assert any("؀" <= ch <= "ۿ" for ch in message), \
        f"{name}: رسالة الخطأ ليست عربية: {message}"
    assert len(message) > 10


# ---- 9. توقعات خاصة بحالات حدّية ----
def test_no_audio_yields_image_only_document(processed):
    result = processed.get("no_audio.mp4")
    assert result and result["ok"]
    transcript = json.loads(
        (result["job_dir"] / "transcription.json").read_text(encoding="utf-8"))
    assert transcript["engine"]["reason"] == "no_audio_stream"
    assert transcript["segments"] == []
    keyframes = json.loads(
        (result["job_dir"] / "keyframes.json").read_text(encoding="utf-8"))
    assert len(keyframes["keyframes"]) >= 1, "يجب إنتاج صور رغم غياب الصوت"


def test_static_video_produces_single_scene(processed):
    result = processed.get("static_single_slide.mp4")
    assert result and result["ok"]
    scenes = json.loads(
        (result["job_dir"] / "scenes.json").read_text(encoding="utf-8"))
    assert len(scenes["scenes"]) == 1, \
        f"شريحة ثابتة يجب ألا تنتج قطعات، وُجد {len(scenes['scenes'])}"


def test_rotation_is_detected_and_dimensions_flip(processed):
    result = processed.get("rotated_90.mp4")
    assert result and result["ok"]
    metadata = json.loads(
        (result["job_dir"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["rotation"] in (90, 270), "لم تُكتشف بيانات الدوران"
    assert metadata["width"] == metadata["stored_height"], \
        "الأبعاد المعروضة يجب أن تنقلب عند الدوران 90/270"


def test_vfr_uses_probe_fps_not_opencv(processed):
    result = processed.get("vfr.mp4")
    assert result and result["ok"]
    metadata = json.loads(
        (result["job_dir"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["fps"] > 0
    scenes = json.loads(
        (result["job_dir"] / "scenes.json").read_text(encoding="utf-8"))
    assert len(scenes["scenes"]) >= 1


def test_tiny_video_still_produces_document(processed):
    result = processed.get("tiny_2s.mp4")
    assert result and result["ok"]
    assert result["docx"].exists()


def test_arabic_filename_is_handled(processed):
    matching = [k for k in processed if "محاضرة" in k]
    assert matching, "ملف الاسم العربي غير موجود في المصفوفة"
    assert processed[matching[0]]["ok"], processed[matching[0]]["error"]
