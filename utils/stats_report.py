"""إرسال إحصاءات الاستخدام المجمّعة لمسؤول الترخيص — بإعلانٍ لا بصمت.

ما يُرسل هو ``core.stats.to_payload`` وحده: أعداد مهامّ ودقائق ومتوسّط
جودة وصيغ وميزات، مربوطةً بكود التفعيل الذي يعرفه الخادم أصلًا. لا أسماء
ملفات ولا نصوص ولا لقطات ولا مسارات.

الشروط كلّها لازمة: الإعداد ``application.share_usage_stats`` (تمنعه سياسة
الجهاز ``allow_usage_stats: false``)، ونسخةٌ بترخيص، وكودٌ مفعَّل. والإرسال
مرّةً كل ``MIN_INTERVAL`` على الأكثر إلا بعد مهمّة، وفي خيطٍ خلفي لا يؤخّر
شيئًا، وفشله صامت: جهازٌ بلا إنترنت حالٌ طبيعية لا خطأ.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from utils.logger import logger

#: ثوانٍ بين إرسالين ما لم يُطلب بعد مهمّة.
MIN_INTERVAL = 6 * 3600
STATE_FILENAME = "report_state.json"


def _state_path() -> Path:
    from core.stats import default_stats_dir
    return default_stats_dir() / STATE_FILENAME


def _last_sent() -> float:
    try:
        return float(json.loads(_state_path().read_text(encoding="utf-8"))
                     .get("last_sent") or 0.0)
    except Exception:                                 # noqa: BLE001
        return 0.0


def _remember(now: float) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_sent": now}), encoding="utf-8")
    except OSError:
        pass


def enabled(config) -> bool:
    if os.environ.get("VTAD_DISABLE_USAGE_REPORT"):
        return False                      # الاختبارات وبيئات البناء
    return bool(getattr(getattr(config, "application", None),
                        "share_usage_stats", False))


def maybe_report(config, *, force: bool = False, now: Optional[float] = None,
                 sender: Optional[Callable] = None) -> bool:
    """يرسل إن تحقّقت الشروط. يعيد ``True`` إن أُرسل. لا يرمي أبدًا."""
    try:
        if not enabled(config):
            return False
        from licensing import app_gate
        if not app_gate.licensing_enabled():
            return False
        code = app_gate.current_code()
        if not code:
            return False
        now = time.time() if now is None else now
        last = _last_sent()
        if not force and last and 0 <= now - last < MIN_INTERVAL:
            return False
        from core import stats
        from version import APP_VERSION
        payload = stats.to_payload(stats.collect(config))
        if sender is None:
            from licensing import client
            sender = client.report_usage
        sender(code, app_gate.device_id(), payload, APP_VERSION)
        _remember(now)
        return True
    except Exception as exc:                          # noqa: BLE001
        logger.info(f"لم تُرسل إحصاءات الاستخدام: {exc}")
        return False


def report_in_background(config, *, force: bool = False) -> None:
    if not enabled(config):
        return
    threading.Thread(target=maybe_report, args=(config,),
                     kwargs={"force": force}, daemon=True,
                     name="usage-stats").start()
