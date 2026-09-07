"""يولّد ``assets/logo.ico`` من ``assets/logo.png``.

    python tools/make_icon.py

لماذا أداة لا ملفًّا مُودَعًا: الأيقونة مُشتقّة من الشعار، وإيداع
الاثنين يعني نسختين تفترقان بصمت عند أول تحديث للشعار. الأداة تُشغَّل
عند تغيّره، والناتج مستبعَد من المستودع.

**التربيع مقصود.** الشعار لافتة عريضة (669×298). حفظه ICO مباشرةً يُنتج
مدخلات غير مربّعة (16×7، 32×14…) لأن Pillow يحافظ على النسبة — وويندوز
يمطّطها في شريط المهام وقائمة ابدأ فتبدو مشوّهة. نضعه في مربّع شفّاف
بهامش بدل ذلك.

**ولماذا ICO أصلًا:** ‏PyInstaller على ويندوز و Inno Setup كلاهما يشترط
‏``.ico``؛ تمرير PNG يُتجاهَل بصمت (أو يفشل)، فيخرج التطبيق بأيقونة
Python الافتراضية — وهي أول ما يراه المستخدم في قائمة ابدأ.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: الأحجام التي يطلبها ويندوز فعلًا: شريط المهام، قائمة ابدأ، سطح
#: المكتب بأحجام العرض المختلفة، ونافذة الخصائص.
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48),
              (64, 64), (128, 128), (256, 256)]

#: هامش حول الشعار داخل المربّع، كنسبة من الضلع. بلا هامش يلتصق الشعار
#: بحواف الأيقونة فيبدو مقصوصًا عند الأحجام الصغيرة.
PADDING_RATIO = 0.06

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "logo.png"
TARGET = ROOT / "assets" / "logo.ico"


def square_canvas(image, padding_ratio: float = PADDING_RATIO):
    """يضع صورة بأي نسبة في مربّع شفّاف، موسّطة وبهامش."""
    from PIL import Image

    image = image.convert("RGBA")
    side = max(image.size)
    padded = int(side * (1 + padding_ratio * 2))

    scale = min((padded - int(padded * padding_ratio * 2)) / image.width,
                (padded - int(padded * padding_ratio * 2)) / image.height)
    resized = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.LANCZOS)

    canvas = Image.new("RGBA", (padded, padded), (0, 0, 0, 0))
    canvas.paste(resized,
                 ((padded - resized.width) // 2,
                  (padded - resized.height) // 2),
                 resized)
    return canvas


#: تحت هذا الحجم تُستعمل العلامة المختصرة. القياس: عند 24 بكسل تصير
#: أسطر النصّ ثلاث بقع رمادية ويختفي مثلّث التشغيل داخل الحلقة.
#: أيقونةٌ لا تُقرأ في شريط المهام أسوأ من أيقونة أبسط.
SMALL_SIZE_THRESHOLD = 32


def main() -> int:
    from PIL import Image

    try:
        from tools.make_logo import render, render_mark
    except ImportError:
        render = render_mark = None

    if render is None:
        # سقوط إلى الشعار المُودَع: البناء لا يتوقّف على استيراد.
        if not SOURCE.is_file():
            print(f"لا شعار في {SOURCE}")
            return 1
        frames = {size: square_canvas(Image.open(SOURCE))
                  for size, _ in ICON_SIZES}
    else:
        frames = {
            size: square_canvas(
                (render_mark if size < SMALL_SIZE_THRESHOLD else render)(256))
            for size, _ in ICON_SIZES}

    # ‏Pillow تكتب ICO من صورة واحدة بأحجام مشتقّة، فنكتب كل حجم بمصدره
    # ثم ندمج — وإلا ضاع تمييز الصغير عن الكبير.
    largest = frames[max(frames)]
    largest.save(TARGET, format="ICO", sizes=ICON_SIZES,
                 append_images=[frames[size].resize((size, size), Image.LANCZOS)
                                for size, _ in ICON_SIZES])

    written = sorted(Image.open(TARGET).info.get("sizes", []))
    print(f"{TARGET}  —  {len(written)} حجمًا: {written}")
    if any(w != h for w, h in written):
        print("تحذير: خرج حجم غير مربّع — ستبدو الأيقونة ممطوطة")
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
