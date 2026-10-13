"""لوحة الإحصاءات في الصفحة الرئيسية — ما أنجزه المستخدم بمعين على جهازه.

البيانات من ``core.stats`` (سجلّ المهامّ + مسح مجلد الإخراج) وتُجمع في خيطٍ
خلفي: مجلد إخراج فيه مئات المهامّ يُقرأ منه ``plan.json`` لكل مهمّة لم
تُسجَّل، ولا يجوز أن تتجمّد النافذة لذلك عند الإقلاع.

الرسوم مرسومة بـ ``QPainter`` لا بمكتبة رسوم: سلسلةٌ واحدة في كل مخطّط
بلون العلامة، وأعمدة رفيعة بحوافّ مستديرة، وشبكة خافتة، وتلميحٌ عند
المرور على كل عمود يذكر قيمته — فالقيمة لا تُعرف باللون وحده.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.stats import UsageStats
from ui import theme

ARABIC_MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
                 "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
FORMAT_LABELS = {
    ".docx": "Word", ".html": "صفحة ويب", ".pdf": "PDF", ".pptx": "شرائح",
    ".srt": "ترجمة", ".scorm.zip": "SCORM", ".study.html": "دليل مذاكرة",
    ".flashcards.csv": "بطاقات",
}
FEATURE_LABELS = {"rewrite": "صياغة بالذكاء الاصطناعي", "vision": "الوضع المرئي",
                  "study": "الحزمة التعليمية", "audio_only": "ملفات صوتية"}
STATUS_TEXT = {"completed": ("✓ اكتملت", theme.SUCCESS),
               "failed": ("✕ فشلت", theme.DANGER),
               "cancelled": ("— أُلغيت", theme.MUTED)}


def _num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}" if digits else f"{int(round(value)):,}"


def _month_label(key: str) -> str:
    try:
        return ARABIC_MONTHS[int(key[5:7]) - 1]
    except (ValueError, IndexError):
        return key


def _day_label(iso: str) -> str:
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{when.day} {ARABIC_MONTHS[when.month - 1]}"


# ── عناصر ─────────────────────────────────────────────────────────────
class _Card(QFrame):
    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet(
            f"#statCard {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER_SOFT}; border-radius: 12px; }}"
            f"#statCard QLabel {{ background: transparent; }}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(6)
        if title:
            heading = QLabel(title)
            heading.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;"
                                  " font-weight: 600;")
            self.body.addWidget(heading)


class StatTile(_Card):
    """رقمٌ بارز مع تسميته وسطرٍ ثانوي."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__("", parent)
        self.value = QLabel("—")
        self.value.setStyleSheet(f"color: {theme.INK}; font-size: 24px;"
                                 " font-weight: 700;")
        self.label = QLabel(label)
        self.label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        self.body.addWidget(self.value)
        self.body.addWidget(self.label)
        self.body.addWidget(self.note)

    def set(self, value: str, note: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)
        self.note.setVisible(bool(note))


