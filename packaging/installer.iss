; مثبِّت ويندوز — Inno Setup 6
;
; سبب وجوده: تسليم مجلّد مضغوط يعني أن كل معلّم يواجه SmartScreen
; و Defender وهو يفكّ أرشيفًا لا يفهم محتواه، ثم يبحث عن ملف تنفيذي
; بين مئات الملفات. أسوأ انطباع أول ممكن لبرنامج يُفترض أن يوفّر وقته.
;
; قرارات مقصودة:
;   * PrivilegesRequired=lowest — تثبيت لكل مستخدم، بلا طلب صلاحيات
;     مدير. أجهزة المدارس كثيرًا ما تمنعها، ولا نحتاجها: لا خدمة ولا
;     تعريف ولا كتابة في Program Files.
;   * لا ارتباط بامتدادات الفيديو — البرنامج ليس مشغّلًا، وخطف ارتباط
;     mp4 من مشغّل المستخدم عدوانٌ لا ميزة.
;   * ArchitecturesAllowed=x64 — ctranslate2 لا يشحن عجلات 32-بت.
;
; البناء:
;   pyinstaller build.spec --noconfirm
;   iscc packaging\installer.iss /DAppVersion=1.4.0

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName        "محوّل الفيديو إلى Word"
#define AppNameLatin   "VideoToArabicWord"
#define AppPublisher   "Mohammed Yosef"
#define AppExe         "VideoToArabicWord.exe"

[Setup]
AppId={{8F3C2A91-6D45-4B7E-9C18-2A5E7D4B1F03}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

; تثبيت لكل مستخدم — لا صلاحيات مدير
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppNameLatin}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no

; ctranslate2 بلا عجلات 32-بت
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist
OutputBaseFilename={#AppNameLatin}-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\logo.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

; الحزمة ~2GB بعد النموذج؛ نطلب هامشًا معقولًا قبل أن نبدأ
ExtraDiskSpaceRequired=0

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; مجلد PyInstaller كاملًا — التنفيذي و_internal بما فيه ffmpeg وffprobe
Source: "..\dist\{#AppNameLatin}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; \
  Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; مخلّفات PyInstaller وذاكرة Python المؤقتة — لا تُترك بعد الإزالة.
; نماذج Whisper المنزَّلة تبقى في مجلد المستخدم عمدًا: 1.7GB لا نريد
; إعادة تنزيلها إن أعاد التثبيت، وليست من إنتاج المثبِّت أصلًا.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: dirifempty; Name: "{app}"
