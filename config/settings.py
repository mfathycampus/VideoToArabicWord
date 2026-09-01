from __future__ import annotations

import json
from pathlib import Path
from pydantic import BaseModel, Field


class SceneDetectionConfig(BaseModel):
    """إعدادات كشف المشاهد.

    ملاحظة معايرة مُلزِمة: القيم أدناه مشتقّة من قياس فعلي على انتقالات
    شرائح اصطناعية وحقيقية. لا تُغيَّر بالتخمين — استخدم أداة المعايرة
    ``tools/calibrate_scenes.py`` على عينة من فيديوهاتك.
    """
    analysis_fps: float = Field(3.0, gt=0)
    minimum_gap_seconds: float = Field(3.0, ge=0)

    # الإشارات: كلها مطبّعة إلى [0, 1]
    ssim_weight: float = 0.30
    histogram_weight: float = 0.20
    pixel_weight: float = 0.25
    edge_weight: float = 0.25

    # --- العتبة التكيّفية (تحلّ محل الأرقام المطلقة) ---
    # العتبة = max(absolute_floor, median + k * MAD_scaled)
    adaptive_k: float = Field(6.0, gt=0)
    absolute_floor: float = Field(0.045, ge=0)
    # القطع الحاد: نسبة من العتبة المحسوبة، يتجاوز قاعدة الفجوة الزمنية
    major_multiplier: float = Field(2.5, ge=1.0)
    allow_major_change_before_gap: bool = True

    # دقة التحليل الداخلية (تصغير الإطار للسرعة)
    analysis_width: int = 480
    analysis_height: int = 270
    # عتبة اعتبار البكسل "متغيرًا" ضمن pixel_ratio
    pixel_change_threshold: int = 25

    max_scenes: int = Field(400, gt=0)  # سقف أمان للفيديوهات الطويلة


class KeyframeConfig(BaseModel):
    """اختيار الصور وضغطها.

    الافتراضيات مشتقّة من فحص مخرج حقيقي (تسجيل شاشة 88 ثانية) أنتج
    13 صورة بحجم 8.5 ميجابايت، ثلاث منها فارغة تمامًا.
    """
    # JPEG بدل PNG: نفس التسجيل ينزل من 8.5MB إلى أقل من ميجابايت،
    # ومحاضرة ساعة كانت ستنتج مستندًا يتجاوز 300MB يتعذّر فتحه.
    format: str = "jpg"
    jpg_quality: int = 82
    max_width: int = 1366          # يكفي لقراءة نص الشاشة في مستند مطبوع

    min_brightness: float = 12.0
    # حدّ رمزي فقط: يمسك الإطار المنعدم التفاصيل. القيمة العالية كانت
    # ترفض فيديو مضغوطًا بشدة أو مكبَّرًا من دقة أقل — وكلاهما محتوى سليم.
    # الترتيب النسبي يتكفّل بالتفضيل بين الإطارات.
    min_variance: float = 2.0

    # --- رفض الإطارات الفارغة وشاشات التحميل ---
    # حدّ مطلق منخفض عمدًا: يمسك الإطار الفارغ فعليًا فقط. شريحة عرض
    # بسيطة (عنوان وثلاث نقاط) كثافتها منخفضة أيضًا، ورفضها بحدّ عالٍ
    # يُفقد المستند محتوى سليمًا. التمييز الحقيقي يتم بالعتبة النسبية
    # أدناه، على غرار ADR-011.
    min_content_density: float = 0.0025
    # عالٍ عمدًا: يمسك الإطار شبه المنتظم تمامًا فقط. شريحة بيضاء عليها
    # عنوان وثلاث نقاط لونها الغالب ~94% وهي محتوى سليم. القياس أثبت أن
    # كثافة الحواف وحدها هي المميِّز الموثوق (فارغ 1.0-1.3% مقابل مفيد 2%+)،
    # بينما اللون الغالب للإطارات الفارغة تراوح بين 12% و87% — إشارة
    # غير موثوقة منفردة.
    max_dominant_color_ratio: float = 0.985

    # --- انتظار استقرار الشاشة بعد الانتقال ---
    settle_probe_seconds: float = 0.8    # مسافة إطار المقارنة القريب
    max_instability: float = 0.012       # فرق أعلى منه = الشاشة لم تستقر
    candidates_per_scene: int = 5        # عدد اللحظات المجرَّبة داخل المشهد

    # --- منع التكرار ببصمة إدراكية عبر كل الصور المحفوظة ---
    dedup_similarity: float = 0.93
    min_seconds_between_keyframes: float = 2.0
    max_keyframes: int = 150             # سقف لمنع مستند لا يُقرأ

    # عتبة نسبية بدل رقم مطلق (نفس فلسفة ADR-011): تُسقَط الصور التي
    # تقل درجتها كثيرًا عن وسيط بقية الصور — تمسك شاشة تحميل نجت من
    # الفحص المطلق بفارق ضئيل، وتتكيّف مع طبيعة كل فيديو.
    relative_score_floor: float = 0.75


