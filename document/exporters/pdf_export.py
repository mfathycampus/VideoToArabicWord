"""‏PDF عبر LibreOffice في وضع بلا واجهة — تحويل المستند لا إعادة بنائه.

**لماذا هذا الطريق وليس مُصيِّرًا مستقلًّا.** بناء PDF عربي من الصفر
يعني إعادة كتابة كل ما في ``document/template.py`` و``document/rtl_utils``
مرّة ثانية: تشكيل الحروف ووصلها، اتجاه الفقرة، سلوك الأرقام اللاتينية
داخل نصّ عربي، الفهرس، الرأس والتذييل. وهو عملٌ لن يخرج متطابقًا مع
مستند Word أبدًا، فيصير عند المستخدم **مخرجان بشكلين مختلفين لمحتوى
واحد**. التحويل يضمن التطابق بحكم التعريف.

**والثمن معروف ومحدود:** LibreOffice اعتماد خارجي **اختياري**. غيابه
لا يُفشل شيئًا — يُسجَّل سطر يقول ما ينقص وكيف يُثبَّت، وتُكمل بقية
المخرجات. ومن لا يريد تثبيته يطبع صفحة HTML إلى PDF من متصفّحه.

**عزل ملفّ التعريف إلزامي.** ‏LibreOffice يرفض الإقلاع بلا واجهة إن
كانت نسخة أخرى مفتوحة بملفّ التعريف نفسه — وهو الحال الطبيعي على جهاز
المعلّم: البرنامج مفتوح أمامه على مستند آخر. بلا
``-env:UserInstallation`` يفشل التحويل بصمت عند المستخدمين وحدهم
وينجح دائمًا على جهاز التطوير.

**والتحويل يقع في مجلد مؤقّت ثم يُنقل الناتج.** قياسٌ على تحويل حقيقي:
‏soffice يخلّف في مجلد الإخراج ملفَّ قفلٍ ``.~lock.<الاسم>#`` وملفًّا
مؤقّتًا بحجم الـ PDF كاملًا. توجيهه إلى مجلد المهمة مباشرةً يعني
مخلّفات بجوار مخرجات المستخدم في كل تحويل — وملفٌّ مخفيٌّ اسمه يبدأ
بنقطة لا يراه أحد على ويندوز، فيبقى إلى الأبد. المجلد المؤقّت يبتلع
ذلك كلّه ويُحذف، ولا يصل مجلد المهمة إلا ملفُّ PDF واحد.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from document.exporters import ExportContext, register
from utils.logger import logger

#: التحويل يفتح المستند كاملًا ويصيّر كل صورةٍ فيه. محاضرة بمئة لقطة
#: تتجاوز الدقيقة على معالج متواضع، فالسقف كريم عمدًا — وانتهاؤه يعني
#: عطلًا حقيقيًّا لا بطئًا.
TIMEOUT_SECONDS = 300

INSTALL_HINT = ("‏LibreOffice غير مثبَّت — تحويل PDF يحتاجه. "
                "نزّله مجانًا من https://www.libreoffice.org/download "
                "أو اطبع صفحة HTML إلى PDF من متصفّحك.")

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
_MAC_CANDIDATES = ("/Applications/LibreOffice.app/Contents/MacOS/soffice",)
_POSIX_CANDIDATES = ("/usr/bin/soffice", "/usr/local/bin/soffice",
                     "/snap/bin/libreoffice")


def find_soffice() -> Optional[Path]:
    """يعيد مسار ``soffice`` أو ``None``. يُستعمل في Doctor أيضًا."""
    override = os.environ.get("SOFFICE_PATH", "").strip()
    if override and Path(override).is_file():
        return Path(override)

    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    if sys.platform.startswith("win"):
        candidates: tuple = _WINDOWS_CANDIDATES
    elif sys.platform == "darwin":
        candidates = _MAC_CANDIDATES
    else:
        candidates = _POSIX_CANDIDATES
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


def convert(docx_path: Path, output_dir: Path,
            soffice: Optional[Path] = None) -> Optional[Path]:
    """يحوّل ملفًّا واحدًا ويعيد مسار الـ PDF، أو ``None`` عند التعذّر."""
    soffice = soffice or find_soffice()
    if soffice is None:
        logger.info(INSTALL_HINT)
        return None

    docx_path = Path(docx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{docx_path.stem}.pdf"

    with tempfile.TemporaryDirectory(prefix="vtad_soffice_") as workspace:
        work = Path(workspace)
        profile_uri = (work / "profile").resolve().as_uri()
        staging = work / "out"
        staging.mkdir()

        command: List[str] = [
            str(soffice),
            f"-env:UserInstallation={profile_uri}",
            "--headless", "--norestore", "--nolockcheck", "--nodefault",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(staging),
            str(docx_path),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS,
                # نافذة وحدة التحكّم على ويندوز: بلا هذا يومض إطار أسود
                # أمام المستخدم في منتصف المعالجة.
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if sys.platform.startswith("win") else 0),
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                f"تجاوز تحويل PDF {TIMEOUT_SECONDS} ثانية — أُلغي.")
            return None
        except Exception as exc:
            logger.warning(f"تعذّر تشغيل LibreOffice: {exc}")
            return None

        produced = staging / f"{docx_path.stem}.pdf"
        if produced.is_file() and produced.stat().st_size > 0:
            # ‏replace لا move: مجلد المهمة قد يحمل PDF من تشغيل سابق،
            # والاستبدال الذرّي لا يترك نصف ملف إن انقطع شيء.
            shutil.move(str(produced), str(target))
            return target

        # ‏soffice يعيد رمز خروج صفر حتى حين يفشل التحويل. وجود الملف هو
        # المحك الوحيد الموثوق؛ رمز الخروج وحده كان سيُبلّغ نجاحًا كاذبًا.
        detail = (result.stderr or result.stdout or "").strip()[:300]
        logger.warning("لم يُنتج LibreOffice ملف PDF"
                       + (f" — {detail}" if detail else "."))
        return None


def export(ctx: ExportContext) -> Optional[Path]:
    docx = ctx.docx_path
    if docx is None or not Path(docx).is_file():
        logger.info("لا مستند Word لتحويله — تُخطّى صيغة PDF.")
        return None
    return convert(Path(docx), ctx.base_path.parent)


register("pdf", export)