class BarChart(QWidget):
    """أعمدة لسلسلة واحدة، مع تلميح قيمة عند المرور."""

    def __init__(self, unit: str = "", max_value: Optional[float] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.unit = unit
        self.max_value = max_value
        self.items: List[Tuple[str, float]] = []
        self._bars: List[Tuple[QRectF, str]] = []
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_items(self, items: List[Tuple[str, float]]) -> None:
        self.items = list(items)
        self.update()

    def paintEvent(self, _event) -> None:   # noqa: N802 - Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(8, 20, -8, -24)
        self._bars = []
        if not self.items:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "لا بيانات بعد")
            return
        top = self.max_value or max((v for _, v in self.items), default=0) or 1.0
        grid = QPen(QColor(theme.BORDER_SOFT))
        grid.setWidthF(1.0)
        painter.setPen(grid)
        for i in range(4):
            y = rect.bottom() - rect.height() * i / 3
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        slot = rect.width() / len(self.items)
        width = max(6.0, min(28.0, slot * 0.5))
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 2))
        painter.setFont(small)
        # تسميات القيم انتقائية: كلها إن قلّت الأعمدة، وإلا الأخيرة والقصوى.
        values = [v for _, v in self.items]
        peak = values.index(max(values)) if values else -1
        labelled = (set(range(len(values))) if len(values) <= 6
                    else {len(values) - 1, peak})
        # الترتيب الزمني من اليسار لليمين حتى في واجهة RTL: المحور زمنيّ.
        for index, (label, value) in enumerate(self.items):
            cx = rect.left() + slot * (index + 0.5)
            height = rect.height() * (value / top) if value > 0 else 0.0
            bar = QRectF(cx - width / 2, rect.bottom() - height, width, height)
            if height > 0:
                path = QPainterPath()
                radius = min(4.0, width / 2, height)
                path.addRoundedRect(bar.adjusted(0, 0, 0, radius), radius, radius)
                painter.save()
                painter.setClipRect(QRectF(rect.left(), rect.top(),
                                           rect.width(), rect.height()))
                painter.fillPath(path, QColor(theme.ACCENT))
                painter.restore()
            if index in labelled and value > 0:
                painter.setPen(QColor(theme.INK))
                painter.drawText(QRectF(cx - slot / 2, rect.bottom() - height - 18,
                                        slot, 16),
                                 Qt.AlignmentFlag.AlignCenter, self._short(value))
            hit = QRectF(cx - slot / 2, rect.top(), slot, rect.height() + 20)
            text = f"{label}: {self._format(value)}"
            self._bars.append((hit, text))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QRectF(cx - slot / 2, rect.bottom() + 4, slot, 18),
                             Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

    @staticmethod
    def _short(value: float) -> str:
        return _num(value, 1) if value != int(value) and value < 10 else _num(value)

    def _format(self, value: float) -> str:
        digits = 1 if value and value < 10 and value != int(value) else 0
        return f"{_num(value, digits)} {self.unit}".strip()

    def mouseMoveEvent(self, event) -> None:   # noqa: N802 - Qt
        point = event.position()
        for hit, text in self._bars:
            if hit.contains(point):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()

    def accessible_summary(self) -> str:
        return "، ".join(f"{label} {self._format(v)}" for label, v in self.items)


# ── الجمع في الخلفية ─────────────────────────────────────────────────
# خيط Python لا ``QThread``: نافذةٌ تُغلق (أو تُجمع في الذاكرة) أثناء الجمع
# تُسقط العملية كلّها إن كان ``QThread`` ما زال يعمل. هنا يُكمل الخيط عمله
# ويُرسل النتيجة عبر إشارة إلى كائن في خيط الواجهة؛ إن حُذف الكائن قبلها
# تُهمل النتيجة بهدوء.
class _Bridge(QObject):
    done = pyqtSignal(object)


