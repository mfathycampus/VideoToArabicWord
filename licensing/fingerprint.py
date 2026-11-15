"""بصمة الجهاز — تربط الكود بجهازٍ واحد.

المصادر بالترتيب: ``MachineGuid`` من سجلّ ويندوز (يبقى عبر إعادة تثبيت
البرنامج ويتغيّر بتغيّر نسخة النظام)، ثم الرقم التسلسلي لقرص النظام،
ثم اسم الجهاز والمستخدم. تُدمج وتُهشَّم، فلا يخرج من الجهاز معرّفٌ
يمكن ردّه إلى صاحبه.

**ولا نستعمل عنوان MAC:** يتغيّر بتبديل شبكة أو بطاقة، ويمكن انتحاله
بأمرٍ واحد — فيعاقب المستخدم الصادق ولا يوقف المتحايل.

**ولا اسم الجهاز ولا اسم المستخدم (1.22.7):** كانا في البصمة، فإعادة تسمية
الجهاز أو الدخول بحساب ويندوز آخر كانت تغيّر البصمة — فيُرفض الترخيص على
الجهاز نفسه ويطلب المعلّم «تصفير الجهاز» من المزوّد. البصمة الآن من معرّفات
الجهاز وحدها (``device_fingerprint``). والقديمة (``legacy_fingerprint``) تُحسب
أيضًا ليُقبل ما صدر لها وينتقل إلى الجديدة عند أول إعادة تحقّق — انظر
``licensing/app_gate.machine_ids``.
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


def _macos_platform_uuid() -> str:
    """``IOPlatformUUID`` من ioreg — معرّف الجهاز الثابت على ماك."""
    if sys.platform != "darwin":
        return ""
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=10)
        return parse_ioreg_uuid(out.stdout or "")
    except Exception:                                  # noqa: BLE001
        return ""


def parse_ioreg_uuid(text: str) -> str:
    for line in text.splitlines():
        if "IOPlatformUUID" in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _linux_machine_id() -> str:
    if not sys.platform.startswith("linux"):
        return ""
    for name in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = open(name, encoding="utf-8").read().strip()
        except OSError:
            continue
        if value:
            return value
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


def _hash(parts) -> str:
    raw = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _hardware_ids() -> list:
    return [_windows_machine_guid(), _windows_volume_serial(),
            _macos_platform_uuid(), _linux_machine_id()]


def legacy_fingerprint() -> str:
    """بصمة ما قبل 1.22.7: معرّفات الجهاز + اسمه + اسم المستخدم."""
    return _hash([*_hardware_ids(), platform.node(),
                  os.environ.get("USERNAME") or os.environ.get("USER") or "",
                  platform.machine()])


def device_fingerprint() -> str:
    """‏20 حرفًا سِتّ عشريًّا تُمثّل هذا الجهاز — لا تتغيّر بإعادة تسميته.

    جهازٌ لا يُقرأ منه أيّ معرّف (بيئة مقيّدة) يبقى على البصمة القديمة: بلا
    اسم الجهاز والمستخدم لكانت بصمته واحدة لكل جهاز مثله.
    """
    hardware = [part for part in _hardware_ids() if part and part.strip()]
    if not hardware:
        return legacy_fingerprint()
    return _hash([*hardware, platform.machine()])
