"""«سجّلني — دليل خطوة بخطوة»: تسجيل إجراءٍ في أي برنامج على الشاشة.

تنفّذ العمل بنفسك — في Excel أو Outlook أو نظامٍ تفتحه في متصفّحك أو
إعدادات ويندوز — وكل نقرة تصير خطوةً مرقّمة بصورتها، والزرّ مسمّى باسمه في
البرنامج نفسه. ثم يُكتب دليلٌ إجرائي مصوّر (Word وPDF وصفحة ويب) وإنفوجرافيك
لكل إجراء، بلا نموذج لغوي وبلا رفع أي شيء.

منقولٌ من خاصية *Follow me — My whole screen* في WebDoc.

ويندوز وحده مدعوم. ``MOEEN_SCREEN_GUIDE=0`` يعطّل الخاصية كلّها (قرار مسؤول:
الصور تُظهر كل ما على الشاشة)، وكذلك سياسة الجهاز ``allow_screen_guide: false``.
"""
from __future__ import annotations

import os
import sys
from typing import Any


def screen_guide_supported() -> bool:
    """هل يستطيع هذا الجهاز تسجيل الشاشة كاملةً خطوةً خطوة؟"""
    if os.environ.get("MOEEN_SCREEN_GUIDE", "").strip() == "0":
        return False
    return sys.platform == "win32"


def open_screen_recorder(*, language: str = "ar", title: str = "") -> Any:
    """يبدأ مراقبة الشاشة ويعيد ``ScreenEventSource``.

    يرفع ``RuntimeError`` إن كان الجهاز لا يستطيع تسجيل شاشته.
    """
    if not screen_guide_supported():
        raise RuntimeError("screen_guide_unsupported")
    from screenguide.windows_source import WindowsScreenRecorder

    recorder = WindowsScreenRecorder(language=language, title=title)
    recorder.start()
    return recorder
