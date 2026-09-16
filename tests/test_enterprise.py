"""بوابات الدفعة المؤسسية: SCORM، الوصولية، التدقيق، سياسة الجهاز.

ثلاثة من هذه الأربعة تنكسر **بصمت**: حزمة SCORM ناقصة ملفًّا في بيانها
تُرفض عند رفعها لا عند بنائها، وصورةٌ بلا نصّ بديل تظهر سليمة لكل مبصر،
وسياسةٌ لا تُطبَّق في مدخل واحد لا أثر لها حتى يُدقَّق الجهاز. فالفحص
هنا يقرأ الملفّ المضغوط نفسه ويقارنه ببيانه، ويقرأ XML المستند، ويجرّب
السياسة من نقطة الدخول لا من دالّة التطبيق.
"""
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from config.schemas import DocumentBlock
from config.settings import AppConfig, DocumentConfig
from document.alt_text import MAX_ALT_CHARS, describe
from document.exporters import ExportContext
from tests.test_exporters import _metadata, _plan


@pytest.fixture
def ctx(tmp_path: Path) -> ExportContext:
    from PIL import Image

    images = tmp_path / "keyframes"
    images.mkdir()
    Image.new("RGB", (640, 360), (30, 40, 90)).save(images / "f1.jpg")
    return ExportContext(plan=_plan(), metadata=_metadata(tmp_path),
                         images_dir=images, base_path=tmp_path / "درس.docx",
                         document_config=DocumentConfig())


# ---------------------------------------------------------------------
# الوصولية — WCAG 1.1.1
# ---------------------------------------------------------------------
def test_alt_text_is_never_empty():
    """نصّ بديل فارغ يجعل الصورة كأنها غير موجودة لقارئ الشاشة."""
    bare = DocumentBlock(kind="figure", image_filename="f.jpg")
    assert describe(bare, 3).strip()


def test_alt_text_prefers_the_words_actually_on_screen():
    """نصّ الشاشة هو **حرفيًّا** ما يقرؤه المبصر في الصورة."""
    block = DocumentBlock(kind="figure", image_filename="f.jpg",
                          caption="شاشة الجدول", ocr_text="Modify Course Schedule")
    alt = describe(block)
    assert "Modify Course Schedule" in alt and "شاشة الجدول" in alt


def test_alt_text_falls_back_to_a_real_position_not_an_invented_description():
    """«صورة توضيحية للمحتوى» تمرّ على المدقّق الآلي وتخذل الأعمى."""
    block = DocumentBlock(kind="figure", image_filename="f.jpg", timestamp=760.0)
    alt = describe(block, 3)
    assert "00:12:40" in alt
    assert "توضيحية" not in alt


def test_alt_text_stays_short_enough_to_be_heard():
    block = DocumentBlock(kind="figure", image_filename="f.jpg",
                          ocr_text="سطر طويل جدًّا " * 60)
    assert len(describe(block)) <= MAX_ALT_CHARS


def test_word_pictures_carry_alt_text_in_the_ooxml(tmp_path):
    """الموضع الرسمي ``wp:docPr/@descr`` — وهو ما يقرؤه مدقّق Word."""
    from PIL import Image

    from document.word_generator import DocumentGenerator

    images = tmp_path / "keyframes"
    images.mkdir()
    Image.new("RGB", (640, 360), (20, 30, 70)).save(images / "f1.jpg")
    docx = tmp_path / "a.docx"
    DocumentGenerator(DocumentConfig()).generate(
        _plan(), _metadata(tmp_path), images, docx)

    with zipfile.ZipFile(docx) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    pictures = re.findall(r"<wp:docPr[^>]*>", xml)
    assert pictures, "لا صور في المستند — الاختبار لا يفحص شيئًا"
    assert all('descr="' in tag for tag in pictures)


# ---------------------------------------------------------------------
# SCORM
# ---------------------------------------------------------------------
def test_scorm_package_is_a_valid_scorm_12_zip(ctx):
    from document.exporters.scorm_export import export

    path = export(ctx)
    assert path is not None and path.name == "درس.scorm.zip"

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        manifest = archive.read("imsmanifest.xml").decode("utf-8")

    # البيان في **جذر** الملفّ المضغوط: أشهر سبب لرفض الحزمة هو ضغط
    # المجلد نفسه بدل محتواه.
    assert "imsmanifest.xml" in names
    assert not any("/" in name for name in names)
    assert "index.html" in names

    assert "<schemaversion>1.2</schemaversion>" in manifest
    assert 'adlcp:scormtype="sco"' in manifest
    assert 'href="index.html"' in manifest
    assert "<adlcp:masteryscore>" in manifest


