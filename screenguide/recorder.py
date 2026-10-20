"""خدمة «سجّلني»: الأحداث الخام من الشاشة ← خطوات مرقّمة بصورها.

منقولةٌ من WebDoc (``application/recording/screen.py``) بالقواعد نفسها:

* **الصورة تُلتقط لحظة نزول الزر.** خارج المتصفح لا يمكن حبس النقرة ثم
  إعادتها — ابتلاع نقرة في برنامج غيرك لإعادتها لاحقًا هو كيف يُعطِب المسجِّلُ
  البرنامجَ الذي يراقبه. والبرنامج يتصرّف حين يرتفع الزر، فالشاشة في تلك
  الصورة ما زالت ما سيراه القارئ.
* **نقرتان قريبتان زمنًا ومكانًا نقرةٌ مزدوجة**، وضغطٌ وإفلاتٌ متباعدان سحب،
  والنقر في حقلٍ ثم الكتابة فيه خطوةٌ واحدة («أدخل البيانات في حقل الاسم»).
* **النافذة تقوم مقام الصفحة**: الانتقال إلى نافذة أخرى (حوار يُفتح، برنامج
  يتقدّم) هو نتيجة الخطوة السابقة.
* **ما يُكتب لا يُسجَّل**، وحقل كلمة المرور يُطلى في الصورة قبل حفظها.

لا شيء هنا يعرف ويندوز؛ المصدر (``windows_source``) يعرفه.
"""
from __future__ import annotations

import io
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from screenguide.model import (
    CHECK,
    CLICK,
    SUBMIT,
    TYPE,
    UNCHECK,
    UNNAMED_ROLES,
    Box,
    RecordedStep,
    Recording,
    ScreenEvent,
    ScreenEventSource,
    ScreenWindow,
)

logger = logging.getLogger(__name__)

#: نقرتان بهذا القرب زمنًا ومكانًا نقرةٌ مزدوجة. افتراض ويندوز نفسه؛
#: المصدر يمرّر إعداد الشخص حين يعرفه.
DOUBLE_CLICK_MS = 500
DOUBLE_CLICK_PX = 6.0
#: ضغطٌ وإفلاتٌ أبعد من هذا سحبٌ لا نقرة.
DRAG_PX = 24.0
#: العلامة المرسومة مكان المؤشر حين لا يكون تحته شيءٌ مسمّى.
SPOT = 36.0
#: «عنصر» يغطّي أكثر من هذا من الصورة هو المستند أو الجدول، لا ما ضُغط.
MAX_TARGET_SHARE = 0.25
#: سقف الخطوات في رحلة واحدة.
MAX_STEPS = 200


class RecordingCancelled(Exception):
    """أُلغي التسجيل من خارج الشريط (إغلاق البرنامج مثلًا)."""


@dataclass
class Progress:
    steps: int
    label: str
    action: str


StepHook = Callable[[Progress], None]


@dataclass
class _Pending:
    """خطوةٌ لا تُعرف نتيجتها حتى تُلتقط التالية."""

    step: RecordedStep
    window: ScreenWindow
    at_ms: int
    button: str
    screen_point: Optional[Tuple[float, float]]


