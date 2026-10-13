"""انحدار على تخطيط الواجهة.

سبب وجود هذا الملف: مع نمو الواجهة (محرّكات، مفاتيح، خيارات متقدّمة،
لوحة مهام) تجاوز مجموعُ الارتفاعات الدنيا ارتفاعَ شاشات كثيرة، فضغط
‏Qt المجموعاتِ فوق بعضها ورُسمت الصفوف على بعضها — واجهة غير مقروءة
بلا أي خطأ في السجلّ.

الاختبار يبني الواجهة فعليًا بلا شاشة (‏offscreen) عند ثلاثة ارتفاعات،
ويؤكّد أن المجموعات لا تتقاطع وأن شريط التمرير يظهر حين يلزم. البناء
النحوي وحده لا يمسك هذا العطل إطلاقًا.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ‏importorskip على الحزمة الجذر لا يكفي: ``import PyQt6`` ينجح بلا أي
# مكتبة نظام، والانهيار يقع عند ``QtWidgets`` التي تحتاج libEGL/libGL —
# وهي غائبة عن متتبّع ubuntu العاري، فسقطت جمعُ الاختبارات كلها بـ
# ImportError بدل تخطّي هذا الملف وحده. الحارس على الوحدة الفعلية.
# (‏CI يثبّت مكتبات Qt فعلًا، فالتخطّي هنا للأجهزة العارية لا لإخفاء عطل.)
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QGroupBox, QScrollArea  # noqa: E402

from config.settings import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(app):
    from ui.main_window import MainWindow

    win = MainWindow(AppConfig())
    win.show()
    yield win
    win.close()


HOME, SETTINGS = 0, 1


def _page(win, index) -> QScrollArea:
    return win.pages.widget(index)


def _top_level_boxes(win, index):
    inner = _page(win, index).widget()
    boxes = [b for b in inner.findChildren(QGroupBox) if b.parent() is inner]
    return sorted(boxes, key=lambda b: b.y())


@pytest.mark.parametrize("height", [480, 600, 760, 1000])
@pytest.mark.parametrize("index,minimum", [(HOME, 2), (SETTINGS, 4)])
def test_sections_never_overlap(app, window, height, index, minimum):
    """أسوأ حالة: كل الصفوف الاختيارية ظاهرة."""
    window.rewrite_check.setChecked(True)
    window.resize(900, height)
    window.show_page(index)
    app.processEvents()

    boxes = _top_level_boxes(window, index)
    assert len(boxes) >= minimum, "مجموعات الواجهة مفقودة"
    for upper, lower in zip(boxes, boxes[1:]):
        assert upper.y() + upper.height() <= lower.y(), (
            f"تراكب عند ارتفاع {height}: "
            f"«{upper.title()}» فوق «{lower.title()}»")


def test_every_page_lives_in_a_scroll_area(window):
    """التمرير هو ما يجعل الانهيار مستحيلًا مهما أُضيف لاحقًا."""
    assert window.pages.count() == 2
    for index in (HOME, SETTINGS):
        assert isinstance(_page(window, index), QScrollArea)
        assert _page(window, index).widgetResizable() is True


def test_scrollbar_appears_when_content_exceeds_the_window(app, window):
    window.rewrite_check.setChecked(True)
    window.resize(900, 480)
    window.show_page(SETTINGS)
    app.processEvents()
    bar = _page(window, SETTINGS).verticalScrollBar()
    assert bar.maximum() > 0, "المحتوى أطول من النافذة بلا مدى تمرير"


def test_optional_rows_are_hidden_not_just_disabled(app, window):
    """العنصر المعطّل يشغل ارتفاعه كاملًا ويدفع بقية الواجهة."""
    window.show_page(SETTINGS)
    window.rewrite_check.setChecked(False)
    app.processEvents()
    assert window.provider_row.isVisible() is False
    assert window.key_row.isVisible() is False
    assert window.workspace_row.isVisible() is False

    window.rewrite_check.setChecked(True)
    app.processEvents()
    assert window.provider_row.isVisible() is True


def test_decoding_choice_is_offered(window):
    """فكّ التشفير الجشع أسرع ~2× — فارق ساعات على محاضرة طويلة."""
    values = [window.beam_combo.itemData(i)
              for i in range(window.beam_combo.count())]
    assert set(values) == {5, 1}
    window.beam_combo.setCurrentIndex(values.index(1))
    assert window.config.whisper.beam_size == 1


def test_window_minimum_fits_a_small_screen(window):
    assert window.minimumHeight() <= 500, "حدّ أدنى يمنع الاستخدام على لابتوب"


def test_home_holds_source_output_and_processing_settings_hold_the_rest(window):
    """الرئيسية: المصدر ومجلد الحفظ والتشغيل والإحصاءات؛ والباقي في الإعدادات."""
    home = _page(window, HOME).widget()
    settings = _page(window, SETTINGS).widget()
    titles = lambda w: {b.title() for b in w.findChildren(QGroupBox)}  # noqa: E731
    assert {"المصدر ومجلد الحفظ", "المعالجة"} <= titles(home)
    assert {"نموذج التفريغ", "صياغة النص (اختياري)", "خيارات متقدّمة",
            "الخصوصية والإحصاءات"} <= titles(settings)
    assert home.isAncestorOf(window.dashboard)
    assert home.isAncestorOf(window.start_button)
    assert settings.isAncestorOf(window.model_combo)
    assert settings.isAncestorOf(window.api_key_input)


def test_sidebar_switches_pages(app, window):
    window.nav_buttons[SETTINGS].click()
    app.processEvents()
    assert window.pages.currentIndex() == SETTINGS
    assert window.nav_buttons[SETTINGS].isChecked()
    assert not window.nav_buttons[HOME].isChecked()
    window.nav_buttons[HOME].click()
    assert window.pages.currentIndex() == HOME


def test_log_is_folded_until_asked_for(app, window):
    assert window.log.isVisible() is False
    window.log_toggle.click()
    app.processEvents()
    assert window.log.isVisible() is True
