"""«سجّلني»: ما فعله الشخص على شاشته، خطوةً مرقّمة بعد خطوة.

منقولٌ من خاصية *Follow me — My whole screen* في WebDoc، بالمدخلات والمخرجات
نفسها: كل نقرة في أي برنامج تصير خطوة مرقّمة لها صورتها، والزرّ يُسمّى باسمه
في البرنامج نفسه («حفظ باسم»، «اسم الملف»).

هنا العقود وحدها — لا شيء يعرف ويندوز:

* ``ScreenTarget`` / ``ScreenWindow`` / ``ScreenEvent``: ما رآه المصدر لحظة الحدث.
* ``ScreenEventSource``: الخطّ الفاصل بين شاشة الشخص وخدمة التسجيل. مصدر
  ويندوز الحقيقي في ``screenguide/windows_source.py``، ومصدرٌ مزيَّف في
  الاختبارات.
* ``RecordedStep`` / ``Recording``: الرحلة المحفوظة (``recording.json``)، ومنها
  يُكتب الدليل في أي وقت دون إعادة التسجيل.

ما لا يُسجَّل أبدًا: ما يُكتب. ضغطة المفتاح تقول إن حقلًا مُلئ أو أي اختصار
استُعمل — لا الحروف.
"""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

Box = Tuple[float, float, float, float]

#: أنواع الخطوة — بالأسماء نفسها في WebDoc (``ActionType``).
CLICK = "click"
TYPE = "type"
SUBMIT = "submit"
CHECK = "check"
UNCHECK = "uncheck"
ACTIONS = (CLICK, TYPE, SUBMIT, CHECK, UNCHECK)

#: أدوارٌ لا تسمّي شيئًا: الخطوة تسمّي النافذة، ورقمها على الصورة يدلّ على الموضع.
UNNAMED_ROLES = frozenset({"pane", "window", "client"})

RECORDING_SCHEMA = "1.0"


# --------------------------------------------------------------------- المصدر
@dataclass(frozen=True)
class ScreenTarget:
    """ما قاله البرنامج عمّا تحت المؤشر (أو عمّا معه لوحة المفاتيح)."""

    name: str = ""
    #: مفردات الأدوار: button, link, menuitem, tab, textbox, ...
    role: Optional[str] = None
    #: ‏x, y, width, height بإحداثيات صورة الخطوة.
    box: Optional[Box] = None
    #: حالة مربّع الاختيار **قبل** الضغط (None: ليس مربّعًا أو غير معروف).
    checked: Optional[bool] = None
    #: حقل كلمة مرور: الخطوة تقول ذلك، ومستطيله يُطلى في الصورة.
    secret: bool = False
    editable: bool = False


@dataclass(frozen=True)
class ScreenWindow:
    """النافذة التي وقعت فيها الخطوة."""

    #: البرنامج باسمه الذي يسمّي به نفسه («Microsoft Excel»).
    app: str = ""
    title: str = ""
    #: ملف البرنامج بلا امتداد وبحروف صغيرة («excel»).
    process: str = ""
    #: حوار: نافذة تتبع أخرى وتطلب شيئًا.
    dialog: bool = False
    #: برنامج يعمل بصلاحيات مدير: لا يُقرأ ما تحت المؤشر فيه، فتُسمّى النافذة.
    protected: bool = False

    @property
    def label(self) -> str:
        """ما يسمّي به القارئ هذه النافذة.

        الحوار بعنوانه («حفظ باسم»): ثابتٌ وهو ما يراه القارئ. والنافذة
        الرئيسية ببرنامجها: عنوانها اسم مستند اليوم («ميزانية.xlsx - Excel»)
        أو بريد شخص — ليس جزءًا من الإجراء.
        """
        if self.dialog and self.title.strip():
            return self.title.strip()[:120]
        return (self.app or self.title or self.process or "").strip()[:120]


@dataclass
class ScreenEvent:
    """شيءٌ واحد فعله الشخص كما رآه المصدر.

    ``kind`` واحد من: click, up, type, enter, shortcut, undo, finish, closed.
    """

    kind: str
    #: ساعة النظام بالمللي ثانية لحظة الحدث (لتوقيت النقر المزدوج).
    at_ms: int = 0
    button: str = "left"
    #: الموضع بإحداثيات صورة الخطوة.
    point: Optional[Tuple[float, float]] = None
    #: الموضع على سطح المكتب كلّه — لمقارنة الضغط بالإفلات.
    screen_point: Optional[Tuple[float, float]] = None
    #: الاختصار كما يكتبه القارئ: «Ctrl+S».
    keys: str = ""
    #: الصورة لحظة الحدث: بايتات PNG أو كائن صورة له ``save(fp, format=...)``.
    frame: Any = None
    target: ScreenTarget = field(default_factory=ScreenTarget)
    window: ScreenWindow = field(default_factory=ScreenWindow)


