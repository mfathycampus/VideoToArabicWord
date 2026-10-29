"""نصّ الموافقة قبل التشغيل — مبنيٌّ من الإعداد الفعلي لا مكتوبٌ ثابتًا.

رُصد (1.16.3): رسالة «تأكيد الإرسال إلى خدمة خارجية» تقول «الفيديو والصوت
والصور لا تُرسل إطلاقًا»، والوضع المرئي مفعّل افتراضيًّا يرسل اللقطات. في
تسجيلٍ حقيقي وافق المستخدم على تلك الرسالة ثم أُرسلت 87 لقطة شاشة.
الرسالة كُتبت قبل الوضع المرئي ولم تُحدَّث معه — فصارت هنا دالّةً تُختبر.
"""
from __future__ import annotations

from typing import Optional


def describe_egress(config, rewrite_label: str,
                    study_label: Optional[str] = None) -> str:
    """ما سيغادر الجهاز في هذه المهمّة، وما لن يغادره — بالعربية.

    ``rewrite_label`` اسم مزوّد الصياغة كما يراه المستخدم؛ ``study_label``
    اسم مزوّد الحزمة التعليمية إن كانت سحابية ومفعّلة، وإلا ``None``.
    """
    rewrite = config.rewrite
    document = config.document
    images = bool(getattr(rewrite, "send_images", False))
    anonymize = bool(getattr(document, "anonymize_names", False))
    withhold = bool(getattr(document, "withhold_sensitive_screens", True))

    lines = [f"سيُرسل إلى «{rewrite_label}»:",
             "• نصّ التفريغ كاملًا، لإعادة صياغته."]
    if images:
        lines.append("• لقطات الشاشة المختارة (الوضع المرئي) مع النص.")
        if anonymize:
            lines.append("  تُطمس فيها قبل الإرسال: أسماء الأشخاص والبريد، وصفوف "
                         "أرقام الهوية، ومسارات الملفات، وما بعد اسم الموقع في "
                         "الروابط. والطمس تقديري وقد يفوته شيء.")
        else:
            lines.append("  دون طمس — «إخفاء الأسماء» معطّل.")
        if withhold:
            lines.append("  شاشات تسجيل الدخول لا تُرسل ولا تدخل المستند.")
    if study_label:
        lines.append(f"• نصّ التفريغ أيضًا إلى «{study_label}» لبناء الحزمة التعليمية.")
    lines.append("")
    lines.append("لا يُرسل الفيديو ولا الصوت.")
    lines.append("النص الخام يبقى على جهازك" + (
        "، والصور في المستند هي النسخ المطموسة نفسها." if images and anonymize
        else "."))
    if images:
        lines.append("لإرسال النص وحده: ألغِ «كتابة المستند من لقطات الشاشة».")
    lines.append("")
    lines.append("هل توافق؟")
    return "\n".join(lines)
