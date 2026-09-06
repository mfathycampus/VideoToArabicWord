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

from utils.bundle import binary_name, bundled_dir, is_frozen

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

    داخل حزمة مجمّدة ``sys.executable`` هو ``VideoToArabicWord.exe``،
    فيصير الأمر ``"VideoToArabicWord.exe" -m pip install …`` — سطر بلا
    معنى يُملى على معلّم غير تقني. الحزمة تحمل تبعياتها معها، فأي نقص
    فيها عطبُ بناء لا يصلحه المستخدم بـ pip إطلاقًا.
    """
    if is_frozen():
        return ""
    return f'"{sys.executable}" -m pip'


def _reinstall_hint() -> str:
    """نصيحة الإصلاح حين يكون المستخدم أمام حزمة لا أمام مستودع."""
    return "نسخة التطبيق ناقصة — أعد تنزيل المثبِّت وثبّته من جديد"


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
            fix = (_reinstall_hint() if is_frozen()
                   else f"{_pip_command()} install {package}")
            results.append(CheckResult(
                package, False, f"مفقودة — {purpose}", fix))
    return results


def check_binaries() -> List[CheckResult]:
    """يبحث عن ffmpeg وffprobe: المشحون مع التطبيق أولًا، ثم ``PATH``.

    الترتيب مقصود ومطابق لما يفعله ``utils/media_probe.py`` وقت التشغيل:
    الثنائي المشحون هو ما سيُستعمل فعلًا، فهو ما يجب أن يُفحَص.

    كان الفحص يبني المسار من موقع هذا الملف، فيصير داخل الحزمة
    ``_internal/bin`` — مجلد لا وجود له، لأن ``build.spec`` يضع
    الثنائيات في جذر ``_internal``. فيفشل على كل جهاز بلا ffmpeg في
    ``PATH``، ويمنع الإقلاع كليًا وبصمت. انظر ``utils/bundle.py``.
    """
    results: List[CheckResult] = []
    bundled = bundled_dir()
    for name in ("ffmpeg", "ffprobe"):
        candidate = bundled / binary_name(name)
        found = (str(candidate) if candidate.is_file()
                 else shutil.which(name) or shutil.which(f"{name}.exe"))
        fix = ""
        if not found:
            fix = (_reinstall_hint() if is_frozen()
                   else f'"{sys.executable}" tools/setup_ffmpeg.py')
        results.append(CheckResult(
            name, bool(found), found or "غير موجود", fix))
    return results


def check_ocr_binary() -> CheckResult:
    """Tesseract اختياري (ميزة OCR — video/ocr.py). غيابه لا يمنع تشغيل
    البرنامج، لذا ``ok=True`` دائمًا هنا (تحذير لا فشل — نفس نمط
    ``check_environment_isolation`` أدناه)."""
    found = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if found:
        return CheckResult("tesseract (OCR)", True, found)
    hint = ("sudo apt install tesseract-ocr tesseract-ocr-ara"
            if sys.platform not in ("win32", "darwin") else
            "brew install tesseract tesseract-lang" if sys.platform == "darwin"
            else "https://github.com/UB-Mannheim/tesseract/wiki (حزمة اللغة العربية)")
    return CheckResult(
        "tesseract (OCR)", True,      # تحذير لا فشل — OCR ميزة اختيارية
        "غير مثبَّت — ميزة OCR (نص الشاشة) ستبقى معطّلة", hint)


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



def check_disk_space(path: Path | None = None, minimum_gb: float = 2.0) -> CheckResult:
    """يتحقق من المساحة الحرة دون افتراض مسار ثابت للتثبيت."""
    target = Path(path or Path.home()).resolve()
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return CheckResult("مساحة القرص", False, f"تعذر الفحص: {exc}")
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= minimum_gb
    return CheckResult(
        "مساحة القرص", ok, f"{free_gb:.1f} GB متاحة",
        f"وفّر {minimum_gb:.1f} GB على الأقل قبل بدء المعالجة" if not ok else "",
    )


def default_output_dir() -> Path:
    """مجلد الإخراج الفعلي كما يراه التطبيق.

    الاستيراد كسول ومحاط بـ ``try``: فحص البيئة يسبق عمدًا التحقق من
    ‏pydantic و PyYAML، فلا يجوز أن يعتمد على نجاح استيراد ``config``.
    """
    try:
        from config.settings import AppConfig
        return Path(AppConfig.load().application.output_dir)
    except Exception:
        return Path.home() / "VideoToDocOutput"


def check_write_access(path: Path | None = None) -> CheckResult:
    """يتحقق من إمكانية إنشاء ملف في مجلد الإخراج.

    ترتيب العمليات هنا ليس تفصيلًا أسلوبيًا: ``mkstemp`` تُعيد **واصفًا
    مفتوحًا**، وويندوز يرفض حذف ملف ما زال مفتوحًا
    (‏``PermissionError: [WinError 32]``). حذفُ الملف قبل ``os.close``
    كان يجعل هذا الفحص يفشل على **كل** جهاز ويندوز — أي أن
    ``require_ready`` يمنع إقلاع التطبيق كليًا — بينما ينجح على لينكس
    وmacOS حيث حذف ملف مفتوح مسموح. لذلك: الإغلاق أولًا ثم الحذف.

    وبقاء ملف الفحص لسبب خارجي (مضاد فيروسات، مزامنة سحابية) ليس فشل
    صلاحية كتابة: الكتابة نجحت بالفعل، فلا نُسقط الإقلاع من أجله.
    """
    import os
    import tempfile

    target = Path(path) if path is not None else default_output_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=str(target), prefix=".doctor-")
    except OSError as exc:
        return CheckResult("صلاحية الكتابة", False, f"{target} — {exc}",
                           f"اختر مجلد إخراج قابلًا للكتابة بدلًا من {target}")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        Path(name).unlink()
    except OSError:
        pass
    return CheckResult("صلاحية الكتابة", True, str(target))


def diagnose() -> Diagnosis:
    diagnosis = Diagnosis()
    diagnosis.results.append(check_python_version())
    diagnosis.results.append(check_environment_isolation())
    diagnosis.results.append(check_docx_package())
    diagnosis.results.extend(r for r in check_imports() if r.name != "python-docx")
    diagnosis.results.extend(check_binaries())
    diagnosis.results.append(check_ocr_binary())
    diagnosis.results.append(check_disk_space())
    diagnosis.results.append(check_write_access())
    return diagnosis


def _is_shell_command(line: str) -> bool:
    """هل هذا السطر أمرٌ يُنفَّذ، أم نصيحة عربية تُقرأ؟

    القائمة السابقة كانت تستثني بادئات عربية بعينها («يُنصح»، «ضع»،
    «الحد») فتسرّبت كل نصيحة تبدأ بغيرها — «اختر مجلد إخراج…» و«وفّر
    2.0 GB…» ظهرتا داخل كتلة «COPY AND RUN THESE COMMANDS» كأنهما أمران.

    الفحص هنا على أول حرف لا على السطر كله عمدًا: اشتراط ASCII كاملًا
    يحذف أمرًا صحيحًا على جهاز اسم مستخدمه عربي
    (‏``C:/Users/محمد/.../python.exe``)، فيبقى المستخدم بلا أي أمر
    يُنفّذه — وهو أسوأ من أمر يحتوي مسارًا عربيًا.
    """
    if not line:
        return False
    return (not ("\u0600" <= line[0] <= "\u06ff")
            and not line.lower().startswith(("http://", "https://")))


def fix_commands(diagnosis: Diagnosis) -> List[str]:
    """أوامر الإصلاح وحدها، بلا نص عربي — جاهزة للنسخ واللصق.

    طرفية ويندوز لا تشكّل العربية ولا تعيد ترتيبها، فتظهر الحروف مقلوبة
    ومتقطعة. الأوامر لاتينية بالكامل فتبقى مقروءة مهما كانت الطرفية.
    """
    commands: List[str] = []
    for result in diagnosis.failures:
        for line in result.fix.splitlines():
            command = line.strip()
            if command and _is_shell_command(command):
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
