"""تحويل أخطاء التطبيق إلى رموز ورسائل قابلة للعرض للمستخدم."""
from __future__ import annotations

import errno
from pathlib import Path
from typing import Optional

from core.exceptions import AppBaseException


def describe_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, AppBaseException):
        return {"code": exc.error_code, "category": exc.category,
                "message": exc.user_message()}
    return {"code": "E9999", "category": "INTERNAL_ERROR",
            "message": "حدث خطأ داخلي غير متوقع. راجع سجل التشخيص."}


# ── أخطاء نظام التشغيل ────────────────────────────────────────────────
#
# ‏``str(OSError)`` على ويندوز يعطي نصًّا إنجليزيًّا خامًا مثل:
#
#   [WinError 32] The process cannot access the file because it is being
#   used by another process: 'C:\\Users\\...\\lecture.docx'
#
# والمعلّم الذي يفتح المستند في Word ثم يعيد التشغيل يرى هذا. وهو
# **أشيع فشل متوقّع في البرنامج كلّه**: المستند هو المخرَج، وفتحُه أوّل
# ما يفعله المستخدم بعد أول تشغيل.
#
# الرسائل هنا تقول ثلاثة أشياء: ما الذي فشل، وأي ملف، وما العمل. ولا
# تدّعي سببًا واحدًا حيث يحتمل الخطأ أكثر من سبب.

#: رموز ويندوز التي تعني «الملف مشغول أو ممنوع».
_WIN_ACCESS = {5, 32, 33}          # Access denied / sharing violation / lock
_WIN_DISK_FULL = {39, 112}
_WIN_PATH_TOO_LONG = {206, 3}      # filename too long / path not found


def _target(exc: OSError) -> str:
    """اسم الملف المذكور في الخطأ، أو سلسلة فارغة."""
    name = getattr(exc, "filename", None) or getattr(exc, "filename2", None)
    if not name:
        return ""
    # الفاصلان معًا: الخطأ يقع على ويندوز وقد يُعرض حيث لا يكون «\» فاصلًا،
    # و``Path("C:\\x\\y.docx").name`` على لينكس تُعيد المسار كاملًا.
    # المستخدم يريد اسم الملف لا مساره.
    text = str(name).replace("\\", "/").rstrip("/")
    return Path(text).name or text


def _suffix(exc: OSError) -> str:
    name = _target(exc)
    return f" «{name}»" if name else ""


def describe_os_error(exc: OSError) -> Optional[str]:
    """رسالة عربية قابلة للتنفيذ، أو ``None`` إن لم يُعرف الخطأ.

    ``None`` لا رسالة عامّة: خطأٌ لا نفهمه يُعرض نصّه الأصلي كما هو،
    فذلك أنفع للمستخدم وللتشخيص من «حدث خطأ» تُخفي كل شيء.
    """
    win = getattr(exc, "winerror", None)
    code = exc.errno

    if win in _WIN_ACCESS or code in (errno.EACCES, errno.EPERM):
        return (f"تعذّر الكتابة إلى الملف{_suffix(exc)}. "
                "إن كان مفتوحًا في Word أو برنامج آخر فأغلقه وأعد "
                "المحاولة؛ وإلا فتحقّق من صلاحيتك على المجلد.")

    if win in _WIN_DISK_FULL or code == errno.ENOSPC:
        return ("لا توجد مساحة كافية على القرص لإكمال المعالجة. "
                "أفرغ بعض المساحة ثم أعد المحاولة.")

    if win in _WIN_PATH_TOO_LONG or code == errno.ENAMETOOLONG:
        return ("مسار الحفظ طويل جدًّا على ويندوز. اختر مجلد إخراج "
                "أقصر — مثل C:\\Videos — أو اختصر اسم الملف.")

    if code == errno.EROFS:
        return (f"مجلد الإخراج للقراءة فقط، فتعذّر حفظ الملف{_suffix(exc)}. "
                "اختر مجلدًا آخر.")

    if isinstance(exc, FileNotFoundError):
        return (f"لم يُعثر على الملف{_suffix(exc)}. "
                "قد يكون نُقل أو حُذف أثناء المعالجة.")

    if isinstance(exc, IsADirectoryError):
        return "مسار الإخراج مجلد لا ملف. اختر اسم ملف."

    return None


def format_error_for_user(exc: BaseException) -> str:
    """رسالة معروضة في الواجهة/سطر الأوامر عند فشل عملية.

    لأخطاء التطبيق المصنَّفة (``AppBaseException``) تُعرض برمزها الآلي
    مع رسالتها العربية — لا رسالة داخلية عامة تُخفي التفاصيل، فالرسالة
    نفسها مصمَّمة أصلًا لتُعرض للمستخدم.

    ولأخطاء نظام التشغيل المعروفة تُترجَم إلى عربية تقول ما العمل: لا
    يجوز أن يرى معلّمٌ ‏«[WinError 32] The process cannot access the
    file…» لأنه ترك المستند مفتوحًا في Word.

    وما عدا ذلك يبقى نصّ الاستثناء كما هو: ``describe_error`` يستبدله
    برسالة عامة لا تفيد أحدًا هنا، والتفصيل الكامل مسجَّل في السجلّ
    عبر ``logger.exception``.
    """
    if isinstance(exc, AppBaseException):
        info = describe_error(exc)
        return f"[{info['code']}] {info['message']}"
    if isinstance(exc, OSError):
        described = describe_os_error(exc)
        if described:
            return described
    return str(exc)
