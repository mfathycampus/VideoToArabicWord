"""جملة كل خطوة — محدَّدة تمامًا بما رُصد، بلا نموذج لغوي.

منقولة من WebDoc (``application/recording/document.py``). كل كلمة بين «»
هي اسمٌ ظهر فعلًا على الشاشة: لا يُخترع زرٌّ ولا نافذة.

البرنامج يُذكر حين تدخله الرحلة لا في كل سطر: «في Excel» على كل خطوة ضجيج.
"""
from __future__ import annotations

from typing import List, Optional

from screenguide.model import (
    CHECK,
    SUBMIT,
    TYPE,
    UNCHECK,
    UNNAMED_ROLES,
    RecordedStep,
    Recording,
)


def q(text: str, language: str) -> str:
    return f"«{text}»" if language == "ar" else f"“{text}”"


def instruction(step: RecordedStep, language: str, *, app: Optional[str] = None) -> str:
    """جملة خطوةٍ واحدة. ``app`` يُمرَّر حين تغيّر البرنامج منذ الخطوة السابقة."""
    ar = language == "ar"
    label = q(step.label, language)
    unnamed = step.role in UNNAMED_ROLES or step.role is None
    where = ""
    if app and app != step.label:
        where = f" في {q(app, language)}" if ar else f" in {q(app, language)}"
    kind = step.action

    if step.role == "shortcut":
        return (f"اضغط مفاتيح الاختصار {label}{where}." if ar
                else f"Press {label}{where}.")
    if kind == TYPE:
        if unnamed:
            return (f"أدخل البيانات المطلوبة في {label}." if ar
                    else f"Type the required entry in {label}.")
        if step.secret:
            return (f"أدخل كلمة المرور في حقل {label}{where}." if ar
                    else f"Enter the password in {label}{where}.")
        return (f"أدخل البيانات في حقل {label}{where}." if ar
                else f"Enter a value in {label}{where}.")
    if kind == SUBMIT:
        if unnamed:
            return f"اضغط مفتاح Enter في {label}." if ar else f"Press Enter in {label}."
        return (f"اضغط مفتاح Enter في حقل {label}{where}." if ar
                else f"Press Enter in {label}{where}.")
    if kind == CHECK and not unnamed:
        return f"حدد الخيار {label}{where}." if ar else f"Check {label}{where}."
    if kind == UNCHECK and not unnamed:
        return (f"ألغِ تحديد الخيار {label}{where}." if ar
                else f"Uncheck {label}{where}.")

    if unnamed:
        # لا شيء مسمّى تحت المؤشر: تُسمّى النافذة، والرقم على الصورة يُظهر الموضع.
        spot_ar = f"الموضع المحدد في الصورة داخل {label}"
        spot_en = f"the marked spot in {label}"
        if step.gesture == "double":
            return f"انقر نقرًا مزدوجًا على {spot_ar}." if ar else f"Double-click {spot_en}."
        if step.gesture == "right":
            return (f"انقر بزر الفأرة الأيمن على {spot_ar}." if ar
                    else f"Right-click {spot_en}.")
        if step.gesture == "drag":
            return f"اسحب من {spot_ar}." if ar else f"Drag from {spot_en}."
        return f"اضغط على {spot_ar}." if ar else f"Click {spot_en}."

    if step.gesture == "double":
        return (f"انقر نقرًا مزدوجًا على {label}{where}." if ar
                else f"Double-click {label}{where}.")
    if step.gesture == "right":
        return (f"انقر بزر الفأرة الأيمن على {label}{where}." if ar
                else f"Right-click {label}{where}.")
    if step.gesture == "drag":
        return (f"اسحب {label}{where} إلى موضعه الجديد." if ar
                else f"Drag {label}{where} to its new place.")
    if step.role == "tab":
        return f"افتح تبويب {label}{where}." if ar else f"Open the {label} tab{where}."
    if step.role in ("menuitem", "menu"):
        return (f"اختر {label} من القائمة{where}." if ar
                else f"Choose {label} from the menu{where}.")
    return f"اضغط على {label}{where}." if ar else f"Click {label}{where}."


def outcome(step: RecordedStep, language: str) -> Optional[str]:
    """ما يراه القارئ بعد الخطوة، مما رُصد."""
    if not (step.moved and step.arrived_at):
        return None
    ar = language == "ar"
    where = q(step.arrived_at, language)
    if step.arrived_kind == "dialog":
        return f"تظهر نافذة {where}." if ar else f"The {where} dialog opens."
    return (f"تنتقل إلى نافذة {where}." if ar
            else f"The {where} window comes to the front.")


def instructions(recording: Recording) -> List[str]:
    """جمل الرحلة كلّها بالترتيب — البرنامج مذكورٌ عند دخوله فقط."""
    out: List[str] = []
    previous_app: Optional[str] = None
    for step in recording.steps:
        app = step.app if step.app and step.app != previous_app else None
        previous_app = step.app or previous_app
        out.append(instruction(step, recording.language, app=app))
    return out


def start_line(recording: Recording) -> Optional[str]:
    if not recording.steps:
        return None
    first = recording.steps[0].window
    if not first:
        return None
    ar = recording.language == "ar"
    return (f"ابدأ من نافذة {q(first, recording.language)}." if ar
            else f"Start in the {q(first, recording.language)} window.")


def result_lines(recording: Recording) -> List[str]:
    """النتيجة: الشاشة التي انتهت إليها الرحلة."""
    if not recording.steps:
        return []
    last = recording.steps[-1]
    if last.moved and last.arrived_at:
        ar = recording.language == "ar"
        return [f"تصل إلى {q(last.arrived_at, recording.language)}." if ar
                else f"You arrive at {q(last.arrived_at, recording.language)}."]
    return []


def description(recording: Recording, count: Optional[int] = None) -> str:
    n = len(recording.steps) if count is None else count
    if recording.language == "ar":
        return (f"سُجِّل هذا الإجراء من تنفيذ فعلي على الجهاز ويتكوّن من {steps_ar(n)}. "
                "الرقم الظاهر على كل صورة يطابق رقم الخطوة.")
    return (f"Recorded from a real run on the computer: {n} steps. "
            "The number on each picture is the number of its step.")


def steps_ar(n: int) -> str:
    if n == 1:
        return "خطوة واحدة"
    if n == 2:
        return "خطوتين"
    return f"{n} خطوات" if 3 <= n <= 10 else f"{n} خطوة"
