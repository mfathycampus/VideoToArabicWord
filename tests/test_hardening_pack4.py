"""الحزمة الرابعة: درجة SCORM من الإعداد، ورقم شريحة حقلًا، ومسرد لا يرفض الصحيح،
وإمكانية الوصول في مشغّل SCORM."""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ai.study_verify import term_in_source, verify
from config.schemas import GlossaryTerm, StudyPack
from config.settings import DocumentConfig
from tests.test_study_pack import _transcript


# ---------------------------------------------------------------------
# درجة النجاح من الإعداد
# ---------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    (70, 70), (85, 85), ("90", 90), (1, 1), (100, 100),
    (0, 70), (101, 70), (-3, 70), ("x", 70), (None, 70)])
def test_scorm_mastery_setting_is_clamped_not_fatal(raw, expected):
    """إعدادٌ معطوب يعود إلى الافتراضي — لا يمنع فتح البرنامج."""
    assert DocumentConfig(scorm_mastery=raw).scorm_mastery == expected


def test_pipeline_passes_the_configured_mastery_to_the_exporters(tmp_path, monkeypatch):
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin
    from tests.test_exporters import _metadata, _plan

    seen = {}
    import document.exporters as exporters

    def fake_write(ctx, formats):
        seen["options"] = dict(ctx.options)
        return []

    monkeypatch.setattr(exporters, "write_exports", fake_write)

    class Host(OutputsMixin):
        def __init__(self):
            self.config = AppConfig()
            self.config.document.scorm_mastery = 85
            self.config.document.export_formats = ["scorm"]

    Host()._write_exports(_plan(), _metadata(tmp_path), tmp_path, tmp_path / "a.docx", None)
    assert seen["options"]["scorm_mastery"] == 85


# ---------------------------------------------------------------------
# رقم الشريحة حقل حقيقي
# ---------------------------------------------------------------------
def test_slide_numbers_are_real_fields(tmp_path):
    pytest.importorskip("pptx", reason="python-pptx اختيارية")
    from PIL import Image

    from config.settings import DocumentConfig as Config
    from document.exporters import ExportContext
    from document.exporters.pptx_export import export
    from tests.test_exporters import _metadata, _plan

    images = tmp_path / "keyframes"
    images.mkdir()
    Image.new("RGB", (640, 360), (40, 60, 120)).save(images / "f1.jpg")
    path = export(ExportContext(plan=_plan(), metadata=_metadata(tmp_path),
                                images_dir=images, base_path=tmp_path / "x.docx",
                                document_config=Config()))
    with zipfile.ZipFile(path) as archive:
        slides = [archive.read(n).decode("utf-8") for n in archive.namelist()
                  if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    with_field = [x for x in slides if 'type="slidenum"' in x]
    assert len(with_field) >= len(slides) - 2        # الغلاف والختام بلا تذييل
    # والقيمة المخزَّنة موجودة لمن لا يحسب الحقول
    assert all(re.search(r'type="slidenum".*?<a:t>\d+</a:t>', x, re.S)
               for x in with_field)


# ---------------------------------------------------------------------
# المسرد
# ---------------------------------------------------------------------
@pytest.mark.parametrize("term, haystack, expected", [
    ("TCP", "بروتوكول tcp يضمن", True),                       # حرفي
    ("الفئة (لم يتم التصحيح)", "اختيار الفئة بانها لم يتم التصحيح للواجب", True),
    ("إعدادات الاختبار", "ضبط اعدادات هذا الاختبار", True),   # كل كلمات مضمونه
    ("مصطلح مخترع تماما", "بروتوكول tcp يضمن الوصول", False),
    ("UDP", "بروتوكول tcp يضمن", False),                        # كلمة واحدة: حرفي فقط
])
def test_glossary_term_matching(term, haystack, expected):
    from ai.study_verify import fold

    assert term_in_source(term, fold(haystack)) is expected


def test_a_term_with_only_some_of_its_words_in_the_source_is_still_dropped():
    pack = StudyPack(glossary=[GlossaryTerm(
        term="بروتوكول التوجيه المتقدم", definition="تعريف", segment_ids=[2])])
    result = verify(pack, _transcript())
    assert result.glossary == []
    assert any("لا يرد في المصدر" in d for d in result.dropped)


# ---------------------------------------------------------------------
# مشغّل SCORM: إمكانية الوصول
# ---------------------------------------------------------------------
def test_scorm_player_announces_the_current_step_and_moves_focus(tmp_path):
    from PIL import Image

    from document.exporters import ExportContext
    from document.exporters.scorm_export import export
    from tests.test_exporters import _metadata, _plan
    from tests.test_study_pack import _built_pack

    images = tmp_path / "keyframes"
    images.mkdir()
    Image.new("RGB", (640, 360), (30, 40, 90)).save(images / "f1.jpg")
    path = export(ExportContext(plan=_plan(), metadata=_metadata(tmp_path),
                                images_dir=images, base_path=tmp_path / "d.docx",
                                document_config=DocumentConfig(),
                                options={"study_pack": _built_pack()}))
    with zipfile.ZipFile(path) as archive:
        page = archive.read("index.html").decode("utf-8")
    assert "aria-current" in page                      # الخطوة الحالية معلنة
    assert "heading.focus(" in page and "tabindex" in page   # والتركيز ينتقل مع الوحدة
    assert 'id="result" role="status"' in page        # والنتيجة تُعلن بعد التصحيح