# ── اللوحة ───────────────────────────────────────────────────────────
class StatsDashboard(QWidget):
    """إحصاءات الصفحة الرئيسية. ``refresh`` يعيد الجمع في الخلفية."""

    def __init__(self, collect: Callable[[], UsageStats],
                 on_show_jobs: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._collect = collect
        self._thread: Optional[threading.Thread] = None
        self._pending = False
        self._bridge = _Bridge(self)
        self._bridge.done.connect(self._on_collected)
        self.stats: Optional[UsageStats] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("إحصاءاتك")
        title.setStyleSheet(f"color: {theme.ACCENT}; font-size: 15px; font-weight: 700;")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        head.addWidget(title)
        head.addWidget(self.subtitle, 1)
        refresh = QPushButton("تحديث")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        if on_show_jobs is not None:
            jobs = QPushButton("كل المهام…")
            jobs.clicked.connect(on_show_jobs)
            head.addWidget(jobs)
        root.addLayout(head)

        self.empty = QLabel(
            "لا إحصاءات بعد. اختر فيديو أو ملفًا صوتيًا أو سجّل الشاشة، ثم "
            "«ابدأ المعالجة» — وستظهر هنا مستنداتك وساعات الفيديو وجودة المخرجات.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(
            f"background: {theme.SURFACE}; border: 1px dashed {theme.BORDER};"
            f" border-radius: 12px; padding: 18px; color: {theme.MUTED};")
        root.addWidget(self.empty)

        self.content = QWidget()
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)
        root.addWidget(self.content)

        # الصف الأول: الأرقام الرئيسية
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.tile_documents = StatTile("مستندات مكتملة")
        self.tile_hours = StatTile("ساعات فيديو معالجة")
        self.tile_quality = StatTile("متوسط جودة المستندات")
        self.tile_success = StatTile("نسبة النجاح")
        self.tile_speed = StatTile("سرعة المعالجة")
        self.tile_month = StatTile("هذا الشهر")
        for i, tile in enumerate((self.tile_documents, self.tile_hours,
                                  self.tile_quality, self.tile_success,
                                  self.tile_speed, self.tile_month)):
            tiles.addWidget(tile, i // 3, i % 3)
        content.addLayout(tiles)

        # الصف الثاني: مخطّطان
        charts = QHBoxLayout()
        charts.setSpacing(10)
        activity = _Card("النشاط — مستندات مكتملة في آخر 6 أشهر")
        self.activity_chart = BarChart("مستند")
        activity.body.addWidget(self.activity_chart)
        quality = _Card("جودة آخر المستندات (من 100)")
        self.quality_chart = BarChart("من 100", max_value=100.0)
        quality.body.addWidget(self.quality_chart)
        charts.addWidget(activity, 1)
        charts.addWidget(quality, 1)
        content.addLayout(charts)

        # الصف الثالث: ما أنتجه + آخر المهام
        lower = QHBoxLayout()
        lower.setSpacing(10)
        produced = _Card("ما أنتجه معين لك")
        self.produced = QLabel("")
        self.produced.setWordWrap(True)
        self.produced.setTextFormat(Qt.TextFormat.RichText)
        produced.body.addWidget(self.produced)
        produced.body.addStretch(1)
        recent = _Card("آخر المهام")
        self.recent = QLabel("")
        self.recent.setWordWrap(True)
        self.recent.setTextFormat(Qt.TextFormat.RichText)
        recent.body.addWidget(self.recent)
        recent.body.addStretch(1)
        lower.addWidget(produced, 1)
        lower.addWidget(recent, 1)
        content.addLayout(lower)

        self.show_stats(UsageStats())

    # -- الجمع ------------------------------------------------------
    def refresh(self) -> None:
        if self._thread is not None:
            self._pending = True           # يُعاد الجمع فور انتهاء الجاري
            return
        self.subtitle.setText("جارٍ التحديث…")
        bridge, collect = self._bridge, self._collect

        def work() -> None:
            try:
                result = collect()
            except Exception:                         # noqa: BLE001
                result = UsageStats()
            try:
                bridge.done.emit(result)
            except RuntimeError:
                pass                                  # أُغلقت النافذة

        self._thread = threading.Thread(target=work, daemon=True,
                                        name="usage-dashboard")
        self._thread.start()

    def _on_collected(self, result: UsageStats) -> None:
        self._thread = None
        self.show_stats(result)
        if self._pending:
            self._pending = False
            self.refresh()

    def wait(self, msecs: int = 5000) -> None:
        """للاختبارات والإغلاق: ينتظر انتهاء كل جمعٍ جارٍ أو منتظر."""
        from PyQt6.QtCore import QCoreApplication
        deadline = time.monotonic() + msecs / 1000.0
        while self._thread is not None and time.monotonic() < deadline:
            self._thread.join(0.05)
            QCoreApplication.processEvents()

    # -- العرض ------------------------------------------------------
    def show_stats(self, stats: UsageStats) -> None:
        self.stats = stats
        self.empty.setVisible(stats.is_empty)
        self.content.setVisible(not stats.is_empty)
        since = _day_label(stats.first_at or "")
        self.subtitle.setText(f"منذ {since} · على هذا الجهاز" if since else "على هذا الجهاز")

        self.tile_documents.set(
            _num(stats.completed),
            f"من {_num(stats.jobs_total)} مهمّة" if stats.jobs_total else "")
        self.tile_hours.set(
            _num(stats.video_hours, 1),
            f"{_num(stats.video_seconds / 60)} دقيقة" if stats.video_seconds else "")
        if stats.average_quality is None:
            self.tile_quality.set("—", "تظهر بعد أول مستند مكتمل")
        else:
            self.tile_quality.set(f"{stats.average_quality:.0f}", "من 100 — قابلية القراءة")
        rate = stats.success_rate
        if rate is None:
            self.tile_success.set("—")
        else:
            parts = []
            if stats.failed:
                parts.append(f"{stats.failed} فشلت")
            if stats.cancelled:
                parts.append(f"{stats.cancelled} أُلغيت")
            if stats.unfinished:
                parts.append(f"{stats.unfinished} لم تكتمل")
            self.tile_success.set(f"{rate:.0f}٪", " · ".join(parts))
        speed = stats.speed_ratio
        self.tile_speed.set(
            "—" if speed is None else f"{speed:.1f}×",
            "" if speed is None else "دقائق فيديو لكل دقيقة معالجة")
        self.tile_month.set(
            _num(stats.this_month_jobs),
            f"مستند · {_num(stats.this_month_minutes)} دقيقة فيديو"
            if stats.this_month_jobs else "لا مستندات هذا الشهر بعد")

        self.activity_chart.set_items(
            [(_month_label(b.month), float(b.jobs)) for b in stats.months])
        self.quality_chart.set_items(
            [(str(i + 1), float(v)) for i, v in enumerate(stats.quality_trend)])

        self.produced.setText(self._produced_html(stats))
        self.recent.setText(self._recent_html(stats))

    @staticmethod
    def _produced_html(stats: UsageStats) -> str:
        rows = [
            ("أقسام مستندات", stats.sections),
            ("أشكال ولقطات شاشة", stats.figures),
            ("أسئلة مراجعة", stats.questions),
            ("مصطلحات مسرد", stats.glossary),
        ]
        lines = [f"<b>{_num(v)}</b> {label}" for label, v in rows if v]
        if stats.recordings:
            lines.append(f"<b>{_num(stats.recordings)}</b> تسجيل شاشة "
                         f"({_num(stats.recording_seconds / 60)} دقيقة)")
        formats = [f"{FORMAT_LABELS.get(k, k)} ({n})"
                   for k, n in stats.formats.most_common()]
        if formats:
            lines.append("<span style='color:%s'>الصيغ: </span>%s"
                         % (theme.MUTED, "، ".join(formats)))
        features = [f"{FEATURE_LABELS.get(k, k)} ({n})"
                    for k, n in stats.features.most_common()]
        if features:
            lines.append("<span style='color:%s'>الميزات: </span>%s"
                         % (theme.MUTED, "، ".join(features)))
        return "<br>".join(lines) or "—"

    @staticmethod
    def _recent_html(stats: UsageStats) -> str:
        import html
        lines = []
        for fact in stats.recent:
            label, color = STATUS_TEXT.get(fact.status, ("… لم تكتمل", theme.WARNING))
            quality = ("" if fact.quality_score is None
                       else f" · جودة {fact.quality_score:.0f}")
            minutes = (f" · {_num(fact.duration_seconds / 60)} د"
                       if fact.duration_seconds else "")
            lines.append(
                f"<div style='margin-bottom:6px'><b>{html.escape(fact.name or '—')}</b><br>"
                f"<span style='color:{color}'>{label}</span>"
                f"<span style='color:{theme.MUTED}'> · {_day_label(fact.at)}"
                f"{minutes}{quality}</span></div>")
        return "".join(lines) or "—"
