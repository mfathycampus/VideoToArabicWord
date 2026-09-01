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
