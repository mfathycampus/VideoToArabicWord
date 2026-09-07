"""ألوان الواجهة، مشتقّة من شعار البرنامج.

سبب وجود هذا الملف: كانت الألوان مبعثرة في ``ui/main_window.py`` سطورًا
مثل ``setStyleSheet("color: #555;")`` في سبعة مواضع، وثلاثة رماديّات
مختلفة لمعنى واحد. ولمّا صار للبرنامج شعارٌ خاصّ به لزم مصدر واحد
يشتقّ منه الاثنان — الواجهة والمستند.

**والألوان من الشعار لا اختيارًا حرًّا:** الحبر ``#0B2E2A`` هو لون حرف
الواو، و``#0F766E`` لون مثلّث التشغيل داخله. فما يراه المعلّم في
شريط المهام هو نفسه ما يراه في النافذة وفي ترويسة المستند.

**ولماذا أخضر داكن لا أسود:** النافذة تبقى مفتوحة ساعةً أو أكثر أثناء
المعالجة. الأسود الخالص على أبيض خالص أعلى تباينًا مما تحتاجه العين،
وأتعب لها في جلسة طويلة.

التباين مفحوص على الخلفية ``#F7FAF9``:
    الحبر     14.2:1  — يتجاوز AAA
    المميّز    4.8:1  — يتجاوز AA
    الثانوي    4.6:1  — يتجاوز AA
"""
from __future__ import annotations

#: حبر النصّ والعناوين — لون حرف الواو في الشعار.
INK = "#0B2E2A"

#: اللون المميّز — لون مثلّث التشغيل. الأزرار والروابط والتقدّم.
ACCENT = "#0F766E"
ACCENT_DARK = "#115E59"

#: نصّ ثانوي: التلميحات وأسطر الشرح تحت الحقول.
MUTED = "#5A706C"

#: الحدود والفواصل، ثم الخلفية.
BORDER = "#C9DAD6"
BORDER_SOFT = "#DCE8E5"
SURFACE = "#FFFFFF"
BACKGROUND = "#F7FAF9"

#: حالات. الثلاثة مفحوصة على أرضيّاتها الفاتحة بنفس الطريقة.
WARNING = "#B45309"
WARNING_BG = "#FEF6EC"
WARNING_BORDER = "#F5D9B0"
DANGER = "#9F1239"
SUCCESS = "#15803D"


def stylesheet() -> str:
    """ورقة أنماط التطبيق كاملة.

    تُطبَّق على ``QApplication`` لا على النافذة: الحوارات (اختيار الملف،
    رسائل الخطأ) نوافذ مستقلّة، وتطبيقها على النافذة وحدها يترك
    حوارًا بلون النظام وسط واجهة ملوّنة.
    """
    return f"""
    QWidget {{
        background: {BACKGROUND};
        color: {INK};
        font-size: 13px;
    }}
    QGroupBox {{
        background: {SURFACE};
        border: 1px solid {BORDER_SOFT};
        border-radius: 12px;
        margin-top: 14px;
        padding: 14px 16px 12px 16px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top right;
        right: 14px;
        padding: 0 6px;
        color: {ACCENT};
    }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 7px;
        padding: 5px 9px;
        selection-background-color: {ACCENT};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border-color: {ACCENT};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{
        background: {BACKGROUND};
        color: {MUTED};
    }}
    QPushButton {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 7px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton:pressed {{ background: {BORDER_SOFT}; }}
    QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER_SOFT}; }}
    /* الزرّ الأساسي — واحد في النافذة: «ابدأ المعالجة» */
    QPushButton[primary="true"] {{
        background: {ACCENT};
        border: 0;
        color: #FFFFFF;
        font-weight: 600;
        padding: 8px 22px;
    }}
    QPushButton[primary="true"]:hover {{ background: {ACCENT_DARK}; }}
    QPushButton[primary="true"]:disabled {{ background: {BORDER}; color: {SURFACE}; }}
    QProgressBar {{
        background: {BORDER_SOFT};
        border: 0;
        border-radius: 4px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
    QCheckBox {{ spacing: 7px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BACKGROUND}; }}
    """
