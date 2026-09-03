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

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from utils.logger import logger

# عربي + إنجليزي: شرائح كثيرة تخلط مصطلحات إنجليزية أو كودًا برمجيًا
# وسط نص عربي (نفس ملاحظة قاموس المصطلحات في TranscriptionConfig).
LANGUAGES = "ara+eng"

_cached_exe: Optional[str] = ""   # "" = لم يُفحص بعد، None = غير موجود


def tesseract_executable() -> Optional[str]:
    """مسار ثنائي Tesseract، أو ``None`` إن لم يكن مثبَّتًا. يُخزَّن
    الفحص مؤقتًا (‏``shutil.which`` يمسح PATH في كل استدعاء)."""
    global _cached_exe
    if _cached_exe == "":
        _cached_exe = shutil.which("tesseract") or shutil.which("tesseract.exe")
    return _cached_exe


def is_available() -> bool:
    return tesseract_executable() is not None


def install_hint() -> str:
    if sys.platform == "win32":
        return ("ثبّت Tesseract OCR من "
                "https://github.com/UB-Mannheim/tesseract/wiki — اختر حزمة "
                "اللغة العربية ضمن خيارات التثبيت، ثم أضِفه إلى PATH.")
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
        try:
            result = subprocess.run(
                [exe, str(image_path), str(out_base),
                 "-l", LANGUAGES, "--psm", "3"],
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

        out_file = out_base.with_suffix(".txt")
        if not out_file.exists():
            return ""
        try:
            text = out_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return _clean(text)


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
