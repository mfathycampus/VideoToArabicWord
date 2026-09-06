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

    # OCR على كل لقطة مختارة عبر Tesseract (ثنائي خارجي منفصل يُكتشف
    # بنفس نمط ffmpeg — انظر video/ocr.py وtools/doctor.py). معطّل
    # افتراضيًا: يضيف وقت معالجة ملموسًا لكل لقطة، ويتطلّب تثبيتًا
    # يدويًا لا يملكه كل مستخدم. غيابه لا يوقف المعالجة إطلاقًا — فقط
    # يترك نص الشاشة فارغًا.
    enable_ocr: bool = False


class ClipRange(BaseModel):
    """نطاق زمني جزئي من المصدر.

    وجودها يجعل تجربة الإعدادات رخيصة: ضبط النموذج أو القالب أو الصياغة
    على محاضرة ثلاث ساعات كان يكلّف ساعات انتظار لكل تجربة. خمس دقائق
    تكفي للحكم.

    التوقيتات في المستند تبقى **مطلقة** (بزمن المصدر الأصلي) لا نسبية
    للمقطع، وإلا صار الرجوع إلى الفيديو مستحيلًا.
    """
    start_seconds: float = Field(0.0, ge=0)
    end_seconds: float | None = None

    @property
    def is_partial(self) -> bool:
        return self.start_seconds > 0 or self.end_seconds is not None

    @property
    def duration(self) -> float | None:
        if self.end_seconds is None:
            return None
        return max(0.0, self.end_seconds - self.start_seconds)

    def label(self) -> str:
        """لاحقة قصيرة تميّز مجلد المهمة — نطاقان مختلفان مهمتان مختلفتان."""
        if not self.is_partial:
            return ""
        end = ("end" if self.end_seconds is None
               else f"{int(self.end_seconds)}")
        return f"clip-{int(self.start_seconds)}-{end}"

    @classmethod
    def parse(cls, start: str | float | None,
              end: str | float | None) -> "ClipRange":
        """يقبل ثوانيَ أو ``MM:SS`` أو ``HH:MM:SS``."""
        from utils.timestamps import timestamp_to_seconds

        def to_seconds(value):
            if value is None or value == "":
                return None
            if isinstance(value, (int, float)):
                return float(value)
            return timestamp_to_seconds(str(value))

        start_value = to_seconds(start) or 0.0
        end_value = to_seconds(end)
        if end_value is not None and end_value <= start_value:
            raise ValueError("نهاية المقطع يجب أن تكون بعد بدايته.")
        return cls(start_seconds=start_value, end_seconds=end_value)


class ApplicationConfig(BaseModel):
    language: str = "ar"
    output_dir: Path = Field(default_factory=lambda: Path.home() / "VideoToDocOutput")
    keep_temp_on_error: bool = True
    keep_temp_on_success: bool = False
    max_retries: int = 3
    # تنظيف الصوت قبل التفريغ (مرشّحات ffmpeg، بلا اعتمادية جديدة).
    # تقرير القبول: «صوت نظيف — الأثر الأكبر منفردًا» على الدقة.
    denoise_audio: bool = False
    # نوع المحتوى — يحدّد استراتيجية كشف المشاهد واختيار الصور.
    #
    # كانت استراتيجية واحدة تُطبَّق على كل شيء، وهي معايرة على محاضرة
    # بشرائح. القياس أثبت أنها لا تصلح لغيرها: تسجيل شرح برنامج مدّته
    # 8.5 دقائق أعطى 131 مشهدًا و35 صورة بالإعدادات نفسها التي تعطي
    # خمسة مشاهد على شريحة.
    #
    # القيم في ``config/profiles.py``. الاختيار صريح لا تلقائي: مُصنِّف
    # يخطئ يُفسد المخرج بلا أن يعرف أحد لماذا.
    content_profile: str = "slides"


