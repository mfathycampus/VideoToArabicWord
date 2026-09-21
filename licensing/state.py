"""حالة الترخيص على الجهاز — في ثلاثة مواضع مستقلة.

السبب: حذف ملفٍّ واحد أو تعديله يجب ألّا يعيد المدّة. تُقرأ المواضع
الثلاثة ويُؤخذ **الأحدث** منها، وتُكتب كلها معًا. فمن يمسح مجلد
الإعدادات يجد التاريخ في السجلّ، ومن ينظّف السجلّ يجده في المجلد الثالث.

وكل نسخة موقَّعة بـ HMAC بمفتاح مشتقّ من **بصمة الجهاز**: تحريرها بيد
يُفسد التوقيع، ونسخها إلى جهاز آخر لا تصلح فيه.

وما يُخزَّن ليس تاريخًا واحدًا بل: آخر وقتٍ رآه البرنامج (لكشف إرجاع
الساعة)، و**أيام الاستخدام الفعلية** (لأن المدّة تُستهلك بالأيام
المستعملة لا بفارق تاريخين يمكن العبث بطرفيه).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

#: مِلحٌ ثابت في الشيفرة. ليس سرًّا يحمي من مهاجم يملك الملف — يمنع
#: التحرير العابر بمحرّر نصوص، لا أكثر، وهذا كل ما يُدّعى له.
_SALT = b"VideoToArabicWord/license-state/v1"

_REGISTRY_PATH = r"Software\VideoToArabicWord"
_REGISTRY_VALUE = "State"


def _key(device: str) -> bytes:
    return hashlib.sha256(_SALT + device.encode("utf-8")).digest()


def _seal(data: dict, device: str) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tag = hmac.new(_key(device), raw, hashlib.sha256).hexdigest()[:32]
    return json.dumps({"d": data, "t": tag}, ensure_ascii=False)


def _open(text: str, device: str) -> Optional[dict]:
    try:
        envelope = json.loads(text)
        data, tag = envelope["d"], envelope["t"]
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception:                                  # noqa: BLE001
        return None
    good = hmac.new(_key(device), raw, hashlib.sha256).hexdigest()[:32]
    return data if hmac.compare_digest(tag, good) else None


def config_state_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "VideoToArabicWord" / "state.json"


def shadow_state_path() -> Path:
    """موضعٌ ثانٍ خارج مجلد الإعدادات — يبقى بعد «حذف كل شيء»."""
    base = (os.environ.get("PROGRAMDATA") or os.environ.get("LOCALAPPDATA"))
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "VideoToArabicWord" / ".runtime"


class StateStore:
    """قراءة/كتابة الحالة في كل المواضع المتاحة."""

    def __init__(self, device: str,
                 paths: Optional[Iterable[Path]] = None,
                 use_registry: Optional[bool] = None) -> None:
        self.device = device
        self.paths = list(paths) if paths is not None else [
            config_state_path(), shadow_state_path()]
        self.use_registry = (sys.platform.startswith("win")
                             if use_registry is None else use_registry)

    # ------------------------------------------------------------------
    def _read_registry(self) -> Optional[dict]:
        if not self.use_registry:
            return None
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                return _open(str(winreg.QueryValueEx(key, _REGISTRY_VALUE)[0]),
                             self.device)
        except Exception:                              # noqa: BLE001
            return None

    def _write_registry(self, sealed: str) -> None:
        if not self.use_registry:
            return
        try:
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_SZ, sealed)
        except Exception:                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def read(self) -> dict:
        """يدمج ما وجد: أحدث وقت، واتحاد أيام الاستخدام، وأحدث عقد."""
        found = [self._read_registry()]
        for path in self.paths:
            try:
                found.append(_open(path.read_text(encoding="utf-8"), self.device))
            except Exception:                          # noqa: BLE001
                found.append(None)
        states = [s for s in found if isinstance(s, dict)]
        if not states:
            return {}
        days: set[str] = set()
        for state in states:
            days.update(state.get("days") or [])
        newest = max(states, key=lambda s: int(s.get("saved_at") or 0))
        merged = dict(newest)
        merged["last_seen"] = max(int(s.get("last_seen") or 0) for s in states)
        merged["days"] = sorted(days)
        return merged

    def write(self, state: dict) -> None:
        sealed = _seal(state, self.device)
        for path in self.paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(sealed, encoding="utf-8")
            except Exception:                          # noqa: BLE001
                continue
        self._write_registry(sealed)
