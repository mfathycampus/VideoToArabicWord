"""سياسة الجهاز — إعدادات يفرضها المسؤول ولا يغيّرها المستخدم.

**المشكلة التي يحلّها هذا الملفّ ليست تقنية.** قسم تقنية المعلومات في
مدرسة أو شركة لا يعترض على أن البرنامج **يستطيع** إرسال نصّ التفريغ إلى
مزوّد سحابي؛ يعترض على أن كل معلّم يستطيع تشغيل ذلك بمربّع اختيار.
والفرق بين «الإعداد الافتراضي آمن» و«الإعداد **مقفل** آمن» هو الفرق بين
رفض التثبيت وقبوله.

**ملفّ على مستوى الجهاز لا على مستوى المستخدم.** يوضع في مكان لا يملك
المستخدم العادي الكتابة فيه، ويُنشر بأداة النشر نفسها التي تنشر
البرنامج (‏GPO أو Intune أو نسخٌ يدوي):

    ويندوز:  %ProgramData%\\VideoToArabicWord\\policy.yaml
    غيره:    /etc/videotoarabicword/policy.yaml

**والسياسة تُطبَّق بعد تحميل إعداد المستخدم فتغلب عليه**، وتُعلَن في
الواجهة: الحقل المقفل يظهر **معطَّلًا مع سبب** لا مخفيًّا. إخفاؤه يجعل
المستخدم يظنّ الميزة معطوبة فيفتح بلاغًا، وإظهاره معطَّلًا يجعله يعرف
أن جهة عمله قرّرت ذلك.

**ولا تُشدَّد السياسة إلا نحو التقييد.** ملفٌّ يقول ``allow_cloud_ai:
true`` لا يُفعِّل شيئًا — يكتفي بعدم المنع. سياسةٌ تستطيع **تشغيل**
إرسال البيانات تصير طريق هجوم: ملفٌّ واحد يُدَسّ في مجلد مشترك يحوّل
أداةً محلّية إلى قناة تسريب.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import logger

POLICY_FILENAME = "policy.yaml"


def policy_path() -> Optional[Path]:
    """مسار ملفّ السياسة إن وُجد. ``VTAD_POLICY`` يتجاوزه للاختبار والنشر."""
    override = os.environ.get("VTAD_POLICY", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        candidate = base / "VideoToArabicWord" / POLICY_FILENAME
    else:
        candidate = Path("/etc/videotoarabicword") / POLICY_FILENAME
    return candidate if candidate.is_file() else None


@dataclass
class Policy:
    """ما قرّره المسؤول. ``None`` في أي حقل = لا رأي، يبقى اختيار المستخدم."""
    #: ``False`` يمنع كل مزوّد يُخرج النصّ من الجهاز، ويُسقط الاختيار
    #: إلى المحلّي. لا يوجد ``True`` مُفعِّل — انظر مقدّمة الوحدة.
    allow_cloud_ai: Optional[bool] = None
    #: يفرض مجلد الحفظ (مجلد مشترك مؤرشَف مثلًا).
    force_output_dir: Optional[str] = None
    #: يُلزم كتابة سجلّ التدقيق ويمنع تعطيله.
    require_audit_log: Optional[bool] = None
    #: يثبّت صيغ الإخراج فلا يبدّلها المستخدم.
    lock_export_formats: Optional[List[str]] = None
    #: يمنع تنزيل النموذج من الأجهزة (تُنشر النماذج مركزيًّا).
    allow_model_download: Optional[bool] = None
    #: نصّ يظهر في الواجهة يقول لمن يسأل: من فرض هذا ولماذا.
    notice: str = ""

    #: أسماء الحقول المقفلة فعلًا — تقرؤها الواجهة لتعطيلها.
    locked: Dict[str, str] = field(default_factory=dict)
    source: str = ""

    @property
    def active(self) -> bool:
        return bool(self.locked)


_EMPTY = Policy()


def load(path: Optional[Path] = None) -> Policy:
    """يقرأ السياسة. أي عطل يعيد سياسة فارغة ولا يمنع التشغيل.

    ملفٌّ تالف يجب ألا يوقف معلّمًا عن عمله — لكنه يُسجَّل بوضوح، لأن
    سياسةً يظنّها المسؤول مفروضة وهي غير مقروءة أخطرُ من لا سياسة.
    """
    path = path or policy_path()
    if path is None:
        return _EMPTY

    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("جذر الملفّ ليس قاموسًا")
    except Exception as exc:
        logger.warning(f"تعذّرت قراءة سياسة الجهاز {path}: {exc}")
        return _EMPTY

    policy = Policy(source=str(path), notice=str(data.get("notice") or ""))
    known = {
        "allow_cloud_ai": bool,
        "force_output_dir": str,
        "require_audit_log": bool,
        "allow_model_download": bool,
    }
    for key, caster in known.items():
        if key in data and data[key] is not None:
            setattr(policy, key, caster(data[key]))
    formats = data.get("lock_export_formats")
    if isinstance(formats, list):
        policy.lock_export_formats = [str(f).strip().lower() for f in formats]

    for key in list(known) + ["lock_export_formats"]:
        if getattr(policy, key) is not None:
            policy.locked[key] = policy.notice or "مفروض بسياسة الجهاز"
    if policy.locked:
        logger.info(f"سياسة جهاز مفعّلة ({path}): "
                    + "، ".join(sorted(policy.locked)))
    return policy


def apply(config, policy: Optional[Policy] = None) -> Policy:
    """يطبّق السياسة على الإعداد المحمَّل ويعيدها.

    تُستدعى **بعد** ``AppConfig.load`` مباشرةً وفي كل مدخل: الواجهة
    وسطر الأوامر ووضع الدفعات. مدخلٌ واحد ينسى الاستدعاء يصير الثغرة
    التي تُبطل السياسة كلّها.
    """
    policy = policy if policy is not None else load()
    if not policy.active:
        return policy

    if policy.allow_cloud_ai is False:
        for section in ("rewrite", "study"):
            settings = getattr(config, section, None)
            if settings is None:
                continue
            if _is_cloud(getattr(settings, "provider", "")):
                logger.info(
                    f"سياسة الجهاز تمنع المزوّدات السحابية — "
                    f"أُسقط «{settings.provider}» في {section} إلى ollama.")
                settings.provider = "ollama"
                # المفتاح يُمسح من الإعداد الفعّال أيضًا: إبقاؤه يعني
                # أن تعطيل السياسة يومًا يُعيد الإرسال بلا قرار جديد.
                settings.api_key = ""

    if policy.require_audit_log is True:
        config.application.audit_log = True

    if policy.force_output_dir:
        config.application.output_dir = Path(policy.force_output_dir)

    if policy.lock_export_formats is not None:
        config.document.export_formats = list(policy.lock_export_formats)

    return policy


def _is_cloud(provider: str) -> bool:
    from utils.audit import CLOUD_PROVIDERS

    return str(provider).strip().lower() in CLOUD_PROVIDERS
