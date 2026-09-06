"""منع الجهاز من النوم أثناء معالجة طويلة.

محاضرة ثلاث ساعات تُفرَّغ في نحو ساعة على معالج عادي. والمعلّم يُشغّل
البرنامج ثم يترك الحاسوب — وهذا بالضبط ما نطلبه منه، إذ لا شيء يستدعي
جلوسه أمامه. ثم تُطبَّق سياسة الطاقة الافتراضية في ويندوز فينام الجهاز
بعد نحو ثلاثين دقيقة، ويُعلَّق التنفيذ في منتصف مرحلة.

الضرر ليس فقدان العمل — المراحل المكتملة محفوظة والاستئناف يعمل — بل
أن المستخدم يعود بعد ساعتين فيجد شريط التقدّم واقفًا حيث تركه تقريبًا،
بلا خطأ ولا تفسير. ويستنتج أن البرنامج بطيء أو معطوب.

النطاق مقصود ضيّق: نمنع **نوم النظام** ولا نمنع إطفاء الشاشة. إبقاء
شاشة حاسوب محمول مضاءة ساعةً بلا حاجة إهدارٌ للبطارية، ولا علاقة له
بإكمال العمل.
"""
from __future__ import annotations

import contextlib
import sys

from utils.logger import logger

#: ثوابت ويندوز من ``winbase.h``.
_ES_CONTINUOUS = 0x80000000        # اجعل الحالة سارية حتى تُغيَّر
_ES_SYSTEM_REQUIRED = 0x00000001   # النظام مشغول: لا تنم


@contextlib.contextmanager
def keep_awake(reason: str = ""):
    """يمنع نوم النظام داخل الكتلة، ويعيد السياسة عند الخروج.

    آمن دائمًا: على غير ويندوز، أو إن رفض النظام الطلب، تمرّ الكتلة كما
    هي. **منع النوم راحة لا شرط صحّة**، فلا يجوز أن يُسقط فشلُه معالجةً
    ناجحة. ولذلك يُبتلع الاستثناء هنا مع تسجيله، وهو من المواضع القليلة
    التي يصحّ فيها ذلك.
    """
    if sys.platform != "win32":
        # ماك ولينكس لهما آليات أخرى (caffeinate، systemd-inhibit)،
        # وجمهور هذا البرنامج على ويندوز. لا نُضيف تبعية لمسار لا يُختبر.
        yield
        return

    import ctypes

    try:
        previous = ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    except Exception as exc:                                # noqa: BLE001
        logger.debug(f"تعذّر منع نوم النظام: {exc}")
        yield
        return

    if previous == 0:                       # النظام رفض الطلب
        logger.debug("رفض النظام طلب منع النوم.")
        yield
        return

    if reason:
        logger.info(f"منع نوم النظام أثناء: {reason}")
    try:
        yield
    finally:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception as exc:                            # noqa: BLE001
            # الحالة تُعاد تلقائيًا عند انتهاء العملية، فلا ضرر دائمًا.
            logger.debug(f"تعذّرت إعادة سياسة الطاقة: {exc}")
