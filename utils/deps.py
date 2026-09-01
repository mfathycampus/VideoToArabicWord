"""فحص البيئة قبل التشغيل — يمنع رسائل الخطأ المُضلِّلة.

أشهر عطل تثبيت في هذا المشروع لا علاقة له بالكود:

    ModuleNotFoundError: No module named 'exceptions'

يبدو غامضًا تمامًا، وسببه أن على الجهاز حزمة **``docx``** بدل
**``python-docx``**. الأولى مشروع مهجور من زمن Python 2، تثبّت ملفًا
باسم ``docx.py`` يحجب الحزمة الصحيحة ويحاول استيراد وحدة ``exceptions``
التي أُزيلت من Python 3. تثبيت ``python-docx`` فوقها **لا يحلّ المشكلة**
لأن ``docx.py`` يبقى ويفوز في ترتيب الاستيراد.

هذه الوحدة تكشف الحالة وتشرحها بالعربية مع أمر الإصلاح الدقيق.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

MIN_PYTHON = (3, 10)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class Diagnosis:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.ok]


def _pip_command() -> str:
    """أمر pip المرتبط بمفسّر Python الجاري — لا ``pip`` المجرّد.

    كتابة ``pip install`` في PowerShell قد تصيب مفسّرًا آخر تمامًا،
    وهو سبب شائع لـ "ثبّتُّ الحزمة ولا تزال مفقودة".
    """
    return f'"{sys.executable}" -m pip'


def check_python_version() -> CheckResult:
    current = sys.version_info[:3]
    ok = current >= MIN_PYTHON
    return CheckResult(
        "إصدار Python",
        ok,
        f"{'.'.join(map(str, current))} ({sys.executable})",
        "" if ok else f"الحد الأدنى {'.'.join(map(str, MIN_PYTHON))}",
    )


def check_docx_package() -> CheckResult:
    """يميّز بين ``python-docx`` الصحيحة و ``docx`` الخاطئة."""
    spec = importlib.util.find_spec("docx")
    if spec is None:
        return CheckResult(
            "python-docx", False, "غير مثبّتة",
            f"{_pip_command()} install python-docx")

    origin = Path(spec.origin) if spec.origin else None
    # الحزمة الصحيحة مجلد فيه __init__.py؛ الخاطئة ملف مفرد docx.py
    is_package = bool(spec.submodule_search_locations)
    if not is_package or (origin and origin.name != "__init__.py"):
        return CheckResult(
            "python-docx", False,
            f"مثبّتة الحزمة الخاطئة «docx» في {origin}",
            f"{_pip_command()} uninstall -y docx\n"
            f"        {_pip_command()} install --upgrade --force-reinstall python-docx")

    try:
        import docx
        if not hasattr(docx, "Document"):
            return CheckResult(
                "python-docx", False, "الوحدة موجودة بلا Document",
                f"{_pip_command()} uninstall -y docx && "
                f"{_pip_command()} install python-docx")
        version = getattr(docx, "__version__", "?")
        return CheckResult("python-docx", True, f"سليمة (إصدار {version})")
    except ImportError as exc:
        hint = ""
        if "exceptions" in str(exc):
            hint = " — هذه بصمة حزمة «docx» القديمة"
        return CheckResult(
            "python-docx", False, f"فشل الاستيراد: {exc}{hint}",
            f"{_pip_command()} uninstall -y docx\n"
            f"        {_pip_command()} install --upgrade --force-reinstall python-docx")


_REQUIRED = [
    ("PyQt6", "PyQt6", "الواجهة الرسومية"),
    ("cv2", "opencv-python", "معالجة الفيديو"),
    ("numpy", "numpy", "الحسابات العددية"),
    ("skimage", "scikit-image", "مقاييس تشابه الصور"),
    ("PIL", "Pillow", "حفظ الصور"),
    ("lxml", "lxml", "توليد OOXML"),
    ("pydantic", "pydantic", "عقود البيانات"),
    ("yaml", "PyYAML", "ملف الإعداد"),
    ("faster_whisper", "faster-whisper", "التفريغ الصوتي"),
    ("ctranslate2", "ctranslate2", "محرك التفريغ"),
]


def check_imports() -> List[CheckResult]:
    results: List[CheckResult] = []
    for module, package, purpose in _REQUIRED:
        if importlib.util.find_spec(module) is not None:
            results.append(CheckResult(package, True, purpose))
        else:
            results.append(CheckResult(
                package, False, f"مفقودة — {purpose}",
                f"{_pip_command()} install {package}"))
    return results


def check_binaries() -> List[CheckResult]:
    results: List[CheckResult] = []
    bundled = Path(__file__).resolve().parents[1] / "bin"
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name) or shutil.which(f"{name}.exe")
        if not found:
            candidate = bundled / (f"{name}.exe" if sys.platform == "win32" else name)
            found = str(candidate) if candidate.is_file() else None
        essential = name == "ffprobe"
        results.append(CheckResult(
            name, bool(found), found or "غير موجود",
            "" if found else f'"{sys.executable}" tools/setup_ffmpeg.py'))
    return results


def check_environment_isolation() -> CheckResult:
    """يحذّر من التثبيت خارج بيئة افتراضية."""
    in_venv = (hasattr(sys, "real_prefix")
               or sys.prefix != getattr(sys, "base_prefix", sys.prefix))
    if in_venv:
        return CheckResult("بيئة معزولة", True, f"مفعّلة ({sys.prefix})")
    return CheckResult(
        "بيئة معزولة", True,          # تحذير لا فشل
        "غير مفعّلة — التثبيت في site-packages العام",
        "يُنصح بشدة: python -m venv .venv ثم تفعيلها قبل التثبيت")


def diagnose() -> Diagnosis:
    diagnosis = Diagnosis()
    diagnosis.results.append(check_python_version())
    diagnosis.results.append(check_environment_isolation())
    diagnosis.results.append(check_docx_package())
    diagnosis.results.extend(r for r in check_imports() if r.name != "python-docx")
    diagnosis.results.extend(check_binaries())
    return diagnosis


def fix_commands(diagnosis: Diagnosis) -> List[str]:
    """أوامر الإصلاح وحدها، بلا نص عربي — جاهزة للنسخ واللصق.

    طرفية ويندوز لا تشكّل العربية ولا تعيد ترتيبها، فتظهر الحروف مقلوبة
    ومتقطعة. الأوامر لاتينية بالكامل فتبقى مقروءة مهما كانت الطرفية.
    """
    commands: List[str] = []
    for result in diagnosis.failures:
        for line in result.fix.splitlines():
            command = line.strip()
            if command and not command.startswith(("يُنصح", "ضع", "الحد")):
                commands.append(command)
    return commands


def format_report(diagnosis: Diagnosis) -> str:
    lines = ["", "فحص البيئة  /  Environment check", "=" * 62]
    for result in diagnosis.results:
        mark = "OK  " if result.ok else "FAIL"
        lines.append(f" [{mark}] {result.name:<18s} {result.detail}")
        if result.fix:
            for fix_line in result.fix.splitlines():
                lines.append(f"          -> {fix_line.strip()}")
    lines.append("=" * 62)

    if diagnosis.ok:
        lines.append("البيئة جاهزة  /  Environment ready — run:  python app.py")
        return "\n".join(lines)

    lines.append(f"{len(diagnosis.failures)} مشكلة  /  "
                 f"{len(diagnosis.failures)} problem(s) blocking startup.")
    commands = fix_commands(diagnosis)
    if commands:
        # كتلة أوامر نظيفة: أهم جزء في التقرير، ويجب أن يبقى مقروءًا
        # حتى في طرفية لا تدعم العربية إطلاقًا.
        lines += ["", "-" * 62, "COPY AND RUN THESE COMMANDS:", "-" * 62]
        seen = set()
        for command in commands:
            if command not in seen:
                seen.add(command)
                lines.append(f"  {command}")
        lines.append("-" * 62)
        lines.append("  then re-run:  python tools/doctor.py")
    return "\n".join(lines)


def require_ready() -> None:
    """يوقف التشغيل برسالة مفهومة بدل انهيار استيراد غامض."""
    diagnosis = diagnose()
    if diagnosis.ok:
        return
    raise SystemExit(format_report(diagnosis))