class TranscriptionConfig(BaseModel):
    # محرّك التفريغ (ADR-017). القيم: faster-whisper | cohere-arabic
    engine: str = "faster-whisper"
    # إظهار المحرّكات الاختيارية في الواجهة.
    # معطّل افتراضيًا عمدًا: تلك المحرّكات تجرّ PyTorch، ومجرّد فحص
    # توفّرها كان يُحمّله في العملية فتتنازع بيئتا OpenMP الأنويةَ
    # ويتضاعف زمن التفريغ — بلا أن يختار المستخدم المحرّك.
    show_optional_engines: bool = False
    model_size: str = "large-v3-turbo"   # الأدق والأسرع — انظر model_manager
    language: str = "ar"
    device: str = "auto"
    compute_type: str = "auto"
    vad_filter: bool = True
    # ── ضبط VAD ──────────────────────────────────────────────────────
    # كان يعمل بقيم المكتبة الافتراضية بلا أي ضبط ولا أي وسيلة لضبطه.
    #
    # **القيم أدناه مطابقة لافتراضيات المكتبة عمدًا**، فلا تغيير في
    # السلوك بمجرّد الترقية. غايتها أن تصير قابلة للضبط والقياس: أثر
    # هذه المعاملات على معدّل الخطأ لا يُخمَّن، والقيمة الخاطئة تُسوّئ
    # النتيجة. اضبطها على مجموعة تقييم من محاضراتك بـ
    # ``tools/measure_wer.py``، لا على الحدس.
    #
    # اتجاه الضبط حين تقيس:
    #   * ``speech_pad_ms`` — حشو حول كل مقطع كلام. رفعه (600–800)
    #     يستعيد بدايات ونهايات الكلمات المقصوصة عند الحواف؛ الإفراط
    #     يُدخل ضجيجًا وصمتًا يُغري النموذج بالهلوسة.
    #   * ``min_speech_duration_ms`` — رفعه (~250) يُسقط النقرات
    #     والأنفاس التي تُنتج رموزًا عشوائية.
    #   * ``min_silence_duration_ms`` — خفضه يفصل الجمل عند الوقفات
    #     القصيرة بدل دمجها.
    #   * ``max_speech_duration_s`` — 0 يعني بلا حدّ (افتراضي المكتبة).
    vad_speech_pad_ms: int = 400
    vad_min_speech_duration_ms: int = 0
    vad_min_silence_duration_ms: int = 2000
    vad_threshold: float = 0.5
    vad_max_speech_duration_s: float = 0.0
    word_timestamps: bool = True
    # 5 = بحث شعاعي (أدق)، 1 = جشع (أسرع بمرّة ونصف إلى مرّتين على
    # المعالج، بكلفة دقة متواضعة). الفارق كبير على محاضرة طويلة:
    # ساعة ونصف مقابل ثلاث ساعات.
    beam_size: int = 5
    # حاسم للعربية: يمنع حلقات التكرار الهلوسي على فترات الصمت
    condition_on_previous_text: bool = False
    no_speech_threshold: float = 0.6
    compression_ratio_threshold: float = 2.4
    # ثوانٍ من الصمت يُسقَط بعدها النصّ المُختلَق. المعامل موجود في
    # المكتبة ومعطّل افتراضيًا (``None``)، وشرطه ``word_timestamps``
    # مُفعَّل عندنا أصلًا. تفعيله يمنع أشهر عطل عربي: حلقة «شكرًا لكم»
    # تملأ صفحة فوق موسيقى أو تصفيق. صفر أثر على صوت نظيف.
    hallucination_silence_threshold: float | None = 2.0
    # عدد نوافذ الصوت التي تُفكّ معًا. **1 = متسلسل، وهو الافتراضي**.
    # 0 = تلقائي (8 على المعالج، 16 على البطاقة).
    #
    # الافتراضي متسلسل بناءً على قياس، لا على توقّع. القياس الأول كان
    # على 132 ثانية من كلام **إنجليزي** بنموذج ``tiny`` فأعطى 2.3–6.6×
    # لصالح التجميع. والقياس على المادّة الحقيقية — 8.5 دقائق شرح عربي
    # بنموذج الإنتاج ``large-v3-turbo`` — قلب النتيجة:
    #
    #     متسلسل    182.7 ث   RTF 0.357   175 مقطعًا   3791 حرفًا
    #     مُجمَّع 8   217.7 ث   RTF 0.425    52 مقطعًا   3727 حرفًا
    #
    # أي **أبطأ بـ19٪** لا أسرع. والأمانة تقتضي إضافة أن تشتّت القياس
    # على هذه الآلة واسع (ثلاث تشغيلات متكافئة: 182.7 و193.9 و250.8
    # ثانية)، ففرق الـ19٪ داخل الضجيج. الخلاصة ليست «التجميع أبطأ» بل
    # **«لا مكسب مقيسًا»** — ومعه تغيّر في حدود المقاطع وفي النصّ.
    #
    # تغييرٌ بلا مكسب مُثبَت ليس افتراضيًا صالحًا. يبقى التجميع متاحًا
    # لمن يقيسه على عتاده: ``batch_size: 8``، ويُقاس بـ
    # ``tools/benchmark_asr.py``. على بطاقة رسومية قد تختلف الصورة
    # تمامًا — ولم يُقَس ذلك بعد.
    #
    # يشترط ``vad_filter``: المسار المُجمَّع يبني دفعاته من مقاطع الكلام
    # التي يكشفها. عند تعطيله نسقط إلى المتسلسل تلقائيًا.
    batch_size: int = 1
    initial_prompt: str | None = (
        "هذه محاضرة تعليمية باللغة العربية الفصحى. النص مكتوب بعلامات ترقيم صحيحة."
    )
    # مصطلحات مادتك وأسماء الأعلام فيها، مفصولة بفواصل أو أسطر.
    # تُمرَّر كـ ``hotwords`` فتُحقَن في موجّه **كل** نافذة تفريغ.
    #
    # كانت تُدمج في ``initial_prompt``، وهو ما لا يعمل: المكتبة تُصفّر
    # الموجّه بعد كل نافذة ما دام ``condition_on_previous_text=False``،
    # فكانت المصطلحات تصل أول ~30 ثانية فقط — 0.3٪ من محاضرة ثلاث ساعات.
    #
    # تقرير القبول أثبت أن الأخطاء شبه محصورة في أسماء الأعلام المنقولة
    # («كازا برانكا» ← «كذابرانكا»)، وهذا أرخص علاج لها.
    glossary: str = ""
    # 0 = اكتشاف تلقائي من عدد أنوية المعالج. الرقم الثابت 4 كان يترك
    # نصف الأداء على جهاز بثمانية أنوية، ويُثقِل جهازًا بنواتين.
    cpu_threads: int = 0
    download_root: Path | None = None
    # رمز HuggingFace — يلزم للنماذج مقيّدة الوصول (محرّك Cohere مثلًا).
    # بديل عن متغيّر البيئة HF_TOKEN فلا يحتاج setx ولا إعادة فتح البرنامج.
    # يُحفظ في ملف الإعداد مشفَّرًا بمفتاح محلي مولَّد على جهازك (انظر
    # utils/secret_store.py) — لم يعد نصًا صريحًا قابلًا للقراءة المباشرة.
    # المفتاح ملف منفصل بجواره؛ نسخ الملفين معًا لجهاز آخر يفكّ التشفير
    # هناك أيضًا، فـ«جهازك الشخصي فقط» يبقى النطاق الآمن الفعلي.
    hf_token: str = ""