class ScreenEventSource(ABC):
    """الخطّ بين شاشة الشخص كاملةً وخدمة التسجيل. متزامن: يُقرأ من خيط عامل."""

    @abstractmethod
    def next_event(self, timeout: Optional[float] = None) -> Optional[ScreenEvent]:
        """الحدث التالي، أو None عند انتهاء المهلة."""

    @abstractmethod
    def show_steps(self, steps: int) -> None:
        """أخبر الشخص بعدد الخطوات المسجّلة (الشريط العائم)."""

    @abstractmethod
    def request_finish(self) -> None:
        """إنهاء من خارج الشريط (زرّ البرنامج نفسه)."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    def double_click_ms(self) -> int:
        return 500


# ------------------------------------------------------------------- الرحلة
@dataclass
class RecordedStep:
    #: يبدأ من 1، كما يراه القارئ في النص وعلى الصورة.
    index: int
    action: str
    #: الزر أو الحقل باسمه («حفظ باسم»، «اسم الملف»).
    label: str
    role: Optional[str] = None
    #: حقل كلمة مرور مُلئ.
    secret: bool = False
    #: صورة الخطوة (لحظة قبلها) بالنسبة لمجلد الرحلة: ``shots/003.png``.
    shot: Optional[str] = None
    #: موضع العنصر على تلك الصورة.
    box: Optional[Box] = None
    #: هويّة النافذة: ``<برنامج>/<نافذة>`` — بها يُعرف أن الخطوة نقلت إلى غيرها.
    screen_before: str = ""
    screen_after: str = ""
    #: اسم النافذة التي أدّت إليها الخطوة حين انتقلت إلى جديدة.
    arrived_at: Optional[str] = None
    #: ‏"dialog" أو "window".
    arrived_kind: Optional[str] = None
    #: ‏"double" أو "right" أو "drag" حين لم تكن نقرةً عادية.
    gesture: Optional[str] = None
    #: البرنامج الذي وقعت فيه الخطوة.
    app: Optional[str] = None
    #: اسم النافذة التي بدأت فيها الخطوة (للسطر «ابدأ من نافذة…»).
    window: str = ""
    #: الثواني منذ بدء التسجيل.
    at_seconds: float = 0.0

    @property
    def moved(self) -> bool:
        return bool(self.screen_after) and self.screen_after != self.screen_before

    def to_dict(self) -> dict:
        return {
            "index": self.index, "action": self.action, "label": self.label,
            "role": self.role, "secret": self.secret, "shot": self.shot,
            "box": list(self.box) if self.box else None,
            "screen_before": self.screen_before, "screen_after": self.screen_after,
            "arrived_at": self.arrived_at, "arrived_kind": self.arrived_kind,
            "gesture": self.gesture, "app": self.app, "window": self.window,
            "at_seconds": round(float(self.at_seconds), 3),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "RecordedStep":
        action = str(raw.get("action") or CLICK)
        return cls(
            index=int(raw["index"]),
            action=action if action in ACTIONS else CLICK,
            label=str(raw.get("label") or ""),
            role=raw.get("role"),
            secret=bool(raw.get("secret")),
            shot=raw.get("shot"),
            box=tuple(float(v) for v in raw["box"]) if raw.get("box") else None,
            screen_before=str(raw.get("screen_before") or ""),
            screen_after=str(raw.get("screen_after") or ""),
            arrived_at=raw.get("arrived_at"),
            arrived_kind=raw.get("arrived_kind"),
            gesture=raw.get("gesture"),
            app=raw.get("app"),
            window=str(raw.get("window") or ""),
            at_seconds=float(raw.get("at_seconds") or 0.0),
        )


@dataclass
class Recording:
    #: ما تحقّقه الرحلة بكلمات الشخص: «إضافة مستخدم جديد».
    title: str
    language: str = "ar"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    steps: List[RecordedStep] = field(default_factory=list)
    #: صورة الشاشة عند «إنهاء»: النتيجة التي يتوقّعها القارئ.
    final_shot: Optional[str] = None
    final_window: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_seconds: float = 0.0

    def renumber(self) -> None:
        for index, step in enumerate(self.steps, start=1):
            step.index = index

    def to_dict(self) -> dict:
        return {
            "schema_version": RECORDING_SCHEMA,
            "id": self.id, "title": self.title, "language": self.language,
            "created_at": self.created_at,
            "duration_seconds": round(float(self.duration_seconds), 3),
            "final_shot": self.final_shot, "final_window": self.final_window,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Recording":
        return cls(
            title=str(raw.get("title") or ""),
            language=str(raw.get("language") or "ar"),
            id=str(raw.get("id") or uuid.uuid4().hex),
            steps=[RecordedStep.from_dict(s) for s in raw.get("steps") or []],
            final_shot=raw.get("final_shot"),
            final_window=str(raw.get("final_window") or ""),
            created_at=str(raw.get("created_at") or ""),
            duration_seconds=float(raw.get("duration_seconds") or 0.0),
        )

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "recording.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, folder: Path) -> "Recording":
        raw = json.loads((folder / "recording.json").read_text(encoding="utf-8"))
        return cls.from_dict(raw)