class ApplicationConfig(BaseModel):
    language: str = "ar"
    output_dir: Path = Field(default_factory=lambda: Path.home() / "VideoToDocOutput")
    keep_temp_on_error: bool = True
    keep_temp_on_success: bool = False
    max_retries: int = 3


class TranscriptionConfig(BaseModel):
    model_size: str = "large-v3-turbo"   # الأدق والأسرع — انظر model_manager
    language: str = "ar"
    device: str = "auto"
    compute_type: str = "auto"
    vad_filter: bool = True
    word_timestamps: bool = True
    beam_size: int = 5
    # حاسم للعربية: يمنع حلقات التكرار الهلوسي على فترات الصمت
    condition_on_previous_text: bool = False
    no_speech_threshold: float = 0.6
    compression_ratio_threshold: float = 2.4
    initial_prompt: str | None = (
        "هذه محاضرة تعليمية باللغة العربية الفصحى. النص مكتوب بعلامات ترقيم صحيحة."
    )
    cpu_threads: int = 4
    download_root: Path | None = None


class DocumentConfig(BaseModel):
    font_family: str = "Arial"
    body_size_pt: float = 12.0
    heading_size_pt: float = 17.0
    enable_toc: bool = True
    # شعار المستند: فارغ = assets/logo.png داخل المشروع إن وُجد
    logo_path: Path | None = None
    logo_width_inches: float = 2.1
    show_timecodes: bool = True
    section_minutes: float = 5.0        # يُستخدم فقط عند تعطيل التكيّف
    adaptive_sections: bool = True      # اشتقاق طول القسم من مدة الفيديو
    paragraph_max_chars: int = 700      # تجميع مقاطع Whisper في فقرات
    max_image_width_inches: float = 5.9
    # سقف الارتفاع: صورة عمودية (فيديو هاتف) بعرض ثابت تتجاوز
    # ارتفاع الصفحة فتُدفع خارجها ويظهر المستند بلا صور
    # سقف يسمح بشكلين في الصفحة للصور الأفقية المعتادة
    max_image_height_inches: float = 4.0
    enable_page_numbers: bool = True
    footer_text: str = "تقرير تفريغ الفيديو"


class RewriteSettings(BaseModel):
    """إعادة الصياغة — معطّلة افتراضيًا (ADR-016)."""
    enabled: bool = False
    provider: str = "anthropic"       # anthropic | ollama | openai_compatible
    model: str = ""                   # فارغ = افتراضي المزوّد
    base_url: str = ""
    api_key_env: str = ""             # فارغ = افتراضي المزوّد
    # بديل عن متغيّر البيئة: يُقرأ من ملف الإعداد مباشرةً فلا يحتاج
    # إعادة فتح البرنامج بعد setx. ⚠ يُحفظ نصًا صريحًا — استخدمه على
    # جهازك الشخصي فقط.
    api_key: str = ""
    # إلزامي للمفاتيح المرتبطة بهوية لدى Anthropic (يبدأ بـ wrkspc_)
    workspace_id: str = ""
    batch_chars: int = 3500
    make_outline: bool = True
    timeout_seconds: int = 180


def default_config_path() -> Path:
    """مسار ملف الإعداد — مصدر واحد للحقيقة لكل من يحتاجه."""
    return Path.home() / ".config" / "video_ai_doc" / "config.yaml"


class AppConfig(BaseModel):
    """الإعداد الجذر — يُحمَّل من YAML ويُحقن في الـ Pipeline."""
    application: ApplicationConfig = Field(default_factory=ApplicationConfig)
    whisper: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    scene_detection: SceneDetectionConfig = Field(default_factory=SceneDetectionConfig)
    frames: KeyframeConfig = Field(default_factory=KeyframeConfig)
    document: DocumentConfig = Field(default_factory=DocumentConfig)
    rewrite: RewriteSettings = Field(default_factory=RewriteSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """يحمّل الإعداد من YAML إن وُجد، وإلا يعيد الافتراضيات."""
        if path is None or not Path(path).exists():
            return cls()
        try:
            import yaml
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            return cls(**data)
        except Exception:
            # إعداد تالف يجب ألا يمنع التشغيل
            return cls()

    def save(self, path: Path) -> None:
        import yaml
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(json.loads(self.model_dump_json()),
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8")
