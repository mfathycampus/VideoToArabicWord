"""ملفّات المحتوى — استراتيجية لكل نوع تسجيل.

القصة: البرنامج كان يملك مجموعة إعدادات واحدة معايرة على **محاضرة
بشرائح**، ويطبّقها على كل ما يصله. والقياس أثبت أن ذلك لا يعمل:

    مصفوفة الشرائح الاصطناعية        5 مشاهد
    تسجيل شرح برنامج 8.5 دقائق     131 مشهدًا     ← الإعدادات نفسها

فخرج مستند فيه 35 صورة مقابل 31 شظية نصّية — صورة كل خمس عشرة ثانية.

وبتطبيق ملفّ ``screencast`` على **الفيديو نفسه**:

    الصور              35 ← 15
    درجة القراءة    13.35 ← 39.46
    تعليق الصور       0% ← 100%
"""
from __future__ import annotations

import pytest

from config.profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    apply_profile,
    profile_choices,
)
from config.settings import AppConfig


def _base() -> AppConfig:
    return AppConfig()


# ── العقد الأساسي ────────────────────────────────────────────────────

def test_every_profile_has_a_label_and_honest_calibration():
    """كل ملفّ يقول بصراحة إن كانت قيمه مقيسة أم مُستنتَجة.

    تقديم قيم مُستنتَجة على أنها مقيسة هو ما يجعل المستخدم يثق بما لا
    يستحقّ الثقة.
    """
    for profile in PROFILES.values():
        assert profile.label.strip()
        assert profile.description.strip()
        assert profile.calibration in {"measured", "reasoned"}


def test_the_default_is_the_profile_the_app_was_calibrated_for():
    assert DEFAULT_PROFILE == "slides"
    assert PROFILES[DEFAULT_PROFILE].measured
    assert _base().application.content_profile == DEFAULT_PROFILE


def test_the_slides_profile_changes_nothing():
    """الشرائح هي الإعدادات الافتراضية نفسها — لا إزاحة صامتة."""
    base = _base()
    assert apply_profile(base, "slides") is base


def test_an_unknown_profile_never_blocks_startup():
    """ملف إعداد من إصدار أحدث لا يجوز أن يمنع الإقلاع."""
    base = _base()
    assert apply_profile(base, "لا-يوجد") is base
    assert apply_profile(base, "") is base


def test_applying_a_profile_does_not_mutate_the_original():
    base = _base()
    original_gap = base.scene_detection.minimum_gap_seconds
    apply_profile(base, "screencast")
    assert base.scene_detection.minimum_gap_seconds == original_gap


def test_choices_are_offered_for_the_user_interface():
    keys = [key for key, _ in profile_choices()]
    assert keys == list(PROFILES)
    assert all(label.strip() for _, label in profile_choices())


# ── ملفّ شرح البرنامج: القيم مقيسة ───────────────────────────────────

def test_screencast_closes_the_major_change_escape_hatch():
    """المفتاح الحقيقي، وقد وجده القياس لا الحدس.

    ``allow_major_change_before_gap`` وُضع ليمنع تفويت قطع حقيقي يقع
    بعد قطع آخر مباشرةً. وعلى تسجيل شاشة يتغيّر باستمرار يصير هو المسار
    الغالب فيتجاوز حدّ الفجوة دائمًا:

        رفع الفجوة إلى 12ث وحده     131 ← 111 مشهدًا
        إغلاق المخرج وحده           131 ←  77 مشهدًا
        الاثنان معًا                131 ←  30 مشهدًا
    """
    config = apply_profile(_base(), "screencast")
    assert config.scene_detection.allow_major_change_before_gap is False
    assert config.scene_detection.minimum_gap_seconds == 12.0


