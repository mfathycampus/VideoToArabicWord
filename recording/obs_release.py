"""إصدار OBS Studio المثبَّت: الرابط والتجزئة والحجم.

**الإصدار مثبَّت عمدًا لا «الأحدث».** تنزيل «الأحدث» يعني أن البرنامج
يشغّل شيفرة تنفيذية لم يفحصها أحد وقت الإصدار؛ أما التثبيت مع SHA-256
فيعني أن الملف الذي يُشغَّل هو بالضبط الذي فُحص. وتحديث الإصدار قرار
يُتّخذ هنا (مع تجربة على ويندوز) ثم يُشحن مع تحديث للبرنامج.

التجزئات مأخوذة من حقل ``digest`` في أصول إصدار GitHub الرسمي
(‏obsproject/obs-studio، الوسم 32.2.2) ومطابَقة بصفحة الإصدار.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class ObsRelease:
    version: str
    name: str
    url: str
    sha256: str
    size_bytes: int

    @property
    def size_mb(self) -> int:
        return round(self.size_bytes / (1024 * 1024))

    @property
    def version_int(self) -> int:
        """ترميز OBS للإصدار في ``LastVersion``: major<<24 | minor<<16 | patch."""
        parts = [int(p) for p in self.version.split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return (parts[0] << 24) | (parts[1] << 16) | parts[2]


_BASE = "https://github.com/obsproject/obs-studio/releases/download"

OBS_VERSION = "32.2.2"

WINDOWS_X64 = ObsRelease(
    version=OBS_VERSION,
    name=f"OBS-Studio-{OBS_VERSION}-Windows-x64.zip",
    url=f"{_BASE}/{OBS_VERSION}/OBS-Studio-{OBS_VERSION}-Windows-x64.zip",
    sha256="4d6e40e3ab155f56b30de517380566a206d74b63cdf5ad49aa596924768f97e1",
    size_bytes=187_851_583,
)

WINDOWS_ARM64 = ObsRelease(
    version=OBS_VERSION,
    name=f"OBS-Studio-{OBS_VERSION}-Windows-arm64.zip",
    url=f"{_BASE}/{OBS_VERSION}/OBS-Studio-{OBS_VERSION}-Windows-arm64.zip",
    sha256="b16b41c3cf89b8a919738359a46acb25d207ec1e488563139d7c276cfffb0906",
    size_bytes=167_128_219,
)


def select_release(machine: str | None = None) -> ObsRelease:
    """الحزمة المناسبة لمعمارية الجهاز (x64 افتراضيًا)."""
    machine = (machine or platform.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        return WINDOWS_ARM64
    return WINDOWS_X64