class DocumentConfig(BaseModel):
    font_family: str = "Arial"
    body_size_pt: float = 12.0
    heading_size_pt: float = 17.0
    enable_toc: bool = True
    # شعار المستند: فارغ = assets/logo.png داخل المشروع إن وُجد
    logo_path: Path | None = None
    logo_width_inches: float = 2.1
    show_timecodes: bool = True
    section_minutes: float = 5.0
    # هل يُشتقّ عنوان القسم من نصّ الشاشة (OCR)؟
    # على شريحة، أعلى الصورة عنوانها حرفيًا فالجواب نعم. وعلى تسجيل
    # شاشة، أعلاها شريط تبويبات المتصفّح — فأنتج «Googke Chrome»
    # عنوانًا لقسم. يضبطه ملفّ المحتوى، والبديل عنوان ترتيبي.
    screen_text_titles: bool = True
    adaptive_sections: bool = True      # اشتقاق طول القسم من مدة الفيديو
    paragraph_max_chars: int = 700      # تجميع مقاطع Whisper في فقرات
    max_image_width_inches: float = 5.9
    # سقف الارتفاع: صورة عمودية (فيديو هاتف) بعرض ثابت تتجاوز
    # ارتفاع الصفحة فتُدفع خارجها ويظهر المستند بلا صور
    # سقف يسمح بشكلين في الصفحة للصور الأفقية المعتادة
    max_image_height_inches: float = 4.0
    enable_page_numbers: bool = True
    footer_text: str = "تقرير تفريغ الفيديو"
    # ملفات ترجمة بجوار المستند. مجانية عمليًا: توقيت الكلمات محفوظ
    # أصلًا في transcription.json ولم يكن يُقرأ.
    export_subtitles: bool = True
    subtitle_formats: list[str] = Field(default_factory=lambda: ["srt", "vtt"])
    # فهرس الأشكال بعد جدول المحتويات
    enable_figure_index: bool = True


