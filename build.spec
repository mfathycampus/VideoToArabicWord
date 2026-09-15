# -*- mode: python ; coding: utf-8 -*-
"""وصفة التغليف بـ PyInstaller.

    pyinstaller build.spec --noconfirm

قرارات مقصودة:

* **ffmpeg و ffprobe مشحونان** (ADR-013). ``imageio-ffmpeg`` لا يشحن
  ffprobe إطلاقًا، والاعتماد عليه يُفشل قراءة الميتاداتا من أول دالة.
  ‏``utils.media_probe._bundled_dir`` يقرأ من ``sys._MEIPASS`` عند
  التجميد، فالمسار يعمل داخل الحزمة وخارجها بلا فرع خاص.
* **بلا PyTorch** (ADR-012). ``ctranslate2`` يوفّر اكتشاف CUDA وأنواع
  الحساب. ``torch`` و ``transformers`` مستبعدان صراحةً حتى لا يسحبهما
  محلّل الاستيراد عبر محرّك Cohere الاختياري.
* **بلا نموذج Whisper داخل الحزمة**. النموذج ~1.7GB ويُنزَّل بإذن
  المستخدم مرة واحدة إلى مخبأ المستخدم (ADR-014). حشره في الحزمة يخالف
  القرار ويضاعف حجم المثبِّت.
* ``console=False`` لتطبيق نافذة، مع بقاء السجلّ في ملف ``app.log``.

قبل البناء: ``python tools/setup_ffmpeg.py`` لتعبئة ``bin/``.
"""
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files,
                                     collect_dynamic_libs,
                                     collect_submodules)

PROJECT = Path(SPECPATH)

# --- الثنائيات المشحونة ---------------------------------------------------
binaries = []
for name in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
    candidate = PROJECT / "bin" / name
    if candidate.is_file():
        binaries.append((str(candidate), "."))

# مكتبات ctranslate2 المشتركة لا يلتقطها المحلّل وحده
binaries += collect_dynamic_libs("ctranslate2")

# --- الموارد --------------------------------------------------------------
datas = []
# ‏logo.png ترويسةُ المستند، وlogo_light.png شريطُ الواجهة الداكن.
# شحن الأوّل وحده هو ما يجعل الشعار يختفي في الحزمة بينما يظهر عند
# التشغيل من المصدر — عطلٌ لا يراه أحد إلا المستخدم النهائي.
for name in ("logo.png", "logo_light.png"):
    asset = PROJECT / "assets" / name
    if asset.is_file():
        datas.append((str(asset), "assets"))

# أيقونة الملف التنفيذي. ‏PyInstaller على ويندوز يقبل ``.ico`` فقط —
# تمرير PNG يُتجاهَل بصمت، فيخرج التطبيق بأيقونة Python الافتراضية، وهي
# أول ما يراه المستخدم في قائمة ابدأ. تُولَّد بـ ``tools/make_icon.py``
# ومستبعَدة من المستودع (مُشتقّة من الشعار).
icon = PROJECT / "assets" / "logo.ico"

# نموذج VAD الذي يشحنه faster-whisper ملفُّ بيانات لا وحدةَ بايثون،
# فلا يراه محلّل الاستيراد إطلاقًا. ``collect_submodules`` أدناه يجلب
# الوحدات وحدها، فخرجت الحزمة بـ``faster_whisper`` كاملًا بلا
# ``assets/silero_vad_v6.onnx`` — و``vad_filter`` مفعّل افتراضيًا،
# فكان **كل** تفريغ في الحزمة يسقط بـ:
#
#     [ONNXRuntimeError] : 3 : NO_SUCHFILE : ... silero_vad_v6.onnx
#     failed. File doesn't exist
#
# بعد أن ينتظر المعلّم استخراج الصوت. والتعليق تحت هذا السطر كان يقول
# بالحرف إن faster-whisper «يحمّل موارد بالاسم وقت التشغيل» — عرفنا
# القاعدة وشحنّا الوحدات وحدها.
datas += collect_data_files("faster_whisper")

# tokenizers الخاص بـ faster-whisper يحمّل موارد بالاسم وقت التشغيل
hiddenimports = collect_submodules("faster_whisper") + [
    "ctranslate2",
    "tokenizers",
    "av",
]

# --- ما لا يُشحن ----------------------------------------------------------
excludes = [
    "torch", "torchaudio", "torchvision",     # ADR-012
    "transformers", "accelerate", "librosa",  # محرّك Cohere الاختياري
    "matplotlib", "scipy.spatial.cKDTree",
    "tkinter", "test", "unittest", "pytest",
    "IPython", "notebook",
]

a = Analysis(
    ["app.py"],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoToArabicWord",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX يفسد بعض مكتبات ctranslate2
    console=False,             # تطبيق نافذة؛ السجلّ في app.log
    disable_windowed_traceback=False,
    icon=str(icon) if icon.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideoToArabicWord",
)