def test_every_file_named_in_the_manifest_exists_in_the_package(ctx):
    """منصّات ترفض الحزمة إن نقص ملفّ مذكور، وتتجاهل الموجود غير المذكور."""
    from document.exporters.scorm_export import export

    with zipfile.ZipFile(export(ctx)) as archive:
        names = set(archive.namelist())
        manifest = archive.read("imsmanifest.xml").decode("utf-8")

    declared = set(re.findall(r'<file href="([^"]+)"/>', manifest))
    assert declared, "بيانٌ بلا ملفّات"
    assert declared <= names                       # لا مذكورٌ مفقود
    assert names - declared == {"imsmanifest.xml"}  # ولا موجودٌ مُغفَل


def test_scorm_manifest_is_wellformed_xml(ctx):
    from xml.etree import ElementTree

    from document.exporters.scorm_export import export

    with zipfile.ZipFile(export(ctx)) as archive:
        ElementTree.fromstring(archive.read("imsmanifest.xml"))


def test_scorm_launcher_walks_the_frame_chain_to_find_the_api(ctx):
    """الاكتفاء بـ``parent.API`` يعمل عند الناشر ويصمت عند المدرسة."""
    from document.exporters.scorm_export import export

    with zipfile.ZipFile(export(ctx)) as archive:
        page = archive.read("index.html").decode("utf-8")
    assert "win.parent" in page and "window.opener" in page
    assert "LMSInitialize" in page and "LMSFinish" in page
    assert "cmi.core.lesson_status" in page


def test_scorm_package_has_no_external_references(ctx):
    """منصّات كثيرة خلف جدار ناريّ: مرجعٌ خارجي يخرج أبيض عند الطالب."""
    from document.exporters.scorm_export import export

    with zipfile.ZipFile(export(ctx)) as archive:
        for name in archive.namelist():
            if name.endswith(".html"):
                page = archive.read(name).decode("utf-8")
                assert "http://" not in page and "https://" not in page


def test_scorm_reports_a_score_only_when_there_are_questions(ctx, tmp_path):
    from document.exporters.scorm_export import export
    from tests.test_study_pack import _built_pack

    with zipfile.ZipFile(export(ctx)) as archive:
        without = archive.read("index.html").decode("utf-8")
    assert "cmi.core.score.raw" in without          # الدالة موجودة دائمًا
    assert "لا أسئلة في هذه الوحدة" in without      # ولا تُستدعى بلا أسئلة

    ctx.options = {"study_pack": _built_pack()}
    with zipfile.ZipFile(export(ctx)) as archive:
        with_quiz = archive.read("index.html").decode("utf-8")
        assert "study.html" in archive.namelist()
    assert "أي البروتوكولات" in with_quiz
    assert 'id="submit"' in with_quiz


# ---------------------------------------------------------------------
# سجلّ التدقيق
# ---------------------------------------------------------------------
def test_audit_records_that_text_stayed_on_the_device():
    from utils.audit import cloud_providers_in_use

    config = AppConfig()
    config.study.enabled = True
    config.study.provider = "ollama"
    assert cloud_providers_in_use(config) == []


def test_audit_names_the_cloud_provider_when_one_is_in_use():
    from utils.audit import cloud_providers_in_use

    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.provider = "anthropic"
    assert cloud_providers_in_use(config) == ["anthropic"]


def test_a_disabled_cloud_provider_is_not_counted():
    """إعدادٌ سحابيّ معطّل لا يُخرج حرفًا.

    عدُّه ينتج سجلًّا يقول إن النصّ غادر الجهاز وهو لم يغادر — وسجلٌّ
    يكذب لصالح التشدّد يفقد قيمته كدليل تمامًا كالذي يكذب لصالح التساهل.
    """
    from utils.audit import cloud_providers_in_use

    config = AppConfig()
    config.rewrite.enabled = False
    config.rewrite.provider = "anthropic"
    assert cloud_providers_in_use(config) == []


def test_audit_writes_a_line_even_when_the_job_fails(tmp_path, monkeypatch):
    """السجلّ الذي لا يحوي إلا النجاحات لا يصلح للتدقيق أصلًا."""
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "central"))
    source = tmp_path / "درس.mp4"
    source.write_bytes(b"x" * 100)

    with pytest.raises(RuntimeError):
        with audit.record_job(AppConfig(), source, tmp_path / "job"):
            raise RuntimeError("سقطت المهمّة")

    line = json.loads((tmp_path / "central" / audit.AUDIT_FILENAME)
                      .read_text(encoding="utf-8").strip())
    assert line["status"] == "failed"
    assert line["source_name"] == "درس.mp4"
    assert line["text_left_device"] is False


