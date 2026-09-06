"""استخراج النص الظاهر على الشاشة من لقطة مختارة (OCR) عبر Tesseract.

القرار: Tesseract لا EasyOCR ولا PaddleOCR — كلاهما يجرّ PyTorch معه،
فيخرق ADR-012 (بلا PyTorch) ويعيد بالضبط مشكلة تضارب OpenMP التي
أُصلحت في 1.3.2 (انظر CHANGELOG). ولا خدمة سحابية (Google Vision،
Azure) — تخرق ADR-002 (لا API خارجي ضمن الـpipeline الأساسي).

Tesseract ثنائي خارجي منفصل بالضبط مثل ffmpeg (ADR-013): يُستدعى عبر
``subprocess`` كما تفعل ``utils/ffmpeg_service.py``، بلا أي تبعية pip
جديدة (لا ``pytesseract``) — سطر أوامر واحد يكفي، ولا حاجة لوسيط.

اختياري بالكامل على مستويين: تفعيل صريح في الإعداد
(``KeyframeConfig.enable_ocr``)، ثم توفّر الثنائي فعليًا على الجهاز.
غياب أي منهما لا يوقف المعالجة ولا حتى يمنع إنتاج المستند — فقط يترك
``ocr_text`` فارغًا لكل لقطة (انظر ``core/pipeline.py::_run_ocr``).
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from utils.logger import logger

# عربي + إنجليزي: شرائح كثيرة تخلط مصطلحات إنجليزية أو كودًا برمجيًا
# وسط نص عربي (نفس ملاحظة قاموس المصطلحات في TranscriptionConfig).
LANGUAGES = "ara+eng"

#: أدنى ثقة (0–100) يقبلها سطرٌ مقروء. Tesseract يعطي ثقة لكل كلمة،
#: ومتوسّطها على السطر يفصل القراءة الناجحة عن الفاشلة فصلًا حادًّا.
#:
#: **مقيسة على الخمس عشرة لقطة المستخرَجة من تسجيل حقيقي**، وهي أوّل
#: رقم في هذا الملف ليس تقديرًا:
#:
#:   91  Enter password                     ← صحيح
#:   93  Import Classes                     ← صحيح
#:   93  Social Studies - Section 3         ← صحيح
#:   90  How many days are in your rotation?← صحيح
#:   80  Maarif- Grade 7 - Computer Studios ← صحيح
#:   ───────────────────────── الحدّ ─────────────────────────
#:   72  This Week ) € > f} Week of August  ← مشوّش
#:   63  Bi Lessons | Chalk - Googke Chrome ← شريط متصفّح
#:   51  CAD Loder days with numbers CB     ← فاشل
#:   33  ston. © Freee (ease text ner Al    ← فاشل
#:
#: قبل هذا الفحص كان كل ما سبق يُكتب تعليقًا تحت الصور بلا تمييز،
#: وينال من بوابة الجودة 100٪ لأن شرطها كان «هل قرأ Tesseract شيئًا؟».
MIN_LINE_CONFIDENCE = 75.0

_cached_exe: Optional[str] = ""   # "" = لم يُفحص بعد، None = غير موجود


def _windows_install_dirs() -> list[Path]:
    """مواضع تثبيت Tesseract المعتادة على ويندوز، بترتيب الأرجحية.

    مثبِّت UB-Mannheim **لا يضيف Tesseract إلى PATH افتراضيًّا**: الخيار
    موجود لكنه غير مؤشَّر، ومن يثبّت بالضغط على «التالي» — أي كل معلّم —
    ينتهي بـTesseract مثبَّتًا وغير مرئيّ لـ``shutil.which``.

    وقع هذا فعلًا: ثبّته المستخدم كما طُلب منه، ثم فتح البرنامج فوجد
    التحذير كما هو. أن نطلب من معلّم تحرير متغيّرات البيئة ليعمل
    البرنامج هو تحميلٌ لعطبنا عليه.
    """
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "Tesseract-OCR")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Tesseract-OCR")
    return candidates


def _from_windows_registry() -> Optional[str]:
    """مجلد التثبيت كما سجّله المثبِّت — الأدقّ حين يُختار مسار غير معتاد."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:                                     # ليس ويندوز
        return None

    for root in (getattr(winreg, "HKEY_LOCAL_MACHINE", None),
                 getattr(winreg, "HKEY_CURRENT_USER", None)):
        if root is None:
            continue
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Tesseract-OCR") as key:
                install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
        except OSError:
            continue
        candidate = Path(str(install_dir)) / "tesseract.exe"
        if candidate.exists():
            return str(candidate)
    return None