def test_screencast_enables_screen_text_extraction():
    """نصّ الواجهة **هو** المحتوى في شرح برنامج.

    بلا OCR تصير الصور لقطات صامتة: قياس على مادّة حقيقية أعطى
    ``figure_captioning`` صفرًا؛ ومعه صارت 100٪.
    """
    config = apply_profile(_base(), "screencast")
    assert config.frames.enable_ocr is True
    # فجوة الصور تطابق فجوة المشاهد: صورتان بينهما ثوانٍ تُظهران
    # الشاشة نفسها بفرق نقرة.
    assert config.frames.min_seconds_between_keyframes == \
        config.scene_detection.minimum_gap_seconds


def test_screencast_prompt_mentions_the_english_interface_terms():
    config = apply_profile(_base(), "screencast")
    assert "برنامج" in config.whisper.initial_prompt


# ── الملفّات المُستنتَجة: تُعلن عن نفسها ─────────────────────────────

@pytest.mark.parametrize("key", ["whiteboard", "talking_head"])
def test_unmeasured_profiles_declare_themselves(key):
    """لا مادّة من هذين النوعين بعد — والقيم مُستنتَجة لا مقيسة."""
    assert PROFILES[key].calibration == "reasoned"


def test_whiteboard_lowers_the_bar_for_gradual_change():
    """السبورة تمتلئ سطرًا سطرًا: التغيّر بين لحظتين ضئيل دائمًا."""
    config = apply_profile(_base(), "whiteboard")
    base = _base()
    assert config.scene_detection.absolute_floor < \
        base.scene_detection.absolute_floor
    assert config.scene_detection.adaptive_k < base.scene_detection.adaptive_k
    # ومع ذلك لا نلتقط صورًا متقاربة: الصورة المفيدة بعد اكتمال فكرة
    assert config.scene_detection.minimum_gap_seconds >= 25.0
    # حركة اليد تجعل الإطار غير مستقرّ باستمرار
    assert config.frames.max_instability > base.frames.max_instability


def test_talking_head_produces_a_text_first_document():
    """الصور في وجه متكلّم لا تحمل معلومة — لكن لا نمنعها تمامًا."""
    config = apply_profile(_base(), "talking_head")
    assert 0 < config.frames.max_keyframes <= 20
    assert config.document.section_minutes > _base().document.section_minutes


# ── التكامل مع الـpipeline ───────────────────────────────────────────

def test_the_pipeline_applies_the_profile_from_config(tmp_path):
    from core.pipeline import VideoToDocPipeline

    config = AppConfig()
    config.application.content_profile = "screencast"
    pipeline = VideoToDocPipeline(tmp_path, config)

    assert pipeline.config.scene_detection.minimum_gap_seconds == 12.0
    assert pipeline.scene_detector.config.minimum_gap_seconds == 12.0
    assert pipeline.keyframe_selector.config.enable_ocr is True


def test_changing_the_profile_invalidates_cached_stages(tmp_path):
    """تغيير الملفّ يجب أن يُعيد بناء المشاهد والصور — لأنه يغيّرها فعلًا.

    البصمة تُحسب من قيم الإعداد **بعد** تطبيق الملفّ، فلا حاجة إلى
    مسار خاص: المشاهد المخزّنة تُبطَل تلقائيًا.
    """
    from core.job_state import Stage
    from core.pipeline import VideoToDocPipeline

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 2048)

    slides = AppConfig()
    screencast = AppConfig()
    screencast.application.content_profile = "screencast"

    a = VideoToDocPipeline(tmp_path, slides)._stage_fp(
        Stage.SCENE_DETECTION, video, None)
    b = VideoToDocPipeline(tmp_path, screencast)._stage_fp(
        Stage.SCENE_DETECTION, video, None)
    assert a != b


def test_the_profile_survives_a_config_round_trip(tmp_path):
    """الاختيار يُحفظ: المعلّم لا يعيد اختياره مع كل ملف."""
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.application.content_profile = "whiteboard"
    config.save(path)

    assert AppConfig.load(path).application.content_profile == "whiteboard"
