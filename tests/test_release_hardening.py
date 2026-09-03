from pathlib import Path
import zipfile

from core.exceptions import MediaValidationError, AppBaseException, QualityGateError
from utils.error_reporting import describe_error, format_error_for_user
from document.word_generator import DocumentGenerator
from config.settings import DocumentConfig
from config.schemas import AudioSegment, KeyframeMetadata, TranscriptionResult, VideoMetadata
from document.planner import TimelinePlanner
from PIL import Image


def _fixture(tmp_path):
    images = tmp_path / "images"; images.mkdir()
    name = "000001_00-00-01-000.png"
    Image.new("RGB", (800, 450), (200, 200, 200)).save(images / name)
    tr = TranscriptionResult(language="ar", full_text_raw="", full_text_clean="",
        segments=[AudioSegment(id=1,start=1,end=3,text_raw="اختبار عربي",text_clean="اختبار عربي")], words=[], engine={"name":"test"})
    kf = [KeyframeMetadata(image_id=1,timestamp=1,filename=name,scene_id=1,change_score=.4,width=800,height=450,selection_reason="test")]
    meta = VideoMetadata(filename="test.mp4",path=Path("test.mp4"),duration_seconds=4,width=800,height=450,fps=25,codec="h264",has_audio=True,audio_sample_rate=16000,stored_width=800,stored_height=450)
    return images, TimelinePlanner().build(tr,kf,"test.mp4"), meta


def test_docx_publish_is_atomic_and_valid(tmp_path):
    images, plan, meta = _fixture(tmp_path)
    target = tmp_path / "out.docx"
    DocumentGenerator(DocumentConfig()).generate(plan, meta, images, target)
    assert target.exists()
    assert not list(tmp_path.glob("*.docx.tmp"))
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        assert "[Content_Types].xml" in z.namelist()


def test_error_taxonomy_is_machine_readable():
    exc = MediaValidationError("bad media")
    assert isinstance(exc, AppBaseException)
    assert exc.error_code == "E3001"
    assert exc.category == "MEDIA_ERROR"


def test_quality_gate_error_is_both_app_error_and_assertion_error():
    """يجب أن تشارك QualityGateError في نظام error_code/category، مع بقاء
    التوافق الرجعي مع أي كود يلتقط AssertionError (راجع test_quality_gate.py)."""
    exc = QualityGateError("الدرجة منخفضة")
    assert isinstance(exc, AppBaseException)
    assert isinstance(exc, AssertionError)
    assert exc.error_code == "E8002"
    assert exc.category == "DOCUMENT_ERROR"
    info = describe_error(exc)
    assert info["code"] == "E8002"
    assert info["message"] == "الدرجة منخفضة"


def test_format_error_for_user_prefixes_code_for_app_errors_only():
    """رسالة أخطاء التطبيق تُعرض برمزها؛ الأخطاء العامة تبقى كما كانت
    (نص الاستثناء وحده) بلا فقدان تفاصيلها خلف رسالة داخلية عامة."""
    app_exc = MediaValidationError("الملف غير موجود: x.mp4")
    assert format_error_for_user(app_exc) == "[E3001] الملف غير موجود: x.mp4"

    generic_exc = KeyError("missing")
    assert format_error_for_user(generic_exc) == str(generic_exc)