class ScreenGuideRecorder:
    def __init__(self, *, max_steps: int = MAX_STEPS,
                 double_click_ms: int = DOUBLE_CLICK_MS) -> None:
        self._max_steps = max_steps
        self._double_ms = double_click_ms

    def record(
        self,
        source: ScreenEventSource,
        *,
        title: str,
        folder: Path,
        language: str = "ar",
        on_step: Optional[StepHook] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Recording:
        """يسجّل حتى «إنهاء وحفظ»، ويحفظ الرحلة وصورها في ``folder``."""
        ar = language.startswith("ar")
        recording = Recording(
            title=title.strip() or ("إجراء مسجَّل" if ar else "Recorded procedure"),
            language="ar" if ar else "en",
        )
        shots = folder / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        pending: List[_Pending] = []
        final: Optional[ScreenEvent] = None
        started = time.monotonic()
        counter = 0

        while True:
            if should_stop is not None and should_stop():
                raise RecordingCancelled
            event = source.next_event(timeout=0.5)
            if event is None:
                continue
            kind = event.kind
            if kind in ("finish", "closed"):
                final = event if event.frame is not None else None
                break
            if kind == "undo":
                if recording.steps:
                    recording.steps.pop()
                    gone = pending.pop()
                    if gone.step.shot:
                        (folder / gone.step.shot).unlink(missing_ok=True)
                source.show_steps(len(recording.steps))
                _notify(on_step, recording, "undo")
                continue
            if kind == "up":
                self._released(event, pending)
                continue
            if kind == "click" and self._second_press(event, pending):
                _notify(on_step, recording, "click", recording.steps[-1])
                continue
            if kind == "type" and self._typing_into_clicked_field(event, pending):
                _notify(on_step, recording, "type", recording.steps[-1])
                continue
            if kind not in ("click", "type", "enter", "shortcut"):
                continue
            if len(recording.steps) >= self._max_steps:
                continue
            try:
                counter += 1
                item = self._step(event, recording, pending[-1] if pending else None,
                                  folder=folder, shot_name=f"shots/{counter:04d}.png",
                                  language=recording.language,
                                  at_seconds=time.monotonic() - started)
            except Exception:
                # خطوةٌ واحدة تعذّر حفظها لا تُضيع الرحلة كلّها.
                logger.exception("screen guide: a step could not be captured")
                continue
            recording.steps.append(item.step)
            pending.append(item)
            source.show_steps(len(recording.steps))
            _notify(on_step, recording, kind, item.step)

        recording.renumber()
        recording.duration_seconds = max(1.0, time.monotonic() - started)
        self._finish(recording, pending, final, folder=folder)
        recording.save(folder)
        return recording

    # ------------------------------------------------------------ دمج الإيماءات
    def _second_press(self, event: ScreenEvent, pending: List[_Pending]) -> bool:
        """الضغطة الثانية من نقرٍ مزدوج: تغيّر الخطوة التي تنتمي إليها."""
        if not pending or event.button != "left":
            return False
        last = pending[-1]
        if last.button != "left" or last.step.gesture is not None:
            return False
        if last.step.action not in (CLICK, CHECK, UNCHECK):
            return False
        if not (0 <= event.at_ms - last.at_ms <= self._double_ms):
            return False
        if _distance(event.screen_point, last.screen_point) > DOUBLE_CLICK_PX:
            return False
        last.step.gesture = "double"
        last.step.action = CLICK
        return True

    @staticmethod
    def _released(event: ScreenEvent, pending: List[_Pending]) -> None:
        """إفلاتٌ بعيد عن ضغطه: الخطوة سحب."""
        if not pending:
            return
        last = pending[-1]
        if last.button != event.button or last.step.action != CLICK:
            return
        if last.step.gesture is None and _distance(
                event.screen_point, last.screen_point) > DRAG_PX:
            last.step.gesture = "drag"

    @staticmethod
    def _typing_into_clicked_field(event: ScreenEvent, pending: List[_Pending]) -> bool:
        """نقرٌ في حقل ثم كتابة: خطوةٌ واحدة لا اثنتان.

        صورة النقرة تبقى — تُظهر الحقل فارغًا ورقمه عليه، وهو ما يراه القارئ
        قبل أن يكتب.
        """
        if not pending:
            return False
        step = pending[-1].step
        if step.action != CLICK or step.gesture is not None:
            return False
        if step.role not in ("textbox", "searchbox", "combobox", "cell"):
            return False
        named = event.target.name.strip()
        if named and named != step.label:
            return False
        step.action = TYPE
        step.secret = step.secret or event.target.secret
        return True

    # ------------------------------------------------------------------ الخطوات
    def _step(self, event: ScreenEvent, recording: Recording, previous: Optional[_Pending],
              *, folder: Path, shot_name: str, language: str,
              at_seconds: float) -> _Pending:
        window = event.window
        window_label = window.label or ("الشاشة" if language == "ar" else "Screen")
        size = _size(event.frame)
        shot = _png(event.frame, mask=_mask_boxes(event))
        if shot:
            (folder / shot_name).write_bytes(shot)

        action, role, label = _describe(event, window_label)
        box = _target_box(event, size)
        screen = screen_id(window, window_label)
        if previous is not None:
            _settle(previous, screen, window, window_label)

        step = RecordedStep(
            index=len(recording.steps) + 1,
            action=action,
            label=label,
            role=role,
            secret=event.target.secret,
            shot=shot_name if shot else None,
            box=box,
            screen_before=screen,
            screen_after=screen,
            gesture="right" if event.kind == "click" and event.button == "right" else None,
            app=window.app or None,
            window=window_label,
            at_seconds=at_seconds,
        )
        return _Pending(step=step, window=window, at_ms=event.at_ms,
                        button=event.button, screen_point=event.screen_point)

    def _finish(self, recording: Recording, pending: List[_Pending],
                final: Optional[ScreenEvent], *, folder: Path) -> None:
        """أين انتهت الرحلة: الشاشة أمام الشخص لحظة «إنهاء»."""
        if not pending or final is None:
            return
        language = recording.language
        label = final.window.label or ("الشاشة" if language == "ar" else "Screen")
        try:
            shot = _png(final.frame)
            if shot:
                (folder / "shots" / "final.png").write_bytes(shot)
                recording.final_shot = "shots/final.png"
            recording.final_window = label
            _settle(pending[-1], screen_id(final.window, label), final.window, label)
        except Exception:
            logger.debug("screen guide: final capture skipped", exc_info=True)


# -------------------------------------------------------------------- مساعدات
_ACTIONS = {"click": CLICK, "type": TYPE, "enter": SUBMIT, "shortcut": SUBMIT}


def _describe(event: ScreenEvent, window_label: str) -> Tuple[str, str, str]:
    """فعل الخطوة، ودور العنصر، والكلمات التي تسمّيه."""
    target = event.target
    name = " ".join(target.name.split())[:200]
    role = target.role or "pane"
    if event.kind == "shortcut":
        return SUBMIT, "shortcut", event.keys[:60] or "?"
    action = _ACTIONS.get(event.kind, CLICK)
    if event.window.protected or not name or role in UNNAMED_ROLES:
        # لا شيء مقروء تحت المؤشر — برنامجٌ بصلاحيات مدير، أو لا يصف نفسه.
        # تُسمّى النافذة، ورقم الخطوة على الصورة يُظهر الموضع.
        return action, "pane", window_label
    if (event.kind == "click" and event.button == "left"
            and role in ("checkbox", "switch") and target.checked is not None):
        action = UNCHECK if target.checked else CHECK
    return action, role, name


def _target_box(event: ScreenEvent, size: Optional[Tuple[int, int]]) -> Optional[Box]:
    box = event.target.box if event.target.name.strip() else None
    if box and box[2] > 0 and box[3] > 0 and size:
        share = (box[2] * box[3]) / max(1.0, float(size[0] * size[1]))
        if share <= MAX_TARGET_SHARE:
            return _clip(box, size)
    if event.point is not None:
        x, y = event.point
        return _clip((x - SPOT / 2, y - SPOT / 2, SPOT, SPOT), size)
    return _clip(box, size) if box else None


def _clip(box: Box, size: Optional[Tuple[int, int]]) -> Box:
    if not size:
        return box
    x, y, w, h = box
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(float(size[0]), x + w), min(float(size[1]), y + h)
    if right <= left or bottom <= top:
        return box
    return (left, top, right - left, bottom - top)


def _mask_boxes(event: ScreenEvent) -> List[Box]:
    """ما يُطلى قبل حفظ الصورة: حقل كلمة المرور."""
    if event.target.secret and event.target.box:
        return [event.target.box]
    return []


def _slug(text: str) -> str:
    slug = re.sub(r"[\s/\\?#%]+", "-", text.strip().casefold()).strip("-")
    return slug[:80] or "window"


def screen_id(window: ScreenWindow, label: str) -> str:
    """هويّة النافذة: ``<برنامج>/<نافذة>``."""
    process = _slug(window.process or window.app or "program")
    return f"{process}/{_slug(label)}"


def _settle(previous: _Pending, after: str, window: ScreenWindow, label: str) -> None:
    """سجّل أين قادت خطوةٌ، حين تُعرف الشاشة التالية."""
    step = previous.step
    step.screen_after = after
    if after == step.screen_before:
        return
    step.arrived_at = label[:120] or None
    step.arrived_kind = "dialog" if window.dialog else "window"


def _distance(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> float:
    if a is None or b is None:
        return math.inf
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _size(frame: Any) -> Optional[Tuple[int, int]]:
    if frame is None:
        return None
    size = getattr(frame, "size", None)
    if isinstance(size, tuple) and len(size) == 2:
        return int(size[0]), int(size[1])
    if isinstance(frame, (bytes, bytearray)):
        try:
            from PIL import Image

            with Image.open(io.BytesIO(frame)) as image:
                return int(image.size[0]), int(image.size[1])
        except Exception:
            return None
    return None


def _png(frame: Any, *, mask: Optional[List[Box]] = None) -> Optional[bytes]:
    if frame is None:
        return None
    if isinstance(frame, (bytes, bytearray)) and not mask:
        return bytes(frame)
    from PIL import Image, ImageDraw

    image = (Image.open(io.BytesIO(bytes(frame))).convert("RGB")
             if isinstance(frame, (bytes, bytearray)) else frame.convert("RGB"))
    if mask:
        image = image.copy()
        drawing = ImageDraw.Draw(image)
        for x, y, w, h in mask:
            drawing.rectangle([x, y, x + w, y + h], fill=(32, 32, 32))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False, compress_level=6)
    return out.getvalue()


def _notify(hook: Optional[StepHook], recording: Recording, action: str,
            step: Optional[RecordedStep] = None) -> None:
    if hook is None:
        return
    try:
        hook(Progress(steps=len(recording.steps),
                      label=step.label if step else "", action=action))
    except Exception:
        logger.debug("screen guide: progress hook failed", exc_info=True)
