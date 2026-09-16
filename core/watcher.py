"""مجلد وارد يُعالَج تلقائيًّا — الحلّ الوحيد المعقول لمشكلة الزمن.

**الرقم الذي يجعل هذا ضروريًّا:** ساعة فيديو تحتاج ساعة وخمسًا وأربعين
دقيقة على معالج، وثلاث عشرة دقيقة على كرت رسوميات. فنشرُ البرنامج على
جهاز كل معلّم يعني أن كلًّا منهم ينتظر ساعتين وحاسوبه محجوز — وهو ما
يجعل الأداة تُجرَّب مرّة ثم تُترك، مهما كانت جودة مخرجها.

**والترتيب الصحيح مقلوب:** جهازٌ واحد بكرت رسوميات يعمل ليلًا. يُسقط
المعلّم تسجيله في مجلد مشترك وينصرف، ويجد مخرجاته في الصباح. وتكلفة
الجهاز الواحد أقلّ من تعطيل عشرين حاسوبًا ساعتين.

**واستقرار الملفّ هو العطل الذي يقع حتمًا** ولا يظهر في أي اختبار على
جهاز واحد. الملفّ الذي يُنسخ عبر الشبكة يظهر في المجلد **فور بدء
النسخ**، فيلتقطه المراقب وهو نصف مكتوب، فيُفرّغ نصف محاضرة أو يسقط
بملفّ تالف. ولذلك لا يُقبَل ملفّ حتى يثبت حجمه ووقت تعديله عبر فحصين
متتاليين بينهما مهلة.

**والسجلّ يمنع إعادة المعالجة:** الملفّ يُعرَّف بالاسم والحجم ووقت
التعديل. فمن نسخ الملفّ مرّتين لا يدفع ثمن ساعتين مرّتين، ومن استبدله
بنسخة محرَّرة يُعاد تفريغها لأن بصمته تغيّرت.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from config.settings import AppConfig
from core.batch import MEDIA_SUFFIXES, is_audio
from utils.cancellation import CancellationToken
from utils.logger import logger

LEDGER_FILENAME = ".vtad_processed.json"

#: فحصان متتاليان بينهما هذه المهلة يجب أن يعطيا الحجم ووقت التعديل
#: نفسيهما. ثلاث ثوانٍ تكفي لنسخٍ عبر شبكة محلّية؛ والنسخ الأبطأ يُلتقط
#: في الدورة التالية لا يُقبل ناقصًا.
STABLE_SECONDS = 3.0
#: كل كم ثانية يُفحص المجلد حين يكون فارغًا.
POLL_SECONDS = 20.0


@dataclass
class WatchState:
    """سجلّ ما عولج — يُحفظ في مجلد الوارد نفسه.

    الحفظ بجوار الملفّات مقصود: نقل المجلد أو نسخه ينقل معه سجلَّه،
    فلا يُعاد تفريغ مقرّر كامل لأن المسؤول نقل المشاركة إلى خادم آخر.
    """
    path: Path
    entries: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, inbox: Path) -> "WatchState":
        path = Path(inbox) / LEDGER_FILENAME
        state = cls(path=path)
        if path.is_file():
            try:
                state.entries = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"سجلّ الوارد تالف، يُبدأ من جديد: {exc}")
        return state

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.entries, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:
            logger.warning(f"تعذّر حفظ سجلّ الوارد: {exc}")

    def mark(self, key: str, **fields) -> None:
        self.entries[key] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
        self.save()

    def seen(self, key: str) -> bool:
        return key in self.entries


def fingerprint(path: Path) -> str:
    """هوية الملفّ: الاسم والحجم ووقت التعديل.

    لا مجموع تحقّق: قراءة ملفّ بأربعة جيجابايت لحساب SHA تكلّف دقائق
    لكل دورة فحص، وهو ثمنٌ لا يشتري شيئًا هنا — تغيّرُ المحتوى بلا
    تغيّر الحجم **ووقت التعديل** معًا حالةٌ لا تقع عمليًّا.
    """
    stat = path.stat()
    return f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}"


def is_stable(path: Path, wait: float = STABLE_SECONDS) -> bool:
    """هل انتهى نسخ الملفّ؟ فحصان بينهما مهلة."""
    try:
        first = path.stat()
        time.sleep(wait)
        second = path.stat()
    except OSError:
        return False
    return (first.st_size == second.st_size
            and int(first.st_mtime) == int(second.st_mtime)
            and second.st_size > 0)


def pending_files(inbox: Path, state: WatchState) -> List[Path]:
    """ملفّات الوسائط الجاهزة التي لم تُعالَج بعد."""
    inbox = Path(inbox)
    if not inbox.is_dir():
        return []
    ready: List[Path] = []
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        try:
            if state.seen(fingerprint(path)):
                continue
        except OSError:
            continue
        ready.append(path)
    return ready


def process_once(inbox: Path, output_dir: Path,
                 config: Optional[AppConfig] = None, *,
                 allow_model_download: bool = False,
                 cancel_token: Optional[CancellationToken] = None,
                 on_event: Optional[Callable[[str], None]] = None,
                 state: Optional[WatchState] = None) -> List[Path]:
    """دورة واحدة: يلتقط المستقرّ ويعالجه ويعيد ما عولج فعلًا."""
    from core.batch import process_folder

    config = config or AppConfig()
    state = state or WatchState.load(inbox)

    candidates = pending_files(inbox, state)
    if not candidates:
        return []

    ready: List[Path] = []
    for path in candidates:
        if is_stable(path):
            ready.append(path)
        else:
            _say(on_event, f"ما زال يُنسخ، يُؤجَّل: {path.name}")
    if not ready:
        return []

    _say(on_event, f"بدء معالجة {len(ready)} ملفًّا…")
    results = process_folder(
        ready, output_dir, config,
        allow_model_download=allow_model_download,
        cancel_token=cancel_token)

    done: List[Path] = []
    for result in results:
        try:
            key = fingerprint(result.source)
        except OSError:
            continue
        # يُسجَّل الفاشل أيضًا، ومعه سببه. بلا ذلك يُعاد تفريغ ملفّ تالف
        # كل دورة إلى الأبد — وهو بالضبط ما يحرق ليلة المعالجة كلّها.
        state.mark(key, ok=result.ok, name=result.source.name,
                   output=str(result.output or ""), error=result.error or "")
        _say(on_event,
             ("✓ " if result.ok else "✗ ") + result.source.name
             + ("" if result.ok else f" — {result.error}"))
        if result.ok:
            done.append(result.source)
    return done


def watch(inbox: Path, output_dir: Path,
          config: Optional[AppConfig] = None, *,
          allow_model_download: bool = False,
          poll_seconds: float = POLL_SECONDS,
          max_cycles: Optional[int] = None,
          cancel_token: Optional[CancellationToken] = None,
          on_event: Optional[Callable[[str], None]] = None) -> int:
    """حلقة المراقبة. ``max_cycles`` للاختبار ولتشغيلة ليلية محدودة."""
    inbox, output_dir = Path(inbox), Path(output_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = WatchState.load(inbox)

    _say(on_event, f"مراقبة {inbox} ← {output_dir}")
    processed = 0
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        if cancel_token and cancel_token.is_cancelled():
            _say(on_event, "أُوقفت المراقبة.")
            break
        try:
            processed += len(process_once(
                inbox, output_dir, config,
                allow_model_download=allow_model_download,
                cancel_token=cancel_token, on_event=on_event, state=state))
        except Exception as exc:
            # خدمةٌ تعمل ليلًا يجب ألا تموت على ملفّ واحد. تُسجَّل
            # وتُكمل، وإلا وجد المسؤول الطابور كلّه واقفًا في الصباح
            # بسبب ملفّ تالف في أوّله.
            logger.exception("عطل في دورة المراقبة")
            _say(on_event, f"عطل في الدورة، تُكمل المراقبة: {exc}")

        if max_cycles is not None and cycles >= max_cycles:
            break
        _sleep_interruptible(poll_seconds, cancel_token)
    return processed


def _sleep_interruptible(seconds: float,
                         cancel_token: Optional[CancellationToken]) -> None:
    """نومٌ يستيقظ للإلغاء. ``sleep`` واحدة طويلة تجعل الإيقاف يبدو معلّقًا."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel_token and cancel_token.is_cancelled():
            return
        time.sleep(min(0.5, deadline - time.monotonic()))


def _say(on_event: Optional[Callable[[str], None]], message: str) -> None:
    logger.info(message)
    if on_event:
        on_event(message)


def summarize(state: WatchState) -> Sequence[dict]:
    return [{"key": key, **value} for key, value in state.entries.items()]


__all__ = ["WatchState", "fingerprint", "is_stable", "pending_files",
           "process_once", "watch", "is_audio", "LEDGER_FILENAME"]
