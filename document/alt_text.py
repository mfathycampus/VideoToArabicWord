"""النصّ البديل للصور — وصولية، ومتطلّب شراء قبل أن يكون فضيلة.

**لماذا هذا الملفّ ليس تحسينًا اختياريًّا.** دوائر حكومية وجامعات كثيرة
تشترط في مشترياتها امتثالًا لـ WCAG 2.1 المستوى AA، والبند 1.1.1 فيه
صريح: كل محتوى غير نصّي له بديل نصّي. مستندٌ فيه ثلاثون لقطة شاشة بلا
نصّ بديل يسقط في أول مراجعة امتثال — وسقوطه ليس عيبًا تقنيًّا يُصلَح
لاحقًا، بل سببُ استبعادٍ من قائمة المورّدين.

**وهو مجّانيّ هنا بالذات.** الأداة تملك أصلًا ما يصنع نصًّا بديلًا
حقيقيًّا لا حشوًا: نصّ الشاشة المستخرَج بـ OCR هو **حرفيًّا** ما يقرؤه
المبصر في الصورة. أي أن أدقّ نصّ بديل ممكن مقروءٌ سلفًا ومحفوظ في
``KeyframeMetadata.ocr_text``، ولم يكن يُستعمل إلا في البحث.

**وترتيب المصادر مقصود:**

1. **نصّ الشاشة** — هو ما في الصورة فعلًا، لا ما قيل عندها.
2. **التسمية الوصفية** — محسوبة من الكلام المصاحب، أضعف لكنها وصف.
3. **موضع اللقطة وتوقيتها** — آخر ما يبقى. وهو أفضل من ``"image1.jpg"``
   الذي يقرؤه قارئ الشاشة اليوم، وأفضل من نصٍّ بديل فارغ يجعل الصورة
   كأنها غير موجودة.

**ولا نكتب نصًّا بديلًا مخترَعًا.** «صورة توضيحية للمحتوى» جملةٌ تمرّ
على المدقّق الآلي وتخذل الأعمى — وهي أسوأ من غيابها لأنها تُخفي النقص.
"""
from __future__ import annotations

import re
from typing import Optional

from utils.timestamps import seconds_to_display

#: النصّ البديل يُقرأ بصوت متّصل. الطويل منه يُرهق أكثر مما يفيد،
#: وتوصية WCAG العملية أن يكون موجزًا وأن يُحال التفصيل إلى النصّ.
MAX_ALT_CHARS = 220
#: نصّ شاشة أقصر من هذا غالبًا بقايا OCR (حرفان على زرّ)، لا محتوى.
MIN_OCR_CHARS = 6

_WHITESPACE = re.compile(r"\s+")
#: أسطر OCR المتقطّعة تُدمج بفاصل مرئيّ: قارئ الشاشة يقرأ سطرًا
#: بعد سطر بلا وقفة، فتلتصق الكلمات في جملة بلا معنى.
_LINE_JOIN = " · "


def _tidy(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def _from_screen(ocr_text: str) -> str:
    lines = [_tidy(line) for line in (ocr_text or "").splitlines()]
    lines = [line for line in lines if len(line) >= 2]
    joined = _LINE_JOIN.join(lines)
    return joined if len(joined) >= MIN_OCR_CHARS else ""


def _clip(text: str, limit: int = MAX_ALT_CHARS) -> str:
    text = _tidy(text)
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip(" ·،,") + "…"


def describe(block, number: Optional[int] = None) -> str:
    """نصّ بديل لكتلة شكل. لا يعيد فراغًا أبدًا.

    ``number`` رقم الشكل في المستند إن عُرف — يجعل البديل الأخير
    («لقطة الشكل 3 عند 00:12:40») موضعًا يمكن للقارئ الرجوع إليه، لا
    مجرّد توقيت معلّق.
    """
    screen = _from_screen(getattr(block, "ocr_text", "") or "")
    caption = _tidy(getattr(block, "caption", "") or "")
    timestamp = getattr(block, "timestamp", None)

    if screen and caption:
        # الاثنان معًا حين يوجدان: الوصف يقول ما اللقطة، والنصّ يقول
        # ما فيها. وأحدهما وحده يترك نصفَ المعنى.
        return _clip(f"{caption} — النصّ الظاهر: {screen}")
    if screen:
        return _clip(f"لقطة شاشة، النصّ الظاهر فيها: {screen}")
    if caption:
        return _clip(caption)

    where = (f" عند {seconds_to_display(timestamp)}"
             if timestamp is not None else "")
    label = f"الشكل {number}" if number else "لقطة"
    # آخر ما يبقى: موضعٌ حقيقي لا وصفٌ مخترَع. «صورة توضيحية للمحتوى»
    # تمرّ على المدقّق الآلي وتخذل الأعمى.
    return _clip(f"لقطة {label} من التسجيل{where} — بلا نصّ ظاهر مستخرَج")
