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
import signal
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

INSTALL_HINT = ("‏لا Microsoft Word ولا LibreOffice — تحويل PDF يحتاج أحدهما. "
                "‏LibreOffice مجاني: https://www.libreoffice.org/download "
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


def _no_window() -> int:
    return (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win") else 0)


def _kill_tree(process: "subprocess.Popen") -> None:
    """يُنهي العملية **وأبناءها**.

    ``subprocess.run(timeout=…)`` يقتل العملية المباشرة وحدها. و‏soffice
    على ويندوز مُطلِق (``soffice.exe``) يُشغّل ‏``soffice.bin`` ابنًا له:
    قتل الأول يترك الثاني حيًّا يقفل الملف ويأكل ذاكرة جهاز المعلّم إلى
    أن يُعاد تشغيله.
    """
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30,
                           creationflags=_no_window())
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except Exception:                                   # noqa: BLE001
        try:
            process.kill()
        except Exception:                               # noqa: BLE001
            pass


def _run(command: List[str], timeout: float,
         env: Optional[dict] = None) -> "subprocess.CompletedProcess":
    """مثل ``subprocess.run`` لكن المهلة تُنهي شجرة العمليات كلّها."""
    kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                    "text": True, "env": env}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _no_window()
    else:
        kwargs["start_new_session"] = True      # مجموعة عمليات قابلة للقتل معًا
    process = subprocess.Popen(command, **kwargs)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=10)
        except Exception:                               # noqa: BLE001
            pass
        raise
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def _kill_recorded_word(pid_file: Path) -> None:
    """يقتل نسخة Word التي أطلقها هذا التحويل وحدها.

    Word يُشغَّل عبر COM فليس ابنًا لـPowerShell، فلا يطاله قتل الشجرة.
    والسكربت يسجّل معرّف النسخة **الجديدة** التي ظهرت بعد إنشائها؛
    ولا نقتل إلا ما سُجِّل — لا كل ``WINWORD`` على الجهاز، فقد يكون
    المعلّم يكتب في مستندٍ آخر.
    """
    try:
        pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
    except Exception:                                   # noqa: BLE001
        return
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=30,
                       creationflags=_no_window())
    except Exception as exc:                            # noqa: BLE001
        logger.debug(f"تعذّر إنهاء Word العالق ({pid}): {exc}")


#: ‏Word عبر COM من PowerShell — بلا أي حزمة Python إضافية (لا pywin32).
#: ‏wdExportFormatPDF = 17. المسارات تُمرَّر متغيّراتِ بيئة لا نصًّا داخل
#: السكربت: أسماء الملفّات العربية وعلامات الاقتباس فيها تكسر السكربت.
_WORD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$before = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$word = New-Object -ComObject Word.Application
$mine = @(Get-Process WINWORD -ErrorAction SilentlyContinue |
          ForEach-Object { $_.Id } | Where-Object { $before -notcontains $_ })
if ($mine.Count -eq 1 -and $env:VTAD_PIDFILE) {
    Set-Content -Path $env:VTAD_PIDFILE -Value $mine[0]
}
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($env:VTAD_DOCX, $false, $true)
    try { $doc.ExportAsFixedFormat($env:VTAD_PDF, 17) }
    finally { $doc.Close(0) }
} finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
"""


def convert_with_word(docx_path: Path, target: Path) -> Optional[Path]:
    """‏PDF عبر Microsoft Word المثبَّت — ويندوز وحده.

    البديل الطبيعي على جهاز معلّم: Word موجود غالبًا، وهو **نفسه** من
    يعرض المستند، فالـPDF يطابق ما يراه المستخدم حرفيًّا (بما فيه اتجاه
    الفقرات العربية الذي يقرؤه LibreOffice بطريقة مختلفة).
    """
    if not sys.platform.startswith("win"):
        return None
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        return None
    with tempfile.TemporaryDirectory(prefix="vtad_word_") as scratch:
        pid_file = Path(scratch) / "word.pid"
        env = dict(os.environ, VTAD_DOCX=str(Path(docx_path).resolve()),
                   VTAD_PDF=str(Path(target).resolve()),
                   VTAD_PIDFILE=str(pid_file))
        try:
            result = _run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                 "Bypass", "-Command", _WORD_SCRIPT],
                TIMEOUT_SECONDS, env)
        except subprocess.TimeoutExpired:
            _kill_recorded_word(pid_file)
            logger.warning(f"تجاوز تحويل PDF عبر Word {TIMEOUT_SECONDS} ثانية.")
            return None
        except Exception as exc:                        # noqa: BLE001
            logger.debug(f"تعذّر تشغيل Word للتحويل: {exc}")
            return None
    if Path(target).is_file() and Path(target).stat().st_size > 0:
        return Path(target)
    detail = (result.stderr or "").strip().splitlines()
    logger.debug("لم يُنتج Word ملف PDF"
                 + (f" — {detail[0][:200]}" if detail else "."))
    return None


def convert(docx_path: Path, output_dir: Path,
            soffice: Optional[Path] = None) -> Optional[Path]:
    """يحوّل ملفًّا واحدًا ويعيد مسار الـ PDF، أو ``None`` عند التعذّر.

    الترتيب: LibreOffice إن وُجد، وإلا Microsoft Word (ويندوز).
    """
    soffice = soffice or find_soffice()
    if soffice is None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        produced = convert_with_word(
            Path(docx_path), output_dir / f"{Path(docx_path).stem}.pdf")
        if produced is not None:
            logger.info("‏PDF عبر Microsoft Word.")
            return produced
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
            # ‏_run يُخفي نافذة وحدة التحكّم على ويندوز (بلا هذا يومض إطار
            # أسود أمام المستخدم)، ويُنهي شجرة العمليات عند المهلة.
            result = _run(command, TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"تجاوز تحويل PDF {TIMEOUT_SECONDS} ثانية — أُلغي.")
            return None
        except Exception as exc:
            logger.warning(f"تعذّر تشغيل LibreOffice: {exc}")
            return None

        produced = staging / f"{docx_path.stem}.pdf"
        if produced.is_file() and produced.stat().st_size > 0:
            # نسخٌ إلى جوار الهدف ثم ``os.replace``: المجلد المؤقّت قد يقع
            # على قرصٍ آخر (فلا يصحّ replace مباشرةً)، والاستبدال الأخير
            # ذرّي — مجلد المهمة قد يحمل PDF من تشغيل سابق، وانقطاعٌ في
            # المنتصف لا يجوز أن يترك نصف ملف مكانه.
            partial = target.with_name(target.name + ".part")
            try:
                shutil.copyfile(produced, partial)
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
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
