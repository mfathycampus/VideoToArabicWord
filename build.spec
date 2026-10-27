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
import sys
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
for name in ("logo.png", "logo_light.png", "logo_wordmark.png",
             "logo_wordmark_light.png"):
    asset = PROJECT / "assets" / name
    if asset.is_file():
        datas.append((str(asset), "assets"))

# Tesseract مشحونًا (‏``tools/setup_tesseract.py``): تمويه أسماء الطلاب
# وقصّ شريط المتصفّح يحتاجانه، والمعلّم لا يُطلب منه تثبيت شيء.
# غيابه عند البناء لا يُسقطه — يعمل البرنامج بلا OCR كما كان.
_tesseract = PROJECT / "bin" / "tesseract"
if _tesseract.is_dir():
    datas.append((str(_tesseract), "tesseract"))

# معرّف البناء (``utils/build_info``): رأس git وقت البناء، يُشحن ملفًّا
# فيعرف ``audit.jsonl`` أيّ نسخةٍ من الكود أنتجت كل مخرج.
try:
    import subprocess as _sp
    _build = _sp.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT,
                     capture_output=True, text=True, timeout=10).stdout.strip()
    if _build:
        # ‏``--ignore-cr-at-eol``: نسخة ويندوز تُظهر كل ملف معدَّلًا بنهايات
        # الأسطر وحدها، فتصير كل حزمة «dirty» بلا تعديل حقيقي.
        _dirty = _sp.run(["git", "diff", "--ignore-cr-at-eol", "--name-only", "HEAD"],
                         cwd=PROJECT, capture_output=True, text=True,
                         timeout=20).stdout.strip()
        _build_file = PROJECT / "build" / "BUILD_ID"
        _build_file.parent.mkdir(parents=True, exist_ok=True)
        _build_file.write_text(_build + ("-dirty" if _dirty else ""),
                               encoding="utf-8")
        datas.append((str(_build_file), "."))
except Exception:
    pass  # بلا git: يُسجَّل «unknown» ولا يتوقّف البناء

# أيقونة الملف التنفيذي. ‏PyInstaller على ويندوز يقبل ``.ico`` فقط —
# تمرير PNG يُتجاهَل بصمت، فيخرج التطبيق بأيقونة Python الافتراضية، وهي
# أول ما يراه المستخدم في قائمة ابدأ. تُولَّد بـ ``tools/make_icon.py``
# ومستبعَدة من المستودع (مُشتقّة من الشعار).
icon = PROJECT / "assets" / "logo.ico"
# macOS: ``.icns`` (يُولَّد بـ ``tools/make_icon.py --icns`` على ماك).
icon_icns = PROJECT / "assets" / "logo.icns"

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
# شرائح العرض: قالب python-pptx الافتراضي ملفٌّ يُحمَّل بالاسم وقت التشغيل.
datas += collect_data_files("pptx")

# tokenizers الخاص بـ faster-whisper يحمّل موارد بالاسم وقت التشغيل
hiddenimports = collect_submodules("faster_whisper") + collect_submodules("pptx") + [
    "recording.session",                    # تسجيل الشاشة (استيراد متأخر من الواجهة)
    "screenguide", "screenguide.windows_source", "screenguide.guide",
    "screenguide.infographic", "ui.screenguide_dialog",  # «سجّلني» (استيراد متأخر)
    "ui.dashboard", "core.stats", "utils.stats_report",  # الإحصاءات (استيراد متأخر)
    "cryptography.hazmat.bindings._rust",   # التحقّق من الترخيص
    "licensing.ed25519_pure",               # تحقّقٌ احتياطي بلا امتداد مُترجَم
    "ctranslate2",
    "tokenizers",
    "av",
]

# ‏cryptography كاملة: الاستيراد داخل دالة في ``licensing/signing.py``،
# والامتداد ``_rust`` يحمّل وحداتٍ فرعية بالاسم. رُصد على جهاز معلّم
# نظيف: التفعيل رُفض و طُلب منه «pip install cryptography». التحقّق الآن
# يسقط إلى ``licensing/ed25519_pure`` إن لم تتحمّل، لكن الحزمة تشحنها
# كاملةً على أيّ حال، ويفحصها ``--selftest`` في كل بناء.
try:
    hiddenimports += collect_submodules("cryptography")
    binaries += collect_dynamic_libs("cryptography")
except Exception:                       # noqa: BLE001
    pass

# ‏_rust.pyd امتدادُ abi3 يرتبط بـ ``python3.dll`` لا بـ ``python312.dll``.
# إن غاب الأوّل من الحزمة فشل التحميل على جهازٍ بلا Python — ونجح على
# جهاز المطوّر لأن ``python3.dll`` في PATH عنده. يُشحن صراحةً.
if sys.platform == "win32":
    _stable_abi = Path(sys.base_prefix) / "python3.dll"
    if _stable_abi.is_file():
        binaries.append((str(_stable_abi), "."))

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
    icon=(str(icon) if icon.is_file() else None)
    if sys.platform != "darwin" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideoToArabicWord",
)

# macOS: حزمة ‎.app بجانب مجلد onedir (الأخير لفحص build_smoke).
if sys.platform == "darwin":
    from version import APP_VERSION

    app = BUNDLE(
        coll,
        name="VideoToArabicWord.app",
        icon=str(icon_icns) if icon_icns.is_file() else None,
        bundle_identifier="com.mfathycampus.videotoarabicword",
        info_plist={
            "CFBundleName": "VideoToArabicWord",
            "CFBundleDisplayName": "VideoToArabicWord",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