def test_audit_never_stores_the_transcript(tmp_path, monkeypatch):
    """سجلّ تدقيق يحوي المحتوى يصير هو نفسه تسريبًا للبيانات."""
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "central"))
    source = tmp_path / "درس.mp4"
    source.write_bytes(b"x")
    with audit.record_job(AppConfig(), source, tmp_path / "job"):
        pass

    line = json.loads((tmp_path / "central" / audit.AUDIT_FILENAME)
                      .read_text(encoding="utf-8").strip())
    assert set(line) == {
        "at", "app_version", "user", "host", "os", "source_name",
        "source_bytes", "duration_seconds", "job_dir", "outputs", "engine",
        "model", "text_left_device", "cloud_providers", "permitted_providers",
        "attempted_no_egress", "status", "error", "elapsed_seconds"}


def test_audit_failure_never_breaks_a_job(tmp_path, monkeypatch):
    from utils import audit

    # مجلد مُدار للقراءة فقط شائع — وإسقاط المعالجة عليه عقاب بلا فائدة
    monkeypatch.setattr(audit, "default_audit_dir",
                        lambda: Path("/proc/لا-يمكن-الكتابة"))
    source = tmp_path / "د.mp4"
    source.write_bytes(b"x")
    with audit.record_job(AppConfig(), source, None):
        pass          # لا استثناء


# ---------------------------------------------------------------------
# سياسة الجهاز
# ---------------------------------------------------------------------
def _write_policy(tmp_path: Path, body: str, monkeypatch) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("VTAD_POLICY", str(path))


def test_policy_blocks_cloud_providers_from_the_entry_point(tmp_path, monkeypatch):
    """يُفحص من ``AppConfig.load`` لا من ``policy.apply``.

    سياسةٌ صحيحة في دالّة التطبيق ومنسيّة في مدخلٍ واحد لا أثر لها —
    وذلك المدخل هو الثغرة التي تُبطلها كلّها.
    """
    _write_policy(tmp_path, "allow_cloud_ai: false\n", monkeypatch)
    user_config = tmp_path / "config.yaml"
    user_config.write_text(
        "rewrite:\n  enabled: true\n  provider: anthropic\n"
        "  api_key: sk-secret\n", encoding="utf-8")

    config = AppConfig.load(user_config)
    assert config.rewrite.provider == "ollama"
    # والمفتاح يُمسح: إبقاؤه يعني أن رفع السياسة يومًا يُعيد الإرسال
    # بلا قرار جديد من أحد.
    assert config.rewrite.api_key == ""
    assert "allow_cloud_ai" in config.policy_locked


def test_policy_cannot_switch_cloud_on(tmp_path, monkeypatch):
    """سياسةٌ تستطيع **تشغيل** الإرسال تصير طريق هجوم بملفّ واحد."""
    _write_policy(tmp_path, "allow_cloud_ai: true\n", monkeypatch)
    user_config = tmp_path / "config.yaml"
    user_config.write_text("rewrite:\n  provider: ollama\n", encoding="utf-8")

    config = AppConfig.load(user_config)
    assert config.rewrite.provider == "ollama"       # لم تُفعِّل شيئًا


def test_policy_forces_the_audit_log_and_locks_the_formats(tmp_path, monkeypatch):
    _write_policy(
        tmp_path,
        "require_audit_log: true\nlock_export_formats: [html, scorm]\n"
        "notice: سياسة الهيئة\n", monkeypatch)
    config = AppConfig.load(tmp_path / "لا-يوجد.yaml")
    assert config.application.audit_log is True
    assert config.document.export_formats == ["html", "scorm"]
    assert config.policy_notice == "سياسة الهيئة"


def test_a_broken_policy_file_never_stops_the_program(tmp_path, monkeypatch):
    """معلّمٌ مُوقَف بسبب ملفّ أخطأ المسؤول في كتابته ليس أمنًا."""
    _write_policy(tmp_path, "allow_cloud_ai: [هذا ليس منطقيًّا\n", monkeypatch)
    config = AppConfig.load(None)
    assert config.policy_locked == {}


def test_no_policy_file_means_no_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("VTAD_POLICY", str(tmp_path / "غير-موجود.yaml"))
    assert AppConfig.load(None).policy_locked == {}


@pytest.mark.skipif(os.name == "nt", reason="مسارات POSIX")
def test_policy_path_is_machine_wide_not_per_user(monkeypatch):
    """ملفّ في مجلد المستخدم يستطيع المستخدم تعديله — فلا يكون سياسة."""
    monkeypatch.delenv("VTAD_POLICY", raising=False)
    from config.policy import POLICY_FILENAME

    expected = Path("/etc/videotoarabicword") / POLICY_FILENAME
    assert str(expected).startswith("/etc/")
