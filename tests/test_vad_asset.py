"""نموذج VAD المفقود من الحزمة — والوجود الذي لا يعني العمل.

المستخدم شغّل الحزمة على جهازٍ نظيف فسقط التفريغ بعد استخراج الصوت:

    [E4001] فشل التفريغ الصوتي: [ONNXRuntimeError] : 3 : NO_SUCHFILE :
    Load model from ...\\_internal\\faster_whisper\\assets\\
    silero_vad_v6.onnx failed. File doesn't exist

السبب أن ``build.spec`` كان يجمع **وحدات** faster-whisper
(‏``collect_submodules``) ولا يجمع ملفّات بياناتها. فخرجت الحزمة
بالمكتبة كاملةً بلا نموذجها، و``vad_filter`` مفعّل افتراضيًّا — أي أن
**كل** تفريغ في الحزمة كان يسقط. لا بعضها.

ولماذا مرّ من كل بواباتنا:

* ``check_imports`` يسأل ``importlib.util.find_spec`` فيجد الوحدة
  ويقول «موجودة».
* ``build_smoke`` كان يفحص الثنائيات والشعار، ولا يعرف بملفّات بيانات
  حزم الطرف الثالث أصلًا.
* ``--selftest`` يُقلع ويخرج بصفر: الإقلاع لا يمسّ VAD.

وهو الدرس المكتوب في رأس ``build_smoke.py`` نفسه — «الوجود لا يعني
الإقلاع» — ولم يُطبَّق على المكتبات، فقط على الملفّ التنفيذي.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from utils.deps import VAD_MODEL_NAME, check_vad_asset, diagnose

ROOT = Path(__file__).resolve().parents[1]


# ── الاسم: مشتقٌّ من المكتبة لا مخمَّن ────────────────────────────────

def test_the_expected_name_is_what_faster_whisper_actually_opens():
    """الحارس الذي يسقط يوم تُعيد ترقيةٌ تسميةَ الملف.

    بلا هذا الاختبار يبقى الفحص يبحث عن اسمٍ قديم فيجده غائبًا دائمًا،
    أو — الأسوأ — يبحث عن اسمٍ موجود بينما المكتبة تفتح غيره فيمرّ على
    حزمة معطوبة.
    """
    faster_whisper_vad = pytest.importorskip("faster_whisper.vad")
    source = inspect.getsource(faster_whisper_vad.get_vad_model)
    opened = re.findall(r'"([^"]+\.onnx)"', source)
    assert opened == [VAD_MODEL_NAME], (
        f"المكتبة تفتح {opened} والفحص يبحث عن {VAD_MODEL_NAME}")


# ── الفحص ─────────────────────────────────────────────────────────────

def test_the_asset_is_present_in_this_environment():
    assert check_vad_asset().ok


def test_a_missing_asset_is_a_failure_not_a_warning(tmp_path, monkeypatch):
    """حزمةٌ بلا نموذج لا تفرّغ شيئًا — فهذا فشلٌ يمنع الإقلاع."""
    fw_utils = pytest.importorskip("faster_whisper.utils")

    monkeypatch.setattr(fw_utils, "get_assets_path", lambda: str(tmp_path))
    result = check_vad_asset()
    assert not result.ok
    assert VAD_MODEL_NAME in result.detail
    assert result.fix, "فشلٌ بلا أمر إصلاح يترك المستخدم واقفًا"


def test_the_check_runs_on_every_diagnosis():
    """موضعه في ``diagnose`` هو ما يجعله بوابةً وتحذيرًا معًا.

    ‏``diagnose`` يُستدعى من إقلاع التطبيق (فيعرف المعلّم في الثانية
    الأولى) ومن ``--selftest`` الذي يشغّله ``build_smoke`` على كل بناء
    (فيسقط البناء يوم يقع العطل).
    """
    assert any(r.name == "نموذج VAD" for r in diagnose().results)


# ── البناء: أن يُشحن أصلًا ────────────────────────────────────────────

def test_the_spec_collects_the_data_files_not_only_the_modules():
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("faster_whisper")' in spec, (
        "‏build.spec يجمع الوحدات وحدها — تعود الحزمة بلا نموذج VAD")


def test_the_bundle_check_knows_about_the_data_file():
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    import build_smoke

    assert any(VAD_MODEL_NAME in entry
               for entry in build_smoke.BUNDLED_DATA_FILES), (
        "‏build_smoke لا يفحص نموذج VAD — تمرّ الحزمة المعطوبة كما مرّت")


def test_the_bundle_check_fails_when_the_data_file_is_absent(tmp_path):
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    import build_smoke

    payload = tmp_path / "_internal"
    payload.mkdir()
    errors = build_smoke.validate_layout(tmp_path)
    assert any(VAD_MODEL_NAME in error for error in errors)
