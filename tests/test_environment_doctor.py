"""فحص البيئة — انحدار على أعطال التثبيت الحقيقية.

العطل المرجعي:
    ModuleNotFoundError: No module named 'exceptions'
سببه حزمة «docx» القديمة بدل «python-docx». الرسالة الأصلية لا تدل على
السبب إطلاقًا، فيضيع المستخدم في تثبيت حزمة «exceptions» غير الموجودة.
"""
import sys
import types
from pathlib import Path

import pytest

from utils.deps import (
    check_binaries, check_docx_package, check_environment_isolation,
    check_imports, check_python_version, diagnose, format_report,
)


def test_correct_python_docx_is_detected():
    result = check_docx_package()
    assert result.ok, result.detail
    assert "سليمة" in result.detail


def test_wrong_docx_module_is_detected(tmp_path, monkeypatch):
    """يحاكي حزمة docx القديمة: ملف مفرد docx.py يحجب الحزمة الصحيحة."""
    fake = tmp_path / "docx.py"
    fake.write_text("from exceptions import PendingDeprecationWarning\n",
                    encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for module in [m for m in list(sys.modules) if m == "docx" or m.startswith("docx.")]:
        monkeypatch.delitem(sys.modules, module, raising=False)
    import importlib
    importlib.invalidate_caches()

    result = check_docx_package()
    assert not result.ok, "لم تُكتشف الحزمة الخاطئة"
    assert "docx" in result.detail
    assert "uninstall" in result.fix, "الإصلاح يجب أن يبدأ بإزالة الحزمة الخاطئة"
    assert "python-docx" in result.fix


def test_missing_docx_is_detected(monkeypatch):
    import importlib.util
    original = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "docx":
            return None
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    result = check_docx_package()
    assert not result.ok
    assert "install python-docx" in result.fix


def test_fix_commands_reference_current_interpreter():
    """الأوامر يجب أن تربط بمفسّر Python الجاري لا بـ pip المجرّد.

    ``pip install`` في PowerShell قد يصيب مفسّرًا آخر — وهو سبب شائع
    لـ "ثبّتُّ الحزمة ولا تزال مفقودة".
    """
    import importlib.util
    original = importlib.util.find_spec
    results = [r for r in check_imports() if not r.ok]
    for result in results:
        assert sys.executable in result.fix, \
            f"{result.name}: أمر الإصلاح لا يذكر المفسّر الجاري"
        assert "-m pip" in result.fix


def test_python_version_check():
    result = check_python_version()
    assert result.ok, f"إصدار Python غير مدعوم: {result.detail}"
    assert sys.executable in result.detail


def test_binaries_check_reports_ffprobe_separately():
    names = {r.name for r in check_binaries()}
    assert names == {"ffmpeg", "ffprobe"}


def test_isolation_check_never_blocks():
    """غياب البيئة المعزولة تحذير لا فشل."""
    assert check_environment_isolation().ok


def test_report_is_arabic_and_actionable():
    report = format_report(diagnose())
    assert any("؀" <= ch <= "ۿ" for ch in report), "التقرير ليس عربيًا"
    assert "فحص البيئة" in report


def test_diagnosis_covers_every_runtime_dependency():
    names = {r.name for r in diagnose().results}
    for required in ("python-docx", "PyQt6", "opencv-python", "numpy",
                     "faster-whisper", "ctranslate2", "ffmpeg", "ffprobe"):
        assert required in names, f"{required} غير مشمول في الفحص"


def test_requirements_do_not_cap_numpy():
    """انحدار: تقييد numpy<2 يجعل التثبيت مستحيلًا على Python 3.13/3.14.

    عجلات numpy 1.x غير موجودة لهذه الإصدارات، و opencv-python 5.x
    تشترط numpy>=2 على Python 3.9 فأعلى.
    """
    root = Path(__file__).resolve().parents[1]
    for name in ("requirements.txt", "requirements.in"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("numpy") and not stripped.startswith("#"):
                assert "<2" not in stripped, \
                    f"{name}: سقف numpy يكسر Python 3.13/3.14 -> {stripped}"


def test_requirements_never_list_the_wrong_docx():
    root = Path(__file__).resolve().parents[1]
    for name in ("requirements.txt", "requirements.in", "requirements-dev.txt"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#")[0].strip()
            if not stripped:
                continue
            package = stripped.split(">")[0].split("=")[0].split("<")[0].strip()
            assert package.lower() != "docx", \
                f"{name}: «docx» حزمة خاطئة — الصحيحة python-docx"


def test_fix_commands_block_is_pure_ascii():
    """كتلة الأوامر يجب أن تبقى مقروءة في طرفية لا تدعم العربية.

    طرفية ويندوز لا تشكّل الحروف العربية ولا تعيد ترتيبها، فتظهر مقلوبة.
    الأوامر هي الجزء القابل للتنفيذ، فيجب ألا تحتوي عربية إطلاقًا.
    """
    from utils.deps import Diagnosis, CheckResult, fix_commands

    diagnosis = Diagnosis(results=[
        CheckResult("scikit-image", False, "مفقودة",
                    '"C:\\Python314\\python.exe" -m pip install scikit-image'),
        CheckResult("ffprobe", False, "غير موجود",
                    '"C:\\Python314\\python.exe" tools/setup_ffmpeg.py'),
    ])
    commands = fix_commands(diagnosis)
    assert commands, "لم تُستخرج أوامر"
    for command in commands:
        assert not any("؀" <= ch <= "ۿ" for ch in command), \
            f"أمر يحتوي عربية: {command}"


def test_report_contains_copyable_command_block_on_failure():
    from utils.deps import CheckResult, Diagnosis, format_report
    diagnosis = Diagnosis(results=[
        CheckResult("scikit-image", False, "مفقودة",
                    "python -m pip install scikit-image")])
    report = format_report(diagnosis)
    assert "COPY AND RUN THESE COMMANDS" in report
    assert "pip install scikit-image" in report
    assert "doctor.py" in report, "يجب أن يذكّر بإعادة الفحص"


def test_report_says_ready_when_clean():
    from utils.deps import CheckResult, Diagnosis, format_report
    report = format_report(Diagnosis(results=[CheckResult("x", True, "fine")]))
    assert "app.py" in report
    assert "COPY AND RUN" not in report


def test_ffmpeg_failure_points_at_setup_script():
    from utils.deps import check_binaries
    import shutil as _shutil
    import utils.deps as deps

    original = _shutil.which
    try:
        deps.shutil.which = lambda name: None
        results = {r.name: r for r in check_binaries()}
        for name in ("ffmpeg", "ffprobe"):
            if not results[name].ok:
                assert "setup_ffmpeg.py" in results[name].fix, \
                    f"{name}: يجب توجيه المستخدم لأداة التنزيل التلقائي"
    finally:
        deps.shutil.which = original


def test_setup_ffmpeg_extracts_from_nested_archive(tmp_path):
    """أرشيفات FFmpeg الرسمية تضع الثنائيات داخل مجلدات متداخلة."""
    import zipfile
    import tools.setup_ffmpeg as setup

    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-7.1-essentials_build/bin/ffmpeg.exe", b"MZ fake")
        zf.writestr("ffmpeg-7.1-essentials_build/bin/ffprobe.exe", b"MZ fake")
        zf.writestr("ffmpeg-7.1-essentials_build/README.txt", b"docs")

    original_bin = setup.BIN
    original_system = setup.platform.system
    try:
        setup.BIN = tmp_path / "bin"
        setup.platform.system = lambda: "Windows"
        extracted = setup._extract(archive, setup.NEEDED)
        assert sorted(extracted) == ["ffmpeg.exe", "ffprobe.exe"]
        assert (tmp_path / "bin" / "ffprobe.exe").is_file()
    finally:
        setup.BIN = original_bin
        setup.platform.system = original_system

# ----------------------------------------------------------------------
# فحص صلاحية الكتابة — العطل الذي كان يمنع الإقلاع على ويندوز وحده
# ----------------------------------------------------------------------

def test_write_probe_closes_handle_before_deleting_it(tmp_path, monkeypatch):
    """الإغلاق قبل الحذف — وإلا فشل الفحص على كل جهاز ويندوز.

    ``mkstemp`` تُعيد واصفًا مفتوحًا، وويندوز يرفض حذف ملف مفتوح
    (‏WinError 32). الترتيب المعكوس كان يجعل ``require_ready`` يمنع
    إقلاع التطبيق كليًا هناك، بينما ينجح على لينكس وmacOS حيث حذف ملف
    مفتوح مسموح — لذلك يُثبَّت الترتيب نفسه هنا لا نتيجته وحدها.

    المراقبة مقيَّدة بواصف الفحص واسمه تحديدًا: ``tempfile`` يفتح ويغلق
    ملفًا داخليًا عند أول استخدام لتحديد مجلد المؤقتات، فمراقبة
    ``os.close`` على إطلاقها تجعل الاختبار متذبذبًا بحسب ترتيب تشغيله.
    """
    import os
    import tempfile
    from pathlib import Path as _Path
    from utils import deps

    order: list[str] = []
    probe: dict = {}
    real_mkstemp, real_close, real_unlink = tempfile.mkstemp, os.close, _Path.unlink

    def spy_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        probe["fd"], probe["name"] = fd, name
        return fd, name

    def spy_close(fd):
        if fd == probe.get("fd"):
            order.append("close")
        return real_close(fd)

    def spy_unlink(self, *args, **kwargs):
        if str(self) == probe.get("name"):
            order.append("unlink")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(_Path, "unlink", spy_unlink)

    result = deps.check_write_access(tmp_path)

    assert result.ok, result.detail
    assert order == ["close", "unlink"], f"ترتيب خاطئ: {order}"


def test_write_probe_leaves_no_residue(tmp_path):
    assert deps_check(tmp_path).ok
    assert not list(tmp_path.glob(".doctor-*")), "ملف الفحص لم يُنظَّف"


def deps_check(path):
    from utils.deps import check_write_access
    return check_write_access(path)


def test_write_probe_survives_undeletable_probe_file(tmp_path, monkeypatch):
    """الكتابة نجحت؛ تعذّر الحذف (مضاد فيروسات/مزامنة) ليس فشل صلاحية."""
    from pathlib import Path as _Path
    from utils.deps import check_write_access

    def refuse(self, *args, **kwargs):
        raise PermissionError("[WinError 32] الملف مستخدم من عملية أخرى")

    monkeypatch.setattr(_Path, "unlink", refuse)
    assert check_write_access(tmp_path).ok


def test_write_probe_targets_the_configured_output_dir_not_an_invented_one():
    """الفحص كان يخترع مجلدًا في المنزل (``~/VideoToArabicWord``) ويُنشئه،
    بدل مجلد الإخراج الذي يكتب فيه التطبيق فعلًا."""
    from utils.deps import default_output_dir

    target = default_output_dir()
    assert target.name != "VideoToArabicWord", \
        "الفحص يستهدف مجلدًا مخترعًا لا علاقة له بمخرجات التطبيق"


def test_arabic_advice_never_reaches_the_command_block():
    """النصائح العربية من الفحوص الحقيقية يجب ألا تظهر كأوامر للنسخ.

    الفلترة القديمة كانت بقائمة بادئات («يُنصح»، «ضع»، «الحد») فتسرّبت
    «اختر مجلد إخراج…» و«وفّر 2.0 GB…» إلى كتلة COPY AND RUN.
    """
    from utils.deps import CheckResult, Diagnosis, fix_commands, format_report

    diagnosis = Diagnosis(results=[
        CheckResult("صلاحية الكتابة", False, "C:\\Users\\x — تعذّرت الكتابة",
                    "اختر مجلد إخراج قابلًا للكتابة بدلًا من C:\\Users\\x"),
        CheckResult("مساحة القرص", False, "0.4 GB متاحة",
                    "وفّر 2.0 GB على الأقل قبل بدء المعالجة"),
        CheckResult("scikit-image", False, "مفقودة",
                    '"C:\\Python312\\python.exe" -m pip install scikit-image'),
    ])
    commands = fix_commands(diagnosis)

    for command in commands:
        assert not any("\u0600" <= ch <= "\u06ff" for ch in command), \
            f"نصيحة عربية تسرّبت كأمر: {command}"
    assert any("pip install scikit-image" in c for c in commands), \
        "الأمر الحقيقي اختفى مع الفلترة"
    # النصيحة نفسها تبقى معروضة في متن التقرير — تُقرأ ولا تُنفَّذ
    assert "اختر مجلد إخراج" in format_report(diagnosis)
