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

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


def _top_level_boxes(win):
    inner = win.centralWidget().widget()
    boxes = [b for b in inner.findChildren(QGroupBox) if b.parent() is inner]
    return sorted(boxes, key=lambda b: b.y())


@pytest.mark.parametrize("height", [480, 600, 760, 1000])
def test_sections_never_overlap(app, window, height):
    """أسوأ حالة: كل الصفوف الاختيارية ظاهرة."""
    window.rewrite_check.setChecked(True)
    window.resize(900, height)
    app.processEvents()

    boxes = _top_level_boxes(window)
    assert len(boxes) >= 4, "مجموعات الواجهة الرئيسية مفقودة"
    for upper, lower in zip(boxes, boxes[1:]):
        assert upper.y() + upper.height() <= lower.y(), (
            f"تراكب عند ارتفاع {height}: "
            f"«{upper.title()}» فوق «{lower.title()}»")


def test_content_lives_in_a_scroll_area(window):
    """التمرير هو ما يجعل الانهيار مستحيلًا مهما أُضيف لاحقًا."""
    assert isinstance(window.centralWidget(), QScrollArea)
    assert window.centralWidget().widgetResizable() is True


def test_scrollbar_appears_when_content_exceeds_the_window(app, window):
    window.rewrite_check.setChecked(True)
    window.resize(900, 480)
    app.processEvents()
    bar = window.centralWidget().verticalScrollBar()
    assert bar.maximum() > 0, "المحتوى أطول من النافذة بلا مدى تمرير"


def test_optional_rows_are_hidden_not_just_disabled(app, window):
    """العنصر المعطّل يشغل ارتفاعه كاملًا ويدفع بقية الواجهة."""
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
