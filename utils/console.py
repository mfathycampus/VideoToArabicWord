"""ضمان أن الطرفية تقبل النص العربي على ويندوز.

سبب وجود هذا الملف: كل رسائل الأدوات هنا عربية، و‏Python على ويندوز
يختار ترميز الطرفية من صفحة الترميز النشطة — وهي ``cp1252`` على
الإعداد الإنجليزي الافتراضي، ولا تحوي حرفًا عربيًا واحدًا. النتيجة أن
``print`` نفسه يرمي::

    UnicodeEncodeError: 'charmap' codec can't encode characters ...

فينهار ``python tools/doctor.py`` — وهو الأمر الذي يطلبه README
و‏INSTALL من المستخدم قبل أي شيء آخر — بأثر استدعاء مخيف، بلا علاقة
بأي مشكلة حقيقية في بيئته. وقع هذا فعلًا على متتبّعات ويندوز في CI.

الإصلاح ليس ``PYTHONIOENCODING`` في البيئة (لا يملكه المستخدم ولا
يُنقل مع المشروع) بل إعادة ضبط المجرى نفسه عند بدء كل أداة.
"""
from __future__ import annotations

import sys


def enable_utf8_console() -> None:
    """اجعل stdout/stderr يقبلان UTF-8، بلا انهيار إن تعذّر ذلك.

    ``errors="replace"`` مقصود: طرفية قديمة لا تعرض العربية أفضل من
    أداة تنهار. و‏``reconfigure`` متاح منذ 3.7؛ المجاري المُعاد توجيهها
    في الاختبارات قد لا تملكه، فيُتجاهل بصمت.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):       # مجرى مغلق أو غير قابل للضبط
            pass
