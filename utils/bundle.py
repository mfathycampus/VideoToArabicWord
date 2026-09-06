"""أين تعيش الملفات المشحونة مع التطبيق — مصدر واحد للحقيقة.

سبب وجود هذا الملف: كان الجواب مكتوبًا في مكانين بصيغتين مختلفتين.
``utils/media_probe.py`` يعرف الحزمة المجمّدة ويقرأ ``sys._MEIPASS``،
بينما ``utils/deps.py`` كان يبني المسار من موقع ملفه:

    Path(__file__).resolve().parents[1] / "bin"

داخل حزمة PyInstaller يصير ذلك ``_internal/bin`` — مجلد لا وجود له،
لأن ``build.spec`` يضع ffmpeg وffprobe في جذر ``_internal`` مباشرة.
فيفشل فحص الثنائيات على **كل** جهاز لا يملك ffmpeg في ``PATH``،
ويُرفَع ``SystemExit`` قبل أن توجد نافذة Qt، والبناء بلا طرفية:
المستخدم ينقر مرّتين ولا يحدث شيء إطلاقًا — لا نافذة ولا رسالة.

الوحدة هنا مقصودة الخفّة: لا تستورد شيئًا من المشروع، فيمكن لأبكر
فحص في دورة الحياة أن يستدعيها بلا أن يجرّ معه نصف التطبيق.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: جذر المشروع في التشغيل من المصدر (مجلد ``utils`` هذا يقع تحته).
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    """هل نعمل داخل حزمة PyInstaller؟"""
    return bool(getattr(sys, "frozen", False))


def bundled_dir() -> Path:
    """المجلد الذي تُشحن فيه الثنائيات (ffmpeg / ffprobe).

    * في الحزمة المجمّدة: ``sys._MEIPASS`` — وهو ``_internal`` في نمط
      onedir لـ PyInstaller 6، حيث تضع ``build.spec`` الثنائيات بوجهة
      ``"."``. يُسقَط إلى مجلد الملف التنفيذي إن غاب المتغيّر (نمط
      onefile في إصدارات قديمة).
    * من المصدر: ``bin/`` بجانب جذر المشروع.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return SOURCE_ROOT / "bin"


def binary_name(stem: str) -> str:
    """اسم الملف التنفيذي على المنصّة الحالية."""
    return f"{stem}.exe" if sys.platform == "win32" else stem
