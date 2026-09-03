"""تحويل أخطاء التطبيق إلى رموز ورسائل قابلة للعرض للمستخدم."""
from __future__ import annotations

from core.exceptions import AppBaseException


def describe_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, AppBaseException):
        return {"code": exc.error_code, "category": exc.category,
                "message": exc.user_message()}
    return {"code": "E9999", "category": "INTERNAL_ERROR",
            "message": "حدث خطأ داخلي غير متوقع. راجع سجل التشخيص."}


def format_error_for_user(exc: BaseException) -> str:
    """رسالة معروضة في الواجهة/سطر الأوامر عند فشل عملية.

    لأخطاء التطبيق المصنَّفة (``AppBaseException``) تُعرض برمزها الآلي
    مع رسالتها العربية — لا رسالة داخلية عامة تُخفي التفاصيل، فالرسالة
    نفسها مصمَّمة أصلًا لتُعرض للمستخدم. لأي استثناء آخر (خطأ برمجي غير
    متوقع) يبقى السلوك كما كان: نص الاستثناء كما هو، لأن ``describe_error``
    يستبدله برسالة عامة لا تفيد أحدًا هنا، والتفصيل الكامل مسجَّل بالفعل
    في السجلّ عبر ``logger.exception``.
    """
    if isinstance(exc, AppBaseException):
        info = describe_error(exc)
        return f"[{info['code']}] {info['message']}"
    return str(exc)
