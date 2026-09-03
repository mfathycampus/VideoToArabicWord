# التثبيت والتشغيل

## الخطوات (Windows / PowerShell)

```powershell
# استنساخ المستودع (أول مرة فقط)
git clone https://github.com/mfathycampus/VideoToArabicWord.git
cd VideoToArabicWord

# 1) بيئة معزولة — تمنع تعارض الحزم مع بقية النظام
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) التبعيات
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3) تحقّق من البيئة قبل التشغيل
python tools\doctor.py

# 4) شغّل
python app.py
```

على macOS / Linux استبدل خطوة التفعيل بـ `source .venv/bin/activate`.

---

## أشهر عطل: `No module named 'exceptions'`

```
File "...\site-packages\docx.py", line 30, in <module>
    from exceptions import PendingDeprecationWarning
ModuleNotFoundError: No module named 'exceptions'
```

**الرسالة مُضلِّلة تمامًا.** لا علاقة لها بوحدة اسمها `exceptions`، ولن
يفيدك `pip install exceptions` (لا وجود لهذه الحزمة أصلًا).

**السبب:** على جهازك حزمة اسمها **`docx`** بدل **`python-docx`**.
الأولى مشروع مهجور من زمن Python 2، تثبّت ملفًا مفردًا `docx.py` في
`site-packages`. هذا الملف يحجب حزمة `python-docx` الصحيحة، ويحاول عند
استيراده الوصول إلى وحدة `exceptions` التي أُزيلت من Python 3.

**الإصلاح:**

```powershell
python -m pip uninstall -y docx
python -m pip install --upgrade --force-reinstall python-docx
python tools\doctor.py
```

> تثبيت `python-docx` وحده **لا يكفي** — يجب إزالة `docx` أولًا، وإلا بقي
> `docx.py` وفاز في ترتيب الاستيراد.

**كيف تتأكد أن الصحيحة مثبّتة:**

```powershell
python -c "import docx; print(docx.__file__)"
```

- سليم: مسار ينتهي بـ `docx\__init__.py` (مجلد)
- خاطئ: مسار ينتهي بـ `docx.py` (ملف مفرد)

---

## أعطال شائعة أخرى

### `Defaulting to user installation because normal site-packages is not writeable`

تثبّت في `site-packages` عام غير قابل للكتابة، فتذهب الحزم إلى مجلد
المستخدم وقد لا يراها المفسّر نفسه. الحل هو البيئة المعزولة في الخطوة 1.

### ثبّتُّ الحزمة ولا تزال «مفقودة»

`pip` في مسارك قد يخصّ مفسّرًا آخر. استخدم دائمًا:

```powershell
python -m pip install <الحزمة>
```

`tools\doctor.py` يطبع أوامر الإصلاح مربوطة بمفسّرك الجاري تحديدًا.

### `ffmpeg` / `ffprobe` غير موجودين

أسهل طريقة — أداة التنزيل التلقائي المرفقة:

```powershell
python tools\setup_ffmpeg.py
```

تنزّل البناء الرسمي وتضع `ffmpeg.exe` و `ffprobe.exe` في `bin\` وتتحقق
من عملهما. لا تحتاج صلاحيات مدير ولا تعديل `PATH`.

البديل عبر winget:

```powershell
winget install -e --id Gyan.FFmpeg
```

ثم **أغلق PowerShell وافتحه من جديد** ليُحدَّث `PATH`.

يدويًا: نزّل من <https://www.gyan.dev/ffmpeg/builds/> وضع الملفين في
مجلد `bin\` داخل المشروع.

**`ffprobe` إلزامي** — حزمة `imageio-ffmpeg` لا تشحنه إطلاقًا (ADR-013).

### تفعيل OCR (نص الشاشة) — اختياري

ميزة «استخراج نص الشاشة من الصور» في الواجهة تحتاج ثنائي **Tesseract**
منفصلًا (مثل ffmpeg تمامًا) — بلا تثبيته يبقى البرنامج يعمل عاديًا،
وتُترك حقول نص الشاشة فارغة في المستند.

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

أو نزّل من <https://github.com/UB-Mannheim/tesseract/wiki> — **فعِّل
حزمة اللغة العربية (Arabic)** ضمن خيارات التثبيت، فهي غير مثبَّتة
افتراضيًا. تحقّق من التثبيت عبر `python tools\doctor.py`.

### `numpy` يرفض التثبيت على Python 3.13/3.14

تأكد أنك تستخدم `requirements.txt` المرفق. النسخ القديمة كانت تقيّد
`numpy<2`، وعجلات numpy 1.x غير موجودة لهذه الإصدارات، كما تشترط
opencv-python 5.x نفسها `numpy>=2`.

---

### العربية تظهر مقلوبة في الطرفية

طرفية ويندوز الكلاسيكية لا تشكّل الحروف العربية ولا تعيد ترتيبها. لهذا
يطبع `doctor.py` كتلة **`COPY AND RUN THESE COMMANDS`** بالإنجليزية في
آخر التقرير — انسخ منها مباشرة. أسماء الحزم والأوامر لاتينية دائمًا
فتبقى مقروءة.

لعرض عربي سليم استخدم **Windows Terminal** بدل الطرفية الكلاسيكية.

---

## توافق إصدارات Python

| الإصدار | الحالة |
|---|---|
| 3.10 – 3.12 | مدعوم ومُختبَر |
| 3.13 – 3.14 | مدعوم (كل التبعيات توفّر عجلات: PyQt6 و opencv بصيغة abi3) |
| 3.9 وأقدم | غير مدعوم |

---

## التشغيل بلا واجهة

```powershell
python -m tools.run_pipeline --video "محاضرة.mp4" --allow-download
```

`--allow-download` يسمح بتنزيل نموذج التفريغ عند أول تشغيل. بدونه يتوقف
البرنامج برسالة توضّح الحجم والمسار بدل التنزيل دون إذنك.
