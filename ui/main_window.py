"""النافذة الرئيسية — واجهة عربية RTL بلا أي معالجة داخل خيط الواجهة."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from audio.model_manager import (
    AVAILABLE_MODELS,
    ModelManager,
    estimate_processing_seconds,
)
from config.profiles import PROFILES, profile_choices
from config.settings import AppConfig
from core.pipeline import VideoToDocPipeline
from ui import theme
from ui.worker import PipelineWorker, ProviderTestWorker, ScreenTermsWorker
from utils.cancellation import CancellationToken
from utils.error_reporting import format_error_for_user
from utils.gpu_manager import GPUManager
from utils.media_probe import extract_video_facts, probe_raw
from utils.timestamps import humanize_duration
from version import APP_VERSION


def _engine_status(name: str) -> tuple[bool, str]:
    """‏(جاهز؟، السبب) — السبب يُعرض للمستخدم كما هو.

    كان هذا الفحص يبتلع كل استثناء ويعيد ``False`` صامتًا، فيرى مستخدمٌ
    نفّذ أمر التثبيت فعلًا كلمةَ «غير مثبّت» بلا أي دلالة على السبب.
    """
    try:
        from audio.engines.registry import build_engine
        return build_engine(name).diagnose()
    except Exception as exc:
        return (False, f"تعذّر فحص المحرّك: {type(exc).__name__}: "
                       f"{str(exc)[:200]}")


def _engine_ready(name: str) -> bool:
    return _engine_status(name)[0]


VIDEO_FILTER = (
    "ملفات الفيديو (*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.ts *.m4v *.mpg "
    "*.mpeg *.3gp);;كل الملفات (*)")

# ffmpeg يفكّ ما هو أكثر من ذلك بكثير، والخيار الأخير يترك الباب مفتوحًا
# لأي امتداد — القائمة للتيسير لا للتقييد.
AUDIO_FILTER = (
    "ملفات الصوت (*.mp3 *.m4a *.wav *.aac *.flac *.ogg *.opus *.wma *.aiff "
    "*.aif *.amr *.mka *.webm *.oga *.3ga *.caf *.ac3 *.dts *.m4b *.mp2 "
    "*.au *.ra);;كل الملفات (*)")


class MainWindow(QMainWindow):
    def __init__(self, config: Optional[AppConfig] = None,
                 config_path: Optional[Path] = None) -> None:
        super().__init__()
        self.config = config or AppConfig()
        from config.settings import default_config_path
        self.config_path = config_path or default_config_path()
        self.model_manager = ModelManager(self.config.whisper.download_root)
        self.video_path: Optional[Path] = None
        # هل المصدر المختار ملف صوتي خالص؟ يحدّده الزر الذي اختِير منه.
        self.audio_only: bool = False
        self.thread: Optional[QThread] = None
        self.worker: Optional[PipelineWorker] = None
        self.cancel_token: Optional[CancellationToken] = None
        self.result_path: Optional[Path] = None
        self._test_thread: Optional[QThread] = None
        self._test_worker: Optional[ProviderTestWorker] = None
        self._terms_thread: Optional[QThread] = None
        self._terms_worker: Optional[ScreenTermsWorker] = None
        self._build_ui()
        self._refresh_profile_notice()
        self._warn_if_config_failed_to_load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("محوّل الفيديو إلى مستند Word — محلي بالكامل")
        # الحد الأدنى يكفي أضيق مجموعة؛ الباقي يتكفّل به التمرير
        self.setMinimumSize(720, 480)
        self.resize(900, 760)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        # منطقة تمرير حول المحتوى كله.
        #
        # بدونها ينهار التصميم: ‏QVBoxLayout عندما يقلّ ارتفاع النافذة عن
        # مجموع الارتفاعات الدنيا للمجموعات يضغطها فوق بعضها، فتُرسم
        # الصفوف على بعضها ويصير النص غير مقروء. ومع نمو الواجهة
        # (خيارات متقدّمة، محرّكات، مفاتيح) صار المجموع يتجاوز شاشات
        # كثيرة. التمرير يجعل الانهيار مستحيلًا مهما أُضيف لاحقًا.
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_header())
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # --- اختيار الملف ---
        file_box = QGroupBox("١ · المصدر ومجلد الحفظ")
        file_box_layout = QVBoxLayout(file_box)
        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.file_label = QLabel("لم يُختَر ملف بعد")
        self.file_label.setWordWrap(True)
        browse = QPushButton("اختيار فيديو…")
        browse.clicked.connect(self.choose_file)
        # الصوت مسار كامل لا حالة حدّية: محاضرة مسجَّلة صوتيًا، مقابلة،
        # حلقة بودكاست. المستند يخرج منسّقًا كاملًا بلا لقطات فقط.
        browse_audio = QPushButton("اختيار ملف صوتي…")
        browse_audio.setToolTip(
            "يقبل أي امتداد صوتي يفكّه ffmpeg — mp3، m4a، wav، flac، opus…\n"
            "المخرج مستند Word منسّق بالكامل بلا لقطات شاشة.")
        browse_audio.clicked.connect(self.choose_audio_file)
        browse_folder = QPushButton("مجلد كامل…")
        browse_folder.setToolTip(
            "يعالج كل ملفات الوسائط في مجلد، واحدًا تلو الآخر.\n"
            "فشل ملف لا يوقف البقية.")
        browse_folder.clicked.connect(self.choose_folder)
        file_layout.addWidget(self.file_label, 1)
        file_layout.addWidget(browse)
        file_layout.addWidget(browse_audio)
        file_layout.addWidget(browse_folder)
        file_box_layout.addWidget(file_row)

        # مجلد الحفظ صفٌّ داخل نفس المجموعة لا مجموعة مستقلة: مجموعة
        # كاملة لحقل واحد تُهدر ~90 بكسل من ارتفاع الواجهة بلا مقابل.
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("مجلد الحفظ:"))
        self.output_label = QLabel(str(self.config.application.output_dir))
        self.output_label.setWordWrap(True)
        self.output_label.setStyleSheet(f"color: {theme.MUTED};")
        change_output = QPushButton("تغيير…")
        change_output.clicked.connect(self.choose_output_dir)
        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(change_output)
        file_box_layout.addLayout(output_row)

        layout.addWidget(file_box)

        # --- النموذج ---
        model_box = QGroupBox("٢ · نموذج التفريغ")
        model_layout = QVBoxLayout(model_box)

        # محرّك التفريغ (ADR-017). لا تُعرض القائمة حين لا يوجد خيار
        # فعلي — صفٌّ بخيار وحيد ضجيج بصري لا معلومة.
        from audio.engines.registry import available_engines, listed_engines

        show_optional = bool(
            getattr(self.config.whisper, "show_optional_engines", False))
        names = listed_engines(show_optional)
        infos = {i.name: i for i in available_engines(show_optional)}

        self.engine_combo = QComboBox()
        for name in names:
            info = infos.get(name)
            if info is None:
                continue
            # الفحص هنا لا يستورد شيئًا (‏find_spec)، فلا يُحمّل PyTorch
            mark = "" if _engine_ready(name) else "  (غير مثبّت)"
            self.engine_combo.addItem(f"{info.label_ar}{mark}", name)
        index = self.engine_combo.findData(self.config.whisper.engine)
        if index >= 0:
            self.engine_combo.setCurrentIndex(index)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)

        if self.engine_combo.count() > 1:
            engine_row = QHBoxLayout()
            engine_row.addWidget(QLabel("المحرّك:"))
            engine_row.addWidget(self.engine_combo, 1)
            model_layout.addLayout(engine_row)
        else:
            self.engine_combo.hide()

        # ── نوع المحتوى ──────────────────────────────────────────
        # أهمّ اختيار في الشاشة، ولذلك يسبق كل ما يتعلّق بالنموذج:
        # مجموعة إعدادات واحدة معايرة على محاضرة بشرائح كانت تُطبَّق على
        # كل شيء. تسجيل شرح برنامج مدّته 8.5 دقائق أعطى 131 مشهدًا و35
        # صورة بها؛ وبملفّه الصحيح 30 مشهدًا و15 صورة، ودرجة قابلية
        # القراءة من 13 إلى 39.
        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        for key, label in profile_choices():
            profile = PROFILES[key]
            suffix = "" if profile.measured else "  (قيم مبدئية)"
            self.profile_combo.addItem(f"{label}{suffix}", key)
            self.profile_combo.setItemData(
                self.profile_combo.count() - 1,
                profile.description, Qt.ItemDataRole.ToolTipRole)
        index = self.profile_combo.findData(
            self.config.application.content_profile)
        self.profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(QLabel("نوع التسجيل:"))
        profile_row.addWidget(self.profile_combo, 1)
        model_layout.addLayout(profile_row)

        # تحذير OCR يظهر تحت القائمة مباشرةً — عند الاختيار لا بعد ساعة.
        self.profile_notice = QLabel("")
        self.profile_notice.setWordWrap(True)
        self.profile_notice.setStyleSheet(f"color: {theme.WARNING};")
        self.profile_notice.setVisible(False)
        model_layout.addWidget(self.profile_notice)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        for spec in AVAILABLE_MODELS:
            status = "✓ مثبّت" if self.model_manager.is_available(spec.name) \
                else f"يحتاج تنزيل ~{spec.approx_size_gb:.1f}GB"
            self.model_combo.addItem(f"{spec.label_ar}  ({status})", spec.name)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_row.addWidget(QLabel("النموذج:"))
        model_row.addWidget(self.model_combo, 1)

        # فكّ التشفير: الفارق على محاضرة طويلة ساعات لا دقائق
        model_row.addWidget(QLabel("فكّ التشفير:"))
        self.beam_combo = QComboBox()
        self.beam_combo.addItem("دقيق (بحث شعاعي)", 5)
        self.beam_combo.addItem("سريع (جشع) — أسرع ~2×", 1)
        index = self.beam_combo.findData(self.config.whisper.beam_size)
        self.beam_combo.setCurrentIndex(index if index >= 0 else 0)
        self.beam_combo.currentIndexChanged.connect(self._on_beam_changed)
        model_row.addWidget(self.beam_combo)
        model_layout.addLayout(model_row)

        # رمز HuggingFace — النماذج مقيّدة الوصول تحتاجه. الحقل هنا بدل
        # setx: ذاك لا يؤثر على البرامج المفتوحة، فيضبطه المستخدم ويظل
        # يرى «مقيّد الوصول» بلا أن يفهم السبب.
        self.hf_row = QWidget()
        hf_layout = QHBoxLayout(self.hf_row)
        hf_layout.setContentsMargins(0, 0, 0, 0)
        hf_layout.addWidget(QLabel("رمز HuggingFace:"))
        self.hf_token_input = QLineEdit()
        self.hf_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token_input.setPlaceholderText(
            "hf_…  (يلزم للنماذج مقيّدة الوصول · يُحفظ على جهازك)")
        self.hf_token_input.setText(self.config.whisper.hf_token)
        self.hf_token_input.textChanged.connect(self._on_hf_token_typed)
        hf_layout.addWidget(self.hf_token_input, 1)
        self.hf_help_button = QPushButton("كيف أحصل عليه؟")
        self.hf_help_button.clicked.connect(self.show_hf_help)
        hf_layout.addWidget(self.hf_help_button)
        model_layout.addWidget(self.hf_row)

        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        self.engine_note.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        model_layout.addWidget(self.engine_note)
        layout.addWidget(model_box)

        # --- صياغة النص ---
        rewrite_box = QGroupBox("٣ · صياغة النص (اختياري)")
        rewrite_layout = QVBoxLayout(rewrite_box)
        self.rewrite_check = QCheckBox(
            "إعادة صياغة التفريغ إلى نص وثيقة احترافي بالذكاء الاصطناعي")
        self.rewrite_check.setChecked(self.config.rewrite.enabled)
        self.rewrite_check.stateChanged.connect(self._on_rewrite_toggled)
        rewrite_layout.addWidget(self.rewrite_check)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("المزوّد:"))
        self.provider_combo = QComboBox()
        from ai.providers import PROVIDER_CHOICES
        for value, label in PROVIDER_CHOICES:
            self.provider_combo.addItem(label, value)
        current = self.provider_combo.findData(self.config.rewrite.provider)
        if current >= 0:
            self.provider_combo.setCurrentIndex(current)
        self.provider_combo.currentIndexChanged.connect(self._on_rewrite_toggled)
        provider_row.addWidget(self.provider_combo, 1)
        self.provider_row = QWidget()
        self.provider_row.setLayout(provider_row)
        rewrite_layout.addWidget(self.provider_row)

        self.key_row = QWidget()
        key_row = QHBoxLayout(self.key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(QLabel("مفتاح الوصول:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-ant-api03-…  (يُحفظ على جهازك)")
        self.api_key_input.setText(self.config.rewrite.api_key)
        self.api_key_input.textChanged.connect(self._on_key_typed)
        key_row.addWidget(self.api_key_input, 1)
        self.test_button = QPushButton("اختبار وحفظ")
        self.test_button.clicked.connect(self.test_and_save_key)
        key_row.addWidget(self.test_button)
        rewrite_layout.addWidget(self.key_row)

        self.workspace_row = QWidget()
        workspace_row = QHBoxLayout(self.workspace_row)
        workspace_row.setContentsMargins(0, 0, 0, 0)
        workspace_row.addWidget(QLabel("معرّف مساحة العمل:"))
        self.workspace_input = QLineEdit()
        self.workspace_input.setPlaceholderText(
            "wrkspc_…  (اتركه فارغًا إلا إن طلبه الخطأ)")
        self.workspace_input.setText(self.config.rewrite.workspace_id)
        self.workspace_input.textChanged.connect(
            lambda t: setattr(self.config.rewrite, "workspace_id", t.strip()))
        workspace_row.addWidget(self.workspace_input, 1)
        rewrite_layout.addWidget(self.workspace_row)

        self.rewrite_note = QLabel("")
        self.rewrite_note.setWordWrap(True)
        self.rewrite_note.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        rewrite_layout.addWidget(self.rewrite_note)
        layout.addWidget(rewrite_box)
        self._update_rewrite_note()

        # --- خيارات متقدّمة ---
        advanced = QGroupBox("٤ · خيارات متقدّمة")
        advanced_layout = QVBoxLayout(advanced)

        # نطاق المعالجة: أرخص طريقة لتجربة إعداد على محاضرة طويلة
        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel("عالج من:"))
        self.clip_start_input = QLineEdit()
        self.clip_start_input.setPlaceholderText("00:00  (اتركه فارغًا للبداية)")
        self.clip_start_input.setMaximumWidth(190)
        clip_row.addWidget(self.clip_start_input)
        clip_row.addWidget(QLabel("إلى:"))
        self.clip_end_input = QLineEdit()
        self.clip_end_input.setPlaceholderText("05:00  (اتركه فارغًا للنهاية)")
        self.clip_end_input.setMaximumWidth(190)
        clip_row.addWidget(self.clip_end_input)
        clip_hint = QLabel("لتجربة الإعدادات بسرعة قبل معالجة الملف كاملًا")
        clip_hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        clip_row.addWidget(clip_hint, 1)
        for field in (self.clip_start_input, self.clip_end_input):
            field.textChanged.connect(self._refresh_estimate)
        advanced_layout.addLayout(clip_row)

        # مصطلحات المادة: أرخص رفع للدقة على أسماء الأعلام
        advanced_layout.addWidget(QLabel("مصطلحات المادة وأسماء الأعلام فيها:"))
        self.glossary_input = QTextEdit()
        self.glossary_input.setPlaceholderText(
            "مثال: بوذا، اليوغا، كازابلانكا، PowerSchool، Schoology…\n"
            "تُمرَّر إلى النموذج فيتعرّف عليها بدل تخمين إملائها.")
        self.glossary_input.setMaximumHeight(70)
        self.glossary_input.setPlainText(self.config.whisper.glossary)
        self.glossary_input.textChanged.connect(self._on_glossary_changed)
        advanced_layout.addWidget(self.glossary_input)

        # اقتراح المصطلحات من الشاشة — انظر ``video/screen_terms.py``
        # لسبب أنه يقترح ولا يحقن.
        terms_row = QHBoxLayout()
        self.scan_terms_button = QPushButton("اقترح مصطلحات من الشاشة")
        self.scan_terms_button.setToolTip(
            "يقرأ نصّ الشاشة من إطارات موزّعة على الفيديو ويقترح ما وجده "
            "من أسماء قوائم وأزرار. راجِعها واحذف ما لا يُنطق في التسجيل.")
        self.scan_terms_button.clicked.connect(self._scan_screen_terms)
        terms_row.addWidget(self.scan_terms_button)
        self.scan_terms_status = QLabel("")
        self.scan_terms_status.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        terms_row.addWidget(self.scan_terms_status, 1)
        advanced_layout.addLayout(terms_row)

        # شعار ترويسة المستند — الافتراضي شعار البرنامج.
        logo_row = QHBoxLayout()
        logo_row.addWidget(QLabel("شعار ترويسة المستند:"))
        self.logo_label = QLabel("")
        self.logo_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        logo_row.addWidget(self.logo_label, 1)
        choose_logo = QPushButton("اختيار…")
        choose_logo.clicked.connect(self._choose_logo)
        clear_logo = QPushButton("العودة للافتراضي")
        clear_logo.clicked.connect(self._clear_logo)
        logo_row.addWidget(choose_logo)
        logo_row.addWidget(clear_logo)
        advanced_layout.addLayout(logo_row)
        self._refresh_logo_label()

        toggles = QHBoxLayout()
        self.denoise_check = QCheckBox("تنظيف الصوت قبل التفريغ")
        self.denoise_check.setToolTip(
            "مرشّحات ffmpeg: قطع الهدير، خفض الضوضاء، توحيد المستوى.\n"
            "نظافة الصوت هي الأثر الأكبر منفردًا على دقة التفريغ.")
        self.denoise_check.setChecked(self.config.application.denoise_audio)
        self.denoise_check.stateChanged.connect(
            lambda: setattr(self.config.application, "denoise_audio",
                            self.denoise_check.isChecked()))
        toggles.addWidget(self.denoise_check)

        self.transcript_only_check = QCheckBox("تفريغ فقط (بلا مستند Word)")
        self.transcript_only_check.setToolTip(
            "يقف عند التفريغ ويُخرج txt و md و srt و vtt — أسرع بكثير.")
        self.transcript_only_check.stateChanged.connect(self._refresh_estimate)
        toggles.addWidget(self.transcript_only_check)

        self.ocr_check = QCheckBox("استخراج نص الشاشة من الصور (OCR)")
        self.ocr_check.setToolTip(
            "يقرأ النص الظاهر فعليًا على الشاشة (عناوين، جداول، تعريفات)\n"
            "لكل صورة مختارة، ويُدرجه تحتها في المستند. يحتاج تثبيت\n"
            "Tesseract منفصلًا (مثل ffmpeg تمامًا) — شغّل tools/doctor.py\n"
            "للتحقّق. يضيف وقت معالجة لكل صورة.")
        self.ocr_check.setChecked(self.config.frames.enable_ocr)
        self.ocr_check.stateChanged.connect(
            lambda: setattr(self.config.frames, "enable_ocr",
                            self.ocr_check.isChecked()))
        toggles.addWidget(self.ocr_check)
        toggles.addStretch(1)
        advanced_layout.addLayout(toggles)
        layout.addWidget(advanced)

        # --- التشغيل ---
        run_box = QGroupBox("٥ · المعالجة")
        run_layout = QVBoxLayout(run_box)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("ابدأ المعالجة")
        # زرٌّ أساسي واحد في النافذة كلها — انظر ``ui/theme.py``.
        self.start_button.setProperty("primary", "true")
        self.start_button.clicked.connect(self.start)
        self.start_button.setEnabled(False)
        self.pause_button = QPushButton("إيقاف مؤقت")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        self.cancel_button = QPushButton("إلغاء")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        # إعادة البناء من الخطة المحفوظة: تغيير الخط أو الشعار أو حجم
        # الصور كان يستلزم إعادة المهمة كاملة — ساعات على المعالج — رغم
        # أن كل ما يحتاجه التوليد محفوظ على القرص.
        self.rebuild_button = QPushButton("أعد بناء المستند")
        self.rebuild_button.setToolTip(
            "يعيد توليد ملف Word من الخطة المحفوظة بالإعدادات الحالية — "
            "بلا إعادة تفريغ أو تحليل.")
        self.rebuild_button.clicked.connect(self.rebuild_document)
        self.rebuild_button.setEnabled(False)
        self.open_button = QPushButton("فتح المستند")
        self.open_button.clicked.connect(self.open_result)
        self.open_button.setEnabled(False)
        for b in (self.start_button, self.pause_button, self.cancel_button,
                  self.rebuild_button, self.open_button):
            buttons.addWidget(b)
        run_layout.addLayout(buttons)

        tools_row = QHBoxLayout()
        jobs_button = QPushButton("المهام السابقة…")
        jobs_button.setToolTip(
            "كل ما عالجته وحالته — مع فتح واستئناف وإعادة بناء.")
        jobs_button.clicked.connect(self.show_jobs)
        merge_button = QPushButton("ادمج مهامّ في مستند واحد…")
        merge_button.setToolTip(
            "محاضرة مقسّمة إلى أجزاء تصير وثيقة واحدة بفهرس\n"
            "وترقيم أشكال متصلين.")
        merge_button.clicked.connect(self.merge_jobs)
        tools_row.addWidget(jobs_button)
        tools_row.addWidget(merge_button)
        tools_row.addStretch(1)
        run_layout.addLayout(tools_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("%p%")
        run_layout.addWidget(self.progress)

        self.status_label = QLabel("جاهز")
        run_layout.addWidget(self.status_label)

        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        run_layout.addWidget(self.estimate_label)
        layout.addWidget(run_box)

        # --- السجل ---
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        # ارتفاع محدّد لا تمدّد: داخل منطقة تمرير، العنصر المتمدّد يدفع
        # المحتوى ويجعل شريط التمرير بلا معنى.
        self.log.setMinimumHeight(120)
        self.log.setMaximumHeight(200)
        layout.addWidget(self.log)
        layout.addStretch(0)

        scroll = QScrollArea()
        scroll.setWidget(root)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        # Initialize UI state after all widgets are created
        self._on_engine_changed()

    # ------------------------------------------------------------------
    def _on_rewrite_toggled(self) -> None:
        enabled = self.rewrite_check.isChecked()
        self.config.rewrite.enabled = enabled
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.provider_combo.setEnabled(enabled)
        self._update_rewrite_note()

    def _on_key_typed(self, text: str) -> None:
        self.config.rewrite.api_key = text.strip()

    def _needs_key(self) -> bool:
        return self.provider_combo.currentData() != "ollama"

    def test_and_save_key(self) -> None:
        """يختبر المفتاح باستدعاء حقيقي قصير ثم يحفظه.

        فحص وجود المفتاح وحده لا يكفي: مفتاح خاطئ يبدو «جاهزًا» حتى أول
        استدعاء بعد ساعة من التفريغ. الاختبار هنا يكشفه في ثانيتين.
        """
        from ai.providers import build_provider

        self.config.rewrite.api_key = self.api_key_input.text().strip()
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.config.rewrite.workspace_id = self.workspace_input.text().strip()
        provider = build_provider(
            self.config.rewrite.provider, self.config.rewrite.model,
            self.config.rewrite.base_url, self.config.rewrite.api_key_env,
            self.config.rewrite.api_key, self.config.rewrite.workspace_id)

        if not provider.is_available():
            QMessageBox.warning(self, "مفتاح مفقود",
                                "ألصق المفتاح في الحقل أولًا.")
            return

        # الاختبار على خيط خلفي: نداء الشبكة بمهلة 30 ثانية داخل معالج
        # الزر كان يجمّد الواجهة فعليًا (ADR-001 والمواصفة §15).
        self.test_button.setEnabled(False)
        self.test_button.setText("جارٍ الاختبار…")
        self.append_log("جارٍ اختبار الاتصال بالمزوّد…")

        self._test_thread = QThread(self)
        self._test_worker = ProviderTestWorker(provider)
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.succeeded.connect(self._on_key_test_ok)
        self._test_worker.failed.connect(self._on_key_test_failed)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.finished.connect(self._reset_test_button)
        self._test_thread.start()

    def _on_key_test_ok(self, reply: str) -> None:
        self._save_config()
        QMessageBox.information(
            self, "نجح الاتصال",
            f"المزوّد يعمل ورد بـ: {reply}\n\n"
            f"حُفظ المفتاح في:\n{self.config_path}")
        self.append_log("تم التحقق من المفتاح وحفظه.")

    def _on_key_test_failed(self, error: str) -> None:
        QMessageBox.critical(self, "فشل الاتصال", error)
        self.append_log(f"فشل اختبار المفتاح: {error}")

    def _reset_test_button(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("اختبار وحفظ")
        self._update_rewrite_note()

    def _save_config(self) -> None:
        try:
            self.config.save(self.config_path)
        except Exception as exc:
            self.append_log(f"تعذّر حفظ الإعداد: {exc}")

    def _update_rewrite_note(self) -> None:
        """يوضّح أثر الخيار على الخصوصية قبل تفعيله، لا بعده."""
        if not self.rewrite_check.isChecked():
            self.rewrite_note.setText(
                "معطّل — يُكتب نص التفريغ كما هو. لا اتصال بالإنترنت.")
            # الإخفاء لا التعطيل: العنصر المعطّل يشغل ارتفاعه كاملًا،
            # وثلاثة صفوف معطّلة تدفع بقية الواجهة خارج الشاشة.
            self.provider_row.setVisible(False)
            self.key_row.setVisible(False)
            self.workspace_row.setVisible(False)
            return
        needs_key = self._needs_key()
        self.provider_row.setVisible(True)
        self.key_row.setVisible(needs_key)
        self.workspace_row.setVisible(
            needs_key and self.provider_combo.currentData() == "anthropic")
        self.api_key_input.setEnabled(needs_key)
        self.test_button.setEnabled(True)
        name = self.provider_combo.currentData()
        try:
            from ai.providers import build_provider
            provider = build_provider(
                name, self.config.rewrite.model, self.config.rewrite.base_url,
                self.config.rewrite.api_key_env, self.config.rewrite.api_key,
                self.config.rewrite.workspace_id)
            available = provider.is_available()
            status = "جاهز ✓" if available else "غير مهيّأ ✗"
            note = provider.info.privacy_note
            hint = ""
            if not available:
                if name == "ollama":
                    hint = "\nثبّت Ollama ثم:  ollama pull qwen2.5:7b-instruct"
                else:
                    hint = ("\nألصق المفتاح في الحقل أعلاه ثم اضغط "
                            "«اختبار وحفظ».")
            model = getattr(provider, "model", "")
            model_line = f"  النموذج: {model}" if model else ""
        except Exception as exc:
            status, note, hint, model_line = f"خطأ: {exc}", "", "", ""
        self.rewrite_note.setText(
            f"{status} · {note}{model_line}{hint}\n"
            "النص الخام يُحفظ دائمًا، ويمكن توليد المستند منه لاحقًا.")

    def _on_model_changed(self) -> None:
        self.config.whisper.model_size = self.model_combo.currentData()
        self._refresh_estimate()

    def _on_profile_changed(self) -> None:
        """نوع التسجيل يغيّر كشف المشاهد واختيار الصور — لا التفريغ.

        تغييره يُبطل المشاهد والصور المخزّنة عبر بصمة المرحلة، فمهمة
        سبق تشغيلها تُعاد معالجتها بصريًا عند التبديل. وهو المطلوب.
        """
        self.config.application.content_profile = (
            self.profile_combo.currentData() or "slides")
        self._refresh_profile_notice()

    def _refresh_profile_notice(self) -> None:
        """يقول عند الاختيار إن كان الملفّ يحتاج Tesseract وهو غائب.

        عطلٌ حقيقي وقع على تشغيل فعلي: اختار المستخدم «شرح برنامج»،
        وهو الملفّ الوحيد الذي يفعّل OCR، وانتظر المعالجة كاملة، ثم
        لم يجد تحت الصور إلا «لقطة عند 00:00:17» — و``figure_captioning``
        صفرًا. والسبب كان سطرًا **في السجلّ**: «OCR مفعَّل في الإعداد
        لكن Tesseract غير مثبَّت». والمعلّم لا يقرأ السجلّات.

        الوقت الصحيح للقول هو لحظة الاختيار، لا بعد ساعة من المعالجة.
        وليس منعًا: المستند يخرج صحيحًا بلا OCR، وينقصه وصف الصور فقط.
        """
        from config.profiles import PROFILES
        from video.ocr import has_arabic, install_hint, refresh

        key = self.profile_combo.currentData() or ""
        profile = PROFILES.get(key)
        wants_ocr = bool(profile and profile.overrides
                         .get("frames", {}).get("enable_ocr"))
        if not wants_ocr:
            self.profile_notice.setVisible(False)
            return

        # ``refresh`` لا ``is_available``: من ثبّت Tesseract والبرنامج
        # مفتوح يستحقّ أن يراه فور تبديل نوع التسجيل، بلا إعادة تشغيل.
        if refresh() is None:
            self.profile_notice.setText(
                f"⚠ «{profile.label}» يقرأ نصّ الشاشة، و‏Tesseract غير "
                "مثبَّت. المعالجة تعمل، لكن الصور ستخرج بلا تعليق يصفها.\n"
                f"للتثبيت: {install_hint()}")
            self.profile_notice.setVisible(True)
            return

        if not has_arabic():
            # ثنائيّ موجود بلا حزمة عربية: يقرأ الإنجليزية وحدها ويُخرج
            # من الشرائح العربية حروفًا مبعثرة — عطبٌ أخفى من الغياب.
            self.profile_notice.setText(
                "⚠ ‏Tesseract مثبَّت لكن بلا حزمة اللغة العربية. نصّ "
                "الشرائح العربية سيخرج مبعثرًا.\n"
                "أعد تشغيل المثبِّت واختر Arabic ضمن Additional language "
                "data.")
            self.profile_notice.setVisible(True)
            return

        self.profile_notice.setVisible(False)

    def _on_beam_changed(self) -> None:
        self.config.whisper.beam_size = int(self.beam_combo.currentData() or 5)
        self._refresh_estimate()

    def _on_hf_token_typed(self, text: str) -> None:
        self.config.whisper.hf_token = text.strip()

    def _scan_screen_terms(self) -> None:
        """يقترح مصطلحات من نصّ شاشة الفيديو، ويتركها للمراجعة.

        **يُدمج ولا يستبدل.** ما كتبه المستخدم بيده أثمن مما يقرؤه
        الـOCR، فالمقترَح يُضاف بعده ولا يمحوه.
        """
        if self._terms_thread is not None and self._terms_thread.isRunning():
            return
        if self.video_path is None:
            QMessageBox.information(
                self, "لا ملف", "اختر ملف الفيديو أولًا.")
            return

        # الملف الصوتي لا شاشة فيه. بلا هذا السطر يمسح البرنامج
        # أربعة عشر إطارًا سوداء ثم يقول «لم يُعثر على مصطلحات» —
        # جوابٌ صحيح لسؤال خاطئ.
        if self.audio_only:
            QMessageBox.information(
                self, "ملف صوتي",
                "المصطلحات تُقرأ من صورة الفيديو، ولا صورة في ملف صوتي.")
            return

        from video.ocr import install_hint, refresh
        if refresh() is None:
            QMessageBox.information(
                self, "‏Tesseract غير مثبَّت",
                "قراءة نصّ الشاشة تحتاج Tesseract.\n\n" + install_hint())
            return

        try:
            duration = self._source_duration_seconds()
        except Exception as exc:                            # noqa: BLE001
            QMessageBox.warning(self, "تعذّر قراءة الملف",
                                format_error_for_user(exc))
            return

        self.scan_terms_button.setEnabled(False)
        self.scan_terms_status.setText("جارٍ المسح…")

        self._terms_thread = QThread(self)
        self._terms_worker = ScreenTermsWorker(self.video_path, duration)
        self._terms_worker.moveToThread(self._terms_thread)
        self._terms_thread.started.connect(self._terms_worker.run)
        self._terms_worker.progressed.connect(self._on_terms_progress)
        self._terms_worker.succeeded.connect(self._on_terms_found)
        self._terms_worker.failed.connect(
            lambda message: self.scan_terms_status.setText(message[:80]))
        self._terms_worker.finished.connect(self._terms_thread.quit)
        self._terms_thread.finished.connect(
            lambda: self.scan_terms_button.setEnabled(True))
        self._terms_thread.start()

    def _on_terms_progress(self, done: int, total: int) -> None:
        self.scan_terms_status.setText(f"جارٍ المسح… {done} من {total} إطارًا")

    def _on_terms_found(self, terms: list) -> None:
        if not terms:
            self.scan_terms_status.setText(
                "لم يُعثر على مصطلحات إنجليزية على الشاشة.")
            return

        from video.screen_terms import as_glossary
        suggested = as_glossary(terms)
        existing = self.glossary_input.toPlainText().strip()
        if existing:
            known = {part.strip().lower()
                     for part in existing.replace(",", "،").split("،")}
            fresh = [phrase for phrase, _n in terms
                     if phrase.lower() not in known]
            if not fresh:
                self.scan_terms_status.setText("لا جديد فوق ما كتبتَه.")
                return
            suggested = existing + "، " + as_glossary(
                [(phrase, 0) for phrase in fresh])

        self.glossary_input.setPlainText(suggested)
        self.scan_terms_status.setText(
            f"اقتُرح {len(terms)} مصطلحًا — احذف ما لا يُنطق في التسجيل.")

    def _build_header(self) -> QWidget:
        """شريط علوي يحمل شعار البرنامج واسمه.

        ليس زينة: هو ما يفرّق البرنامج عن نافذة أدوات عامّة، وما يجعل
        المعلّم يتعرّف عليه بين نوافذه. والشعار نفسه هو أيقونة شريط
        المهام وترويسة المستند — مصدر واحد في ``assets/logo.png``.
        """
        from PyQt6.QtGui import QPixmap

        bar = QWidget()
        bar.setStyleSheet(f"background: {theme.INK}; border-radius: 10px;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 11, 16, 11)
        row.setSpacing(12)

        mark = QLabel()
        # النسخة الفاتحة، والعلامة وحدها بلا أسطر النصّ. عطلان كانا
        # هنا وقد رآهما المستخدم معًا: الشعار مرسوم بلون الشريط نفسه
        # فلا يُرى، وأسطر النصّ عند 34 بكسل بقعٌ رمادية لا تُقرأ.
        assets = Path(__file__).resolve().parents[1] / "assets"
        logo = assets / "logo_light.png"
        if not logo.is_file():
            logo = assets / "logo.png"
        if logo.is_file():
            mark.setPixmap(QPixmap(str(logo)).scaledToHeight(
                34, Qt.TransformationMode.SmoothTransformation))
        row.addWidget(mark)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        name = QLabel("محوّل المحاضرات")
        name.setStyleSheet(
            f"color: {theme.ON_INK}; font-size: 15px; font-weight: 600;")
        note = QLabel("يعمل على جهازك بالكامل — بلا إنترنت")
        note.setStyleSheet(f"color: {theme.ON_INK_MUTED}; font-size: 11px;")
        titles.addWidget(name)
        titles.addWidget(note)
        row.addLayout(titles)
        row.addStretch(1)

        version = QLabel(APP_VERSION)
        version.setStyleSheet(f"color: {theme.ON_INK_FAINT}; font-size: 11px;")
        row.addWidget(version)
        return bar

    def _choose_logo(self) -> None:
        """شعار ترويسة المستند.

        ``DocumentConfig.logo_path`` موجود منذ البداية ويستعمله
        ``document/template.py``، ولم يكن له أي عنصر في الواجهة — أي
        أن تغييره كان يحتاج تحرير ملف YAML بيد المستخدم. الافتراضي
        شعار البرنامج، ومَن أراد شعار مدرسته يضعه من هنا.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر شعار ترويسة المستند", "",
            "الصور (*.png *.jpg *.jpeg)")
        if not path:
            return
        self.config.document.logo_path = Path(path)
        self._refresh_logo_label()

    def _clear_logo(self) -> None:
        self.config.document.logo_path = None
        self._refresh_logo_label()

    def _refresh_logo_label(self) -> None:
        chosen = self.config.document.logo_path
        self.logo_label.setText(
            chosen.name if chosen else "شعار البرنامج (الافتراضي)")

    def _on_glossary_changed(self) -> None:
        self.config.whisper.glossary = \
            self.glossary_input.toPlainText().strip()

    def _current_clip(self):
        """يقرأ نطاق المعالجة من الحقلين، أو ``None`` إن كانا فارغين."""
        from config.settings import ClipRange

        start = self.clip_start_input.text().strip()
        end = self.clip_end_input.text().strip()
        if not start and not end:
            return None
        return ClipRange.parse(start or None, end or None)

    def show_hf_help(self) -> None:
        """يشرح إنشاء الرمز بخطوات مرقّمة، ويفتح الصفحتين في المتصفّح."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        model_url = "https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026"
        token_url = "https://huggingface.co/settings/tokens"

        box = QMessageBox(self)
        box.setWindowTitle("كيف أحصل على رمز HuggingFace")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "الرمز مجاني، والخطوات ثلاث:\n\n"
            "١ · أنشئ حسابًا على huggingface.co إن لم يكن لديك.\n\n"
            "٢ · افتح صفحة النموذج واضغط زر طلب الوصول\n"
            "     (Agree and access repository) — الموافقة فورية.\n\n"
            "٣ · من صفحة الرموز اضغط New token، واختر النوع Read،\n"
            "     سمِّه أي اسم، ثم انسخ الرمز الذي يبدأ بـ hf_\n"
            "     وألصقه في الحقل أعلاه.\n\n"
            "الرمز يُعرض **مرة واحدة فقط** عند إنشائه — انسخه فورًا.\n\n"
            "اضغط «فتح الصفحتين» ليفتحهما المتصفّح لك.")
        open_button = box.addButton("فتح الصفحتين",
                                    QMessageBox.ButtonRole.ActionRole)
        box.addButton("إغلاق", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(model_url))
            QDesktopServices.openUrl(QUrl(token_url))
            self.append_log("فُتحت صفحتا النموذج والرموز في المتصفّح.")

    def _on_engine_changed(self) -> None:
        """يبدّل المحرّك ويوضّح حالته وأثره على قائمة النماذج."""
        name = self.engine_combo.currentData() or "faster-whisper"
        self.config.whisper.engine = name
        # الرمز لا معنى له لمحرّك Whisper المحلي
        self.hf_row.setVisible(name != "faster-whisper")
        ready, reason = _engine_status(name)
        # قائمة نماذج Whisper تبقى فعّالة ما لم يكن محرّك آخر **جاهزًا**
        # فعلًا: عند اختيار محرّك غير مثبّت ستتم المعالجة بـ Whisper،
        # فتعطيل اختيار نموذجه يحرم المستخدم من ضبط ما سيعمل حقًا.
        self.model_combo.setEnabled(name == "faster-whisper" or not ready)
        if name == "faster-whisper":
            self.engine_note.setText(
                "محلي بالكامل بلا PyTorch — الافتراضي الموصى به.")
        elif ready:
            self.engine_note.setText(
                f"جاهز ✓ — يعمل محليًا أيضًا، وأدق على اللهجات، لكنه أثقل.\n"
                f"{reason}")
        else:
            # السبب الفعلي لا كلمة «غير مثبّت» وحدها
            self.engine_note.setText(
                f"غير جاهز ✗\n{reason}\n"
                "حتى ذلك الحين ستتم المعالجة بـ faster-whisper.")
        self._refresh_estimate()

    def choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "اختر مجلد الحفظ",
            str(self.config.application.output_dir))
        if path:
            self.config.application.output_dir = Path(path)
            self.output_label.setText(path)
            self.append_log(f"مجلد الحفظ: {path}")
            self._save_config()

    def append_log(self, message: str) -> None:
        self.log.append(message)

    def choose_file(self) -> None:
        self._pick_source("اختر ملف فيديو", VIDEO_FILTER, audio_only=False)

    def choose_audio_file(self) -> None:
        self._pick_source("اختر ملفًا صوتيًا", AUDIO_FILTER, audio_only=True)

    def _pick_source(self, title: str, file_filter: str,
                     audio_only: bool) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, title, str(Path.home()), file_filter)
        if not path:
            return
        self.video_path = Path(path)
        self.audio_only = audio_only
        kind = "صوت" if audio_only else "فيديو"
        self.file_label.setText(f"{self.video_path.name}   ({kind})")
        self.start_button.setEnabled(True)
        self.append_log(f"تم اختيار ملف {kind}: {self.video_path.name}")
        self._refresh_estimate()
        self._refresh_rebuild_button()

    def _source_duration_seconds(self) -> float:
        """مدة الملف المختار بالثواني.

        دالّة واحدة لأن نسختين افترقتا: ``_refresh_estimate`` كانت
        تستدعي ``extract_video_facts(probe_raw(path))`` صحيحةً، ونسخة
        ``_scan_screen_terms`` مرّرت المسار نفسه — فانفجر الزرّ عند
        أوّل ضغطة بـ``'WindowsPath' object has no attribute 'get'``.
        ``extract_video_facts`` تأخذ خرج ffprobe لا مسارًا.
        """
        if self.video_path is None:
            raise ValueError("لم يُختَر ملف بعد.")
        facts = extract_video_facts(probe_raw(self.video_path),
                                    allow_audio_only=self.audio_only)
        return float(facts["duration_seconds"])

    def _refresh_estimate(self) -> None:
        """يعرض المدة المتوقعة قبل الضغط على «ابدأ».

        تفريغ محاضرة ساعة على المعالج يستغرق ساعات. بلا هذا التقدير يظن
        المستخدم أن البرنامج متجمد ويلغي عملًا سليمًا.
        """
        if self.video_path is None:
            self.estimate_label.setText("")
            return
        try:
            full_duration = self._source_duration_seconds()
        except Exception:
            self.estimate_label.setText("")
            return

        duration = full_duration
        clip_note = ""
        try:
            clip = self._current_clip()
        except ValueError:
            self.estimate_label.setText(
                "نطاق غير صالح: النهاية يجب أن تكون بعد البداية.")
            return
        if clip is not None:
            end = clip.end_seconds if clip.end_seconds is not None else full_duration
            duration = max(0.0, min(end, full_duration) - clip.start_seconds)
            clip_note = (f" (مقطع {humanize_duration(duration)} "
                         f"من أصل {humanize_duration(full_duration)})")
        if duration <= 0:
            self.estimate_label.setText("النطاق المحدّد خارج مدة الملف.")
            return

        on_gpu = GPUManager.is_cuda_available()
        device = "كرت الشاشة" if on_gpu else "المعالج"

        # الأرقام المقاسة تخصّ Whisper وحده. عرضها لمحرّك آخر يعطي
        # المستخدم توقّعًا مختلقًا — والمحرّك الثقيل على المعالج قد
        # يستغرق أضعافها.
        engine = self.engine_combo.currentData() or "faster-whisper"
        if engine != "faster-whisper":
            self.estimate_label.setText(
                f"سيُعالَج {humanize_duration(duration)}{clip_note} — "
                f"لا يوجد تقدير مقاس لمحرّك «{engine}»؛ "
                f"على {device} توقّع مدة أطول بكثير من Whisper.")
            return

        seconds = estimate_processing_seconds(
            self.model_combo.currentData(), duration, on_gpu=on_gpu)
        if int(self.beam_combo.currentData() or 5) == 1:
            # فكّ التشفير الجشع أسرع بمرّة ونصف إلى مرّتين على المعالج
            seconds /= 1.7
        if self.transcript_only_check.isChecked():
            # بلا تحليل بصري ولا بناء مستند
            seconds *= 0.9
        self.estimate_label.setText(
            f"سيُعالَج {humanize_duration(duration)}{clip_note} — "
            f"المعالجة على {device} تستغرق ~{humanize_duration(seconds)}")

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.video_path is None:
            return
        model_name = self.model_combo.currentData()
        allow_download = False

        if not self.model_manager.is_available(model_name):
            spec = self.model_manager.spec(model_name)
            answer = QMessageBox.question(
                self, "تنزيل النموذج",
                f"النموذج «{spec.label_ar}» غير مثبّت.\n\n"
                f"الحجم: ~{spec.approx_size_gb:.1f} جيجابايت\n"
                f"المسار: {self.model_manager.cache_root}\n\n"
                "هذه هي المرة الوحيدة التي يتصل فيها التطبيق بالإنترنت.\n"
                "بعدها يعمل بالكامل دون اتصال.\n\nهل تسمح بالتنزيل الآن؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer != QMessageBox.StandardButton.Yes:
                self.append_log("أُلغي التنزيل — لا يمكن المتابعة بلا نموذج.")
                return
            allow_download = True

        self.config.whisper.model_size = model_name
        self.config.rewrite.enabled = self.rewrite_check.isChecked()
        self.config.rewrite.provider = self.provider_combo.currentData()
        self.config.rewrite.api_key = self.api_key_input.text().strip()

        if self.config.rewrite.enabled:
            from ai.providers import build_provider
            provider = build_provider(
                self.config.rewrite.provider, self.config.rewrite.model,
                self.config.rewrite.base_url, self.config.rewrite.api_key_env,
                self.config.rewrite.api_key, self.config.rewrite.workspace_id)
            if not provider.is_available():
                QMessageBox.warning(
                    self, "مزوّد غير مهيّأ",
                    f"«{provider.info.label_ar}» غير جاهز.\n\n"
                    "ألصق مفتاح الوصول في حقل «مفتاح الوصول» أعلاه، "
                    "ثم اضغط «اختبار وحفظ».\n\n"
                    "ستتم المعالجة الآن بالنص الخام دون إعادة صياغة.")
            elif not provider.info.is_local:
                answer = QMessageBox.question(
                    self, "تأكيد الإرسال إلى خدمة خارجية",
                    f"سيُرسل نص التفريغ كاملًا إلى "
                    f"«{provider.info.label_ar}» لإعادة صياغته.\n\n"
                    "الفيديو والصوت والصور لا تُرسل إطلاقًا.\n"
                    "النص الخام يبقى محفوظًا على جهازك.\n\nهل توافق؟",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    self.config.rewrite.enabled = False
                    self.rewrite_check.setChecked(False)
                    self.append_log("أُلغيت إعادة الصياغة — المتابعة بالنص الخام.")

        try:
            clip = self._current_clip()
        except ValueError as exc:
            QMessageBox.warning(self, "نطاق غير صالح", str(exc))
            return

        self.cancel_token = CancellationToken()
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)

        self.thread = QThread(self)
        self.worker = PipelineWorker(
            pipeline, self.video_path, self.cancel_token, allow_download,
            allow_audio_only=self.audio_only, clip=clip,
            transcript_only=self.transcript_only_check.isChecked())
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_buttons)

        self._set_running(True)
        self.append_log("بدأت المعالجة…")
        if self.estimate_label.text():
            self.append_log(self.estimate_label.text())
        self.append_log("يمكنك الإيقاف المؤقت أو الإلغاء في أي وقت — "
                        "المراحل المكتملة تُحفظ وتُستأنف لاحقًا.")
        self.thread.start()

    # ------------------------------------------------------------------
    def choose_folder(self) -> None:
        """يعالج كل ملفات الوسائط في مجلد، واحدًا تلو الآخر."""
        from core.batch import iter_media

        folder = QFileDialog.getExistingDirectory(
            self, "اختر مجلد الوسائط", str(Path.home()))
        if not folder:
            return
        sources = iter_media(Path(folder))
        if not sources:
            QMessageBox.information(
                self, "لا توجد ملفات",
                "لم يُعثر على ملفات فيديو أو صوت في هذا المجلد.")
            return

        answer = QMessageBox.question(
            self, "معالجة دفعية",
            f"سيُعالَج {len(sources)} ملفًا واحدًا تلو الآخر:\n\n"
            + "\n".join(f"  • {p.name}" for p in sources[:8])
            + (f"\n  … و{len(sources) - 8} غيرها" if len(sources) > 8 else "")
            + "\n\nفشل ملف لا يوقف البقية. هل نبدأ؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_batch(sources)

    def _start_batch(self, sources) -> None:
        from ui.worker import BatchWorker

        try:
            clip = self._current_clip()
        except ValueError as exc:
            QMessageBox.warning(self, "نطاق غير صالح", str(exc))
            return

        self.cancel_token = CancellationToken()
        self.thread = QThread(self)
        self.worker = BatchWorker(
            sources, self.config.application.output_dir, self.config,
            self.cancel_token,
            allow_model_download=False,
            transcript_only=self.transcript_only_check.isChecked(),
            clip=clip)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.file_done.connect(self.on_batch_file_done)
        self.worker.completed.connect(self.on_batch_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_buttons)

        self._set_running(True)
        self.append_log(f"بدأت معالجة {len(sources)} ملفًا…")
        self.thread.start()

    def on_batch_file_done(self, name: str, ok: bool, detail: str) -> None:
        self.append_log(f"{'✓' if ok else '✗'} {name} — {detail}")

    def on_batch_completed(self, succeeded: int, total: int) -> None:
        self.progress.setValue(100)
        self.status_label.setText(f"اكتملت الدفعة: {succeeded} من {total}.")
        QMessageBox.information(
            self, "اكتملت الدفعة",
            f"نجح {succeeded} من {total}.\n"
            f"المخرجات في:\n{self.config.application.output_dir}")

    # ------------------------------------------------------------------
    def show_jobs(self) -> None:
        from ui.jobs_dialog import JobsDialog

        dialog = JobsDialog(self.config.application.output_dir, self,
                            on_resume=self._resume_job,
                            on_rebuild=self._rebuild_job)
        dialog.exec()

    def _rebuild_job(self, job) -> None:
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            result = pipeline.rebuild_document(job.job_dir)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّرت إعادة البناء", str(exc))
            return
        self.append_log(f"أُعيد بناء المستند: {result}")
        QMessageBox.information(self, "تم", f"أُعيد بناء المستند:\n{result}")

    def _resume_job(self, job) -> None:
        """يعيد اختيار مصدر المهمة ويبدأ — الاستئناف يتكفّل بالباقي."""
        if job.source is None or not job.source.exists():
            QMessageBox.warning(self, "المصدر مفقود",
                                "لم يعد ملف المصدر في مكانه.")
            return
        from core.batch import is_audio

        self.video_path = job.source
        self.audio_only = is_audio(job.source)
        self.file_label.setText(
            f"{job.source.name}   ({'صوت' if self.audio_only else 'فيديو'})")
        self.start_button.setEnabled(True)
        self.append_log(f"استئناف: {job.name} (من {job.stage or 'البداية'})")
        self.start()

    def merge_jobs(self) -> None:
        """يدمج مهامّ منتهية في مستند واحد."""
        from core.job_registry import list_jobs

        jobs = [j for j in list_jobs(self.config.application.output_dir)
                if j.can_rebuild]
        if len(jobs) < 2:
            QMessageBox.information(
                self, "لا يكفي",
                "الدمج يحتاج مهمّتين منتهيتين على الأقل في مجلد الحفظ.")
            return

        from PyQt6.QtWidgets import QInputDialog

        names = [j.name for j in jobs]
        chosen, ok = QInputDialog.getItem(
            self, "دمج مهام",
            "الدمج يشمل كل المهام المنتهية بترتيب آخر تحديث.\n"
            "اختر اسم المستند الناتج:",
            [f"مستند مدموج — {len(jobs)} أجزاء"] + names, 0, True)
        if not ok or not chosen:
            return

        target = (self.config.application.output_dir
                  / f"{chosen.replace('/', '-')}.docx")
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            result = pipeline.merge_documents([j.job_dir for j in jobs], target)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّر الدمج", str(exc))
            return
        self.result_path = result
        self.open_button.setEnabled(True)
        self.append_log(f"مستند مدموج: {result}")
        QMessageBox.information(self, "تم", f"أُنشئ المستند المدموج:\n{result}")

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.cancel_button.setEnabled(running)
        if running:
            self.open_button.setEnabled(False)
            self.rebuild_button.setEnabled(False)
            self.progress.setValue(0)

    def _reset_buttons(self) -> None:
        self._set_running(False)
        self.start_button.setEnabled(self.video_path is not None)
        self.pause_button.setText("إيقاف مؤقت")
        self._refresh_rebuild_button()

    def toggle_pause(self) -> None:
        if self.cancel_token is None:
            return
        if self.cancel_token.is_paused():
            self.cancel_token.resume()
            self.pause_button.setText("إيقاف مؤقت")
            self.append_log("استُؤنفت المعالجة.")
        else:
            self.cancel_token.pause()
            self.pause_button.setText("استئناف")
            self.append_log("أُوقفت المعالجة مؤقتًا.")

    def cancel(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.status_label.setText("جارٍ الإلغاء…")

    # ------------------------------------------------------------------
    def on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(int(percent))
        self.status_label.setText(message)

    def on_completed(self, path: str) -> None:
        self.result_path = Path(path)
        self.progress.setValue(100)
        self.status_label.setText("اكتملت المعالجة بنجاح.")
        self.open_button.setEnabled(True)
        self.append_log(f"تم إنشاء المستند: {path}")
        QMessageBox.information(self, "اكتملت المعالجة",
                                f"تم إنشاء المستند:\n{path}")

    def on_failed(self, error: str) -> None:
        self.status_label.setText("فشلت المعالجة.")
        self.append_log(f"خطأ: {error}")
        QMessageBox.critical(self, "خطأ", error)

    def on_cancelled(self) -> None:
        self.status_label.setText("أُلغيت المعالجة. يمكن استئنافها لاحقًا.")
        self.append_log("أُلغيت المعالجة — المراحل المكتملة محفوظة.")

    def rebuild_document(self) -> None:
        """يعيد توليد المستند من الخطة المحفوظة بالإعدادات الحالية."""
        if self.video_path is None:
            return
        pipeline = VideoToDocPipeline(
            self.config.application.output_dir, self.config)
        try:
            job_dir = pipeline.job_dir_for(self.video_path,
                                           self._current_clip())
        except ValueError:
            job_dir = pipeline.job_dir_for(self.video_path)
        self.rebuild_button.setEnabled(False)
        try:
            result = pipeline.rebuild_document(job_dir)
        except Exception as exc:
            QMessageBox.warning(self, "تعذّرت إعادة البناء", str(exc))
            self.append_log(f"تعذّرت إعادة البناء: {exc}")
            return
        finally:
            self.rebuild_button.setEnabled(True)
        self.result_path = result
        self.open_button.setEnabled(True)
        self.append_log(f"أُعيد بناء المستند: {result}")
        QMessageBox.information(self, "تم", f"أُعيد بناء المستند:\n{result}")

    def _refresh_rebuild_button(self) -> None:
        """يُفعَّل الزر فقط إن وُجدت خطة محفوظة لهذا الفيديو."""
        enabled = False
        if self.video_path is not None:
            pipeline = VideoToDocPipeline(
                self.config.application.output_dir, self.config)
            try:
                job_dir = pipeline.job_dir_for(self.video_path,
                                               self._current_clip())
            except ValueError:
                job_dir = pipeline.job_dir_for(self.video_path)
            enabled = (job_dir / "plan.json").exists()
        self.rebuild_button.setEnabled(enabled)

    def open_result(self) -> None:
        if self.result_path is None or not self.result_path.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(self.result_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(self.result_path)])
        else:
            subprocess.run(["xdg-open", str(self.result_path)])

    def _warn_if_config_failed_to_load(self) -> None:
        """إعدادٌ تالف يُعاد إلى الافتراضيات — والمستخدم يستحقّ أن يعرف.

        ``AppConfig.load`` تحتفظ بالسبب في ``load_error`` منذ جولة سابقة،
        وكان **سطر الأوامر وحده** يعرضه (``tools/run_pipeline.py:78``).
        والمعلّم لا يستعمل سطر الأوامر: يفتح البرنامج فيجد نموذجه ومحرّكه
        ومفاتيحه عادت إلى الافتراضي بلا سبب ظاهر، ويظنّ البرنامج نسي.
        """
        message = getattr(self.config, "load_error", "")
        if not message:
            return
        QMessageBox.warning(self, "تعذّرت قراءة الإعدادات", message)

    def _confirm_close_while_running(self) -> bool:
        """هل يؤكّد المستخدم إغلاق النافذة ومهمّة جارية؟

        كان الإغلاق يُلغي بصمت. محاضرة ثلاث ساعات تُفرَّغ في نحو ساعة،
        ونقرةٌ على «×» كانت تُلغيها بلا سؤال. والمراحل المكتملة محفوظة
        فعلًا، فالسؤال يقول ذلك بدل أن يخوّف.
        """
        answer = QMessageBox.question(
            self, "المعالجة ما تزال جارية",
            "هناك معالجة جارية. الإغلاق الآن يوقفها.\n\n"
            "المراحل المكتملة محفوظة، وإعادة التشغيل على الملف نفسه "
            "تُكمل من حيث توقّفت.\n\nهل تريد الإغلاق؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        """إغلاق نظيف: تأكيد، ثم حفظ الإعداد، ثم إلغاء تعاوني."""
        running = self.thread is not None and self.thread.isRunning()
        if running and not self._confirm_close_while_running():
            event.ignore()
            return

        # الإعداد كان يُحفظ في موضع واحد فقط (بعد نجاح اختبار المفتاح)،
        # فتضيع كل خيارات المستخدم — النموذج والمحرّك والمزوّد — عند الإغلاق.
        self._save_config()
        if running:
            if self.cancel_token is not None:
                self.cancel_token.cancel()
            self.thread.quit()
            self.thread.wait(10000)
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(3000)
        event.accept()