def tesseract_executable() -> Optional[str]:
    """مسار ثنائي Tesseract، أو ``None`` إن لم يكن مثبَّتًا.

    ثلاثة مصادر بالترتيب: ‏PATH، ثم مواضع التثبيت المعتادة، ثم سجلّ
    ويندوز. الاعتماد على PATH وحده كان يجعل تثبيتًا صحيحًا يبدو غيابًا.

    يُخزَّن الفحص مؤقتًا (‏``shutil.which`` يمسح PATH في كل استدعاء)،
    ولذلك ``refresh=True``: من يثبّت Tesseract والبرنامج مفتوح يستحقّ
    أن يراه بلا إعادة تشغيل.
    """
    global _cached_exe
    if _cached_exe != "":
        return _cached_exe

    found = shutil.which("tesseract") or shutil.which("tesseract.exe")

    if found is None and sys.platform == "win32":
        for directory in _windows_install_dirs():
            candidate = directory / "tesseract.exe"
            if candidate.exists():
                found = str(candidate)
                break
        if found is None:
            found = _from_windows_registry()
        if found:
            logger.info(f"عُثر على Tesseract خارج PATH: {found}")

    _cached_exe = found
    return _cached_exe


def refresh() -> Optional[str]:
    """يُبطل الفحص المخزَّن ويعيده — بعد تثبيتٍ والبرنامج مفتوح."""
    global _cached_exe
    _cached_exe = ""
    return tesseract_executable()


def is_available() -> bool:
    return tesseract_executable() is not None


def languages() -> list[str]:
    """اللغات المثبَّتة فعلًا، أو قائمة فارغة إن تعذّر السؤال.

    وجود ``tesseract.exe`` لا يعني وجود العربية: حزم اللغات اختيارية في
    المثبِّت، ومن يتخطّاها يحصل على OCR يقرأ الإنجليزية وحدها ويُخرج
    من الشرائح العربية حروفًا مبعثرة.
    """
    exe = tesseract_executable()
    if exe is None:
        return []
    try:
        result = subprocess.run([exe, "--list-langs"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=15.0)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    # أول سطر عنوان («List of available languages…») ثم لغة في كل سطر
    return [line.strip() for line in result.stdout.splitlines()[1:]
            if line.strip()]


def has_arabic() -> bool:
    installed = languages()
    return not installed or "ara" in installed


def install_hint() -> str:
    if sys.platform == "win32":
        return ("ثبّت Tesseract OCR من "
                "https://github.com/UB-Mannheim/tesseract/wiki — واختر حزمة "
                "اللغة العربية (Arabic) ضمن خيارات التثبيت.")
    if sys.platform == "darwin":
        return "brew install tesseract tesseract-lang"
    return "sudo apt install tesseract-ocr tesseract-ocr-ara"


def extract_text(image_path: Path, timeout_seconds: float = 20.0) -> str:
    """يستخرج النص من صورة لقطة. سلسلة فارغة عند أي فشل أو غياب —
    OCR إثراء اختياري لمحتوى المستند، وليس مرحلة حرجة في الـpipeline،
    فلا يجوز أن يُسقط استخراج لقطة واحدة معالجة الفيديو كلها.
    """
    exe = tesseract_executable()
    if exe is None:
        return ""
    if not Path(image_path).exists():
        return ""

    with tempfile.TemporaryDirectory(prefix="ocr_") as tmp:
        out_base = Path(tmp) / "out"
        # ``tsv`` بدل النصّ الخام: يعطي إحداثيات كل كلمة **وثقتها**،
        # وهي ما يفصل السطر المقروء عن الضوضاء. الأمر نفسه والزمن نفسه.
        try:
            result = subprocess.run(
                [exe, str(image_path), str(out_base),
                 "-l", LANGUAGES, "--psm", "3", "tsv"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(f"تجاوز OCR المهلة على {Path(image_path).name}")
            return ""
        except OSError as exc:
            logger.warning(f"تعذّر تشغيل tesseract: {exc}")
            return ""

        if result.returncode != 0:
            logger.warning(
                f"فشل OCR على {Path(image_path).name}: "
                f"{result.stderr.strip()[:300]}")
            return ""

        out_file = out_base.with_suffix(".tsv")
        if not out_file.exists():
            return ""
        try:
            raw = out_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return _clean(_confident_lines(raw))


def _confident_lines(tsv_text: str) -> str:
    """يجمع كلمات كل سطر ويُسقط ما متوسّط ثقته دون الحدّ.

    الترتيب يبقى ترتيب الشاشة لا ترتيب الثقة: هذا النصّ يُطبع تحت
    الصورة في المستند، فترتيبه بالثقة يجعله غير مقروء.
    """
    reader = csv.DictReader(tsv_text.splitlines(), delimiter="\t",
                            quoting=csv.QUOTE_NONE)
    lines: "OrderedDict[tuple, list]" = OrderedDict()
    for row in reader:
        word = (row.get("text") or "").strip()
        if not word:
            continue
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            continue
        if confidence < 0:                      # ‏-1 = ليس كلمة
            continue
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        lines.setdefault(key, []).append((word, confidence))

    kept = []
    for words in lines.values():
        mean = sum(c for _, c in words) / len(words)
        if mean >= MIN_LINE_CONFIDENCE:
            kept.append(" ".join(w for w, _ in words))
    return "\n".join(kept)


def _clean(text: str) -> str:
    """يزيل الأسطر الفارغة وضوضاء OCR الشائعة (أسطر بحرف واحد أو رموز
    فقط) بلا أي محاولة تصحيح إملائي — هذا نص خام للعرض لا للتحليل."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 2:
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()
