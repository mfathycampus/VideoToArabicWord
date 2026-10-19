"""شعار المخرجات — اختيارٌ واحد يسري على Word وPDF وصفحة الويب والشرائح.

كان للمستند حقلٌ واحد (``logo_path``) وزرّ «اختيار…» مدفونٌ في «خيارات
متقدّمة»، يحفظ **مسار** الصورة كما هو: نُقلت الصورة أو حُذف مجلّد
التنزيلات فعاد الشعار إلى الافتراضي بصمت. وصفحة الويب والشرائح لا
تعرفان الحقل أصلًا، فخرجت مخرجاتُ المهمّة الواحدة بهويّتين.

الآن الاختيار من أربعة:

* ``app`` — علامة معين (الافتراضي).
* ``wordmark`` — علامة معين مع اسمه.
* ``custom`` — شعار المدرسة أو الجهة. تُنسخ الصورة إلى مجلّد إعداد
  البرنامج عند اختيارها، فلا يضيع الشعار إن نُقل الأصل.
* ``none`` — بلا شعار.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from utils.logger import logger

ASSETS = Path(__file__).resolve().parents[1] / "assets"

#: الاختيار ← (التسمية في الواجهة، الملف على أرضية فاتحة، الملف على داكنة).
CHOICES = {
    "app": ("علامة معين", "logo.png", "logo_light.png"),
    "wordmark": ("علامة معين مع الاسم", "logo_wordmark.png", "logo_wordmark_light.png"),
    "custom": ("شعار مؤسّستي (صورة من جهازي)", "", ""),
    "none": ("بلا شعار", "", ""),
}
DEFAULT_CHOICE = "app"
#: أقصى ضلع للشعار المنسوخ: الترويسة تعرضه بعرض 0.55 بوصة والغلاف بـ2.1،
#: فما فوق 1200px وزنٌ في كل مستند بلا فائدة.
MAX_LOGO_PX = 1200


def normalize_choice(choice: Optional[str], logo_path=None) -> str:
    choice = (choice or "").strip().lower()
    if choice in CHOICES:
        if choice == DEFAULT_CHOICE and logo_path:
            # إعدادٌ من إصدارٍ سابق: مسارٌ مخصّص بلا «اختيار».
            return "custom"
        return choice
    return "custom" if logo_path else DEFAULT_CHOICE


def resolve_logo(config, dark: bool = False) -> Optional[Path]:
    """مسار الشعار الذي يُطبع، أو ``None`` (بلا شعار أو ملفٌّ مفقود)."""
    choice = normalize_choice(getattr(config, "logo_choice", None),
                              getattr(config, "logo_path", None))
    if choice == "none":
        return None
    if choice == "custom":
        custom = getattr(config, "logo_path", None)
        if custom and Path(custom).is_file():
            return Path(custom)
        logger.warning(f"شعار المؤسّسة غير موجود ({custom}) — يُستعمل شعار معين.")
        choice = DEFAULT_CHOICE
    _, light, on_dark = CHOICES[choice]
    path = ASSETS / (on_dark if dark and on_dark else light)
    return path if path.is_file() else None


def branding_dir() -> Path:
    from config.settings import default_config_path

    return default_config_path().parent / "branding"


def import_custom_logo(source: Path, directory: Optional[Path] = None) -> Path:
    """ينسخ صورة الشعار إلى مجلّد الإعداد PNG ويعيد مسار النسخة.

    يرفع ``ValueError`` برسالةٍ عربية إن لم تكن صورةً تُقرأ.
    """
    from PIL import Image

    target_dir = Path(directory or branding_dir())
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.load()
            mode = "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
            image = image.convert(mode)
    except Exception as exc:                          # noqa: BLE001
        raise ValueError(f"تعذّرت قراءة الصورة: {Path(source).name}") from exc
    if image.width < 16 or image.height < 16:
        raise ValueError("الصورة أصغر من أن تكون شعارًا.")
    bbox = image.getbbox() if mode == "RGBA" else None
    if bbox:
        image = image.crop(bbox)          # الحواشي الشفّافة تصغّر الشعار المطبوع
    image.thumbnail((MAX_LOGO_PX, MAX_LOGO_PX))
    # اسمٌ جديد لكل اختيار: Word وصفحة الويب قد يحتفظان بنسخةٍ قديمة
    # بالاسم نفسه، ونسخةٌ سابقة لا تُحذف قبل نجاح الجديدة.
    import hashlib
    digest = hashlib.sha1(image.tobytes()).hexdigest()[:10]
    target = target_dir / f"logo-{digest}.png"
    image.save(target, "PNG")
    for old in target_dir.glob("logo-*.png"):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass
    return target


def logo_data_uri(config, dark: bool = False) -> str:
    """الشعار مضمَّنًا لصفحة الويب — الصفحة ملفٌّ واحد يُرسل كما هو."""
    path = resolve_logo(config, dark=dark)
    if path is None:
        return ""
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{data}"
