"""فحص وجود إصدار أحدث — **بطلب المستخدم وحده**.

سبب وجود هذا الملف أن التحديث لم يكن يصل. البرنامج يُبنى ويُوسم ويُنتج
مثبِّتًا، ثم يقف: مَن يملك نسخةً قديمة لا يملك وسيلةً يعلم بها أن أحدث
منها صدرت. وكل إصلاحٍ في هذا المستودع — بما فيه إصلاحات تُفسد المستند
إن غابت — كان يصل إلى المستخدم **الجديد** وحده. وهو معكوس الترتيب
الصحيح، والدرس نفسه الذي أنتج ``drop_superseded_defaults``.

**وحدود هذا الملف أهمّ من وظيفته.** ‏ADR-014 يقول إن الاتصال الشبكي
الوحيد في البرنامج يشترط إذنًا صريحًا، والصفحة الأولى تَعِد المستخدم
بأنه «يعمل على جهازك بالكامل — بلا إنترنت». ففحصٌ تلقائي عند الإقلاع
كان سيخالف الوعد المكتوب في الواجهة نفسها، ويرسل أثرًا (‏IP ووقت
التشغيل) إلى خادم في كل مرّة يفتح فيها معلّمٌ برنامجَه.

لذلك:

* لا شيء هنا يعمل عند الاستيراد. ``check_for_update`` دالّةٌ تُستدعى،
  ولا مؤقّت ولا خيط يستدعيها من تلقائه.
* الطلب مجهول: لا معرّف جهاز ولا إصدار ولا أي شيء عن المستخدم في
  الطلب. ما يُرسَل هو ``GET`` على عنوانٍ عامّ، والمقارنة تقع **هنا**
  على الجهاز لا هناك.
* لا تنزيل ولا تثبيت تلقائيّ. أقصى ما يفعله البرنامج أن يقول «صدر
  1.6.0» ويفتح الصفحة في المتصفّح. ما يُثبَّت على جهاز المستخدم
  يقرّره المستخدم.
* لا يرفع استثناءً أبدًا. جهازٌ بلا إنترنت — وهو الحال الطبيعي
  المقصود لهذا البرنامج — يجب أن يُنتج رسالةً هادئة لا انهيارًا.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from utils.logger import logger
from version import APP_VERSION

REPO = "mfathycampus/VideoToArabicWord"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

#: ثوانٍ. قصيرٌ عمدًا: المستخدم ضغط زرًّا وينتظر، وجهازٌ بلا إنترنت
#: يجب أن يقول ذلك سريعًا لا أن يجمّد الواجهة.
DEFAULT_TIMEOUT = 6.0

_NUMBER = re.compile(r"\d+")


def parse_version(text: str) -> tuple[int, ...]:
    """‏``v1.5.0`` و ``1.5.0`` و ``1.5.0-beta`` كلها ← ``(1, 5, 0)``.

    المقارنة النصّية المباشرة كانت ستقول إن ``1.10.0`` أقدم من
    ``1.9.0`` — وهو عطلٌ لا يظهر إلا بعد عشرة إصدارات ثانوية، أي حين
    لا يعود أحد يذكر هذا السطر.
    """
    parts = tuple(int(n) for n in _NUMBER.findall(text or ""))
    return parts or (0,)


def is_newer(latest: str, current: str) -> bool:
    """هل ``latest`` أحدث فعلًا من ``current``؟"""
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


@dataclass(frozen=True)
class UpdateCheck:
    """نتيجة فحصٍ واحد. الفشل قيمةٌ لا استثناء."""

    current: str
    latest: str = ""
    page_url: str = RELEASES_PAGE
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.latest) and is_newer(self.latest, self.current)

    @property
    def message(self) -> str:
        """نصٌّ جاهز للعرض — الواجهة لا تصوغ الرسائل بنفسها."""
        if self.error:
            return self.error
        if not self.latest:
            return "لم يُعثر على أي إصدار منشور بعد."
        if self.available:
            return (f"صدر الإصدار {self.latest} — وأنت على {self.current}.\n\n"
                    "التحديث تثبيتٌ فوق القديم: إعداداتك والنماذج "
                    "المنزَّلة تبقى كما هي.")
        return f"أنت على أحدث إصدار ({self.current})."


def check_for_update(current: str = APP_VERSION,
                     timeout: float = DEFAULT_TIMEOUT,
                     opener=None) -> UpdateCheck:
    """يسأل GitHub عن أحدث إصدار منشور. لا يرفع استثناءً أبدًا.

    ``opener`` موجود للاختبار وحده — حقنُه أرخص من تشغيل خادم، ويمنع
    اختبارًا يعتمد على الشبكة فيخضرّ أو يحمرّ بحسب حالها لا بحسب
    الشيفرة.
    """
    fetch = opener or urllib.request.urlopen
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub يردّ 403 على طلبٍ بلا هوية عميل. والاسم هنا اسم
            # البرنامج لا اسم المستخدم — لا شيء يخصّ الجهاز.
            "User-Agent": f"VideoToArabicWord/{current}",
        },
    )
    try:
        with fetch(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # مستودعٌ بلا إصدارات منشورة بعد — ليس عطلًا.
            return UpdateCheck(current=current)
        logger.info("فحص التحديث: ردّ %s من GitHub", exc.code)
        # ‏403 مع رصيدٍ منتهٍ يعني حدّ المعدّل لا عطلًا: طلبٌ بلا مفتاح
        # له ستّون طلبًا في الساعة **لكل عنوان IP**. ومدرسةٌ كاملة خلف
        # بوّابة واحدة تشترك في العنوان نفسه — فقد يستهلكه زميلٌ آخر.
        # ورسالة «تعذّر سؤال GitHub» هنا كانت ستدفع المعلّم إلى ظنّ أن
        # البرنامج معطوب.
        remaining = (exc.headers or {}).get("x-ratelimit-remaining")
        if exc.code == 403 and remaining == "0":
            return UpdateCheck(
                current=current,
                error="‏GitHub يحدّ من عدد الطلبات من شبكتك مؤقّتًا.\n\n"
                      "جرّب بعد ساعة، أو افتح صفحة الإصدارات في "
                      "المتصفّح مباشرة.")
        return UpdateCheck(
            current=current,
            error=f"تعذّر سؤال GitHub (ردّ {exc.code}). جرّب لاحقًا، "
                  "أو افتح صفحة الإصدارات في المتصفّح مباشرة.")
    except Exception as exc:  # شبكة، مهلة، JSON تالف — كلها سواء هنا
        logger.info("فحص التحديث تعذّر: %s", exc)
        return UpdateCheck(
            current=current,
            error="تعذّر الاتصال بالإنترنت للتحقّق من التحديثات.\n\n"
                  "هذا لا يؤثّر على عمل البرنامج إطلاقًا — "
                  "المعالجة كلها تجري على جهازك.")

    tag = str(payload.get("tag_name") or "").strip()
    page = str(payload.get("html_url") or "").strip() or RELEASES_PAGE
    if not tag:
        return UpdateCheck(current=current, page_url=page)
    return UpdateCheck(current=current, latest=tag.lstrip("vV"), page_url=page)