class RewriteSettings(BaseModel):
    """إعادة الصياغة — معطّلة افتراضيًا (ADR-016)."""
    enabled: bool = False
    provider: str = "anthropic"       # anthropic | ollama | openai_compatible
    model: str = ""                   # فارغ = افتراضي المزوّد
    base_url: str = ""
    api_key_env: str = ""             # فارغ = افتراضي المزوّد
    # بديل عن متغيّر البيئة: يُقرأ من ملف الإعداد مباشرةً فلا يحتاج
    # إعادة فتح البرنامج بعد setx. يُحفظ مشفَّرًا بمفتاح محلي — انظر
    # utils/secret_store.py وتعليق hf_token أعلاه لنفس الحدود والنطاق.
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

    # آخر خطأ تحميل — تقرؤه الواجهة لتُعلم المستخدم بدل ابتلاعه
    load_error: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """يحمّل الإعداد من YAML إن وُجد، وإلا يعيد الافتراضيات.

        إعداد تالف يجب ألا يمنع التشغيل — لكن الصمت التام عنه كان يجعل
        المستخدم يفقد كل خياراته بلا أن يعرف السبب. الآن نواصل بالافتراضيات
        **ونحتفظ بالسبب** في ``load_error`` لتعرضه الواجهة والسجلّ.
        """
        if path is None or not Path(path).exists():
            return cls()
        try:
            import yaml
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

            from utils.secret_store import decrypt_field
            config_dir = Path(path).parent
            whisper_data = data.get("whisper") or {}
            if whisper_data.get("hf_token"):
                whisper_data["hf_token"] = decrypt_field(
                    whisper_data["hf_token"], config_dir)
            rewrite_data = data.get("rewrite") or {}
            if rewrite_data.get("api_key"):
                rewrite_data["api_key"] = decrypt_field(
                    rewrite_data["api_key"], config_dir)

            return cls(**data)
        except Exception as exc:
            from utils.logger import logger
            message = (f"تعذّرت قراءة ملف الإعداد {path} ({exc}) — "
                       "استُخدمت القيم الافتراضية.")
            logger.warning(message)
            fallback = cls()
            fallback.load_error = message
            return fallback

    def save(self, path: Path) -> None:
        import yaml
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.model_dump_json())
        data.pop("load_error", None)   # حقل تشخيصي، لا يُحفظ

        # الأسرار تُشفَّر بمفتاح محلي قبل الكتابة — انظر utils/secret_store.
        # ``self.whisper.hf_token`` و``self.rewrite.api_key`` في الذاكرة
        # يبقيان نصًا صريحًا؛ التشفير عند حدود التسلسل فقط (هذا القاموس)
        # فلا يتأثر أي كود آخر يقرأ الإعداد أثناء التشغيل.
        from utils.secret_store import encrypt_field
        if data.get("whisper", {}).get("hf_token"):
            data["whisper"]["hf_token"] = encrypt_field(
                data["whisper"]["hf_token"], path.parent)
        if data.get("rewrite", {}).get("api_key"):
            data["rewrite"]["api_key"] = encrypt_field(
                data["rewrite"]["api_key"], path.parent)

        serialized = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)
