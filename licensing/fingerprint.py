"""بصمة الجهاز — تربط الكود بجهازٍ واحد.

المصادر بالترتيب: ``MachineGuid`` من سجلّ ويندوز (يبقى عبر إعادة تثبيت
البرنامج ويتغيّر بتغيّر نسخة النظام)، ثم الرقم التسلسلي لقرص النظام،
ثم اسم الجهاز والمستخدم. تُدمج وتُهشَّم، فلا يخرج من الجهاز معرّفٌ
يمكن ردّه إلى صاحبه.

**ولا نستعمل عنوان MAC:** يتغيّر بتبديل شبكة أو بطاقة، ويمكن انتحاله
بأمرٍ واحد — فيعاقب المستخدم الصادق ولا يوقف المتحايل.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys


def _windows_machine_guid() -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except Exception:                                  # noqa: BLE001
        return ""


def _windows_volume_serial() -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        out = subprocess.run(
            ["cmd", "/c", "vol", os.environ.get("SystemDrive", "C:")],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (out.stdout or "").splitlines():
            if "-" in line and any(ch.isdigit() for ch in line):
                return line.strip().split()[-1]
    except Exception:                                  # noqa: BLE001
        return ""
    return ""


def device_fingerprint() -> str:
    """‏20 حرفًا سِتّ عشريًّا تُمثّل هذا الجهاز."""
    parts = [
        _windows_machine_guid(),
        _windows_volume_serial(),
        platform.node(),
        os.environ.get("USERNAME") or os.environ.get("USER") or "",
        platform.machine(),
    ]
    raw = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
