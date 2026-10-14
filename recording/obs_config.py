"""تهيئة نسخة OBS المحمولة قبل أول تشغيل، وبناء سطر التشغيل.

ما يُكتب هنا قليلٌ عمدًا: نكتب فقط ما **يجب** أن يكون موجودًا قبل أن
يقلع OBS —

1. إعداد obs-websocket (المنفذ وكلمة المرور) ليكون الخادم جاهزًا فور
   الإقلاع؛
2. علامتا ``FirstRun`` و``LastVersion`` لمنع نافذة «معالج الإعداد
   التلقائي» ونافذة «ما الجديد» اللتين تعترضان أول تشغيل؛

وكل ما عدا ذلك (الملف الشخصي، المشهد، مصادر الصوت والشاشة، الدقة،
مجلد الحفظ) يُضبط بعد الإقلاع عبر obs-websocket في ``obs_controller``،
لأنه واجهة موثَّقة ومُرقَّمة الإصدار، بخلاف تخطيط ملفات OBS الداخلية.
"""
from __future__ import annotations

import configparser
import json
import secrets
import socket
from pathlib import Path
from typing import List

from recording.obs_installer import portable_root
from recording.obs_release import ObsRelease

PROFILE_NAME = "Maeen"
COLLECTION_NAME = "Maeen"
SCENE_NAME = "Maeen"
INPUT_SCREEN = "Maeen Screen"
INPUT_DESKTOP_AUDIO = "Maeen Desktop Audio"
INPUT_MICROPHONE = "Maeen Microphone"

#: اسم الملف المسجَّل: ``Maeen-Recording_2026-10-13_12-30-00.mkv``.
FILENAME_FORMAT = "Maeen-Recording_%CCYY-%MM-%DD_%hh-%mm-%ss"


def config_dir(exe: Path) -> Path:
    """مجلد إعداد OBS في الوضع المحمول."""
    return portable_root(exe) / "config" / "obs-studio"


def find_free_port() -> int:
    """منفذ محلي حرّ — لا نفترض 4455 كي لا نصطدم بـ OBS آخر عند المستخدم."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def new_password() -> str:
    """كلمة مرور عشوائية لكل جلسة تسجيل.

    خادم obs-websocket يستمع على كل الواجهات لا على localhost وحدها، فلا
    نعتمد على العزل بالشبكة: التوثيق مفروض وكلمة المرور لا تُحفظ ولا
    تُعاد بين الجلسات.
    """
    return secrets.token_urlsafe(18)


def _merge_ini(path: Path, section: str, values: dict) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # OBS يفرّق بين الأحرف الكبيرة والصغيرة
    if path.is_file():
        try:
            parser.read(path, encoding="utf-8-sig")
        except configparser.Error:
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
    if not parser.has_section(section):
        parser.add_section(section)
    for key, value in values.items():
        parser.set(section, key, str(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        parser.write(handle, space_around_delimiters=False)


def seed_config(exe: Path, release: ObsRelease, port: int, password: str) -> Path:
    """يكتب إعداد ما قبل الإقلاع ويعيد مجلد الإعداد."""
    base = config_dir(exe)
    ws_dir = base / "plugin_config" / "obs-websocket"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "config.json").write_text(json.dumps({
        "alerts_enabled": False,
        "auth_required": True,
        "first_load": False,
        "server_enabled": True,
        "server_password": password,
        "server_port": port,
    }, indent=4), encoding="utf-8")

    general = {"FirstRun": "true", "LastVersion": release.version_int}
    # الإصدارات الحديثة تفصل global.ini عن user.ini؛ نكتب في الاثنين.
    for name in ("global.ini", "user.ini"):
        _merge_ini(base / name, "General", general)
    return base


def canvas_size(width: int, height: int) -> tuple:
    """حجم اللوحة الأساسي: أبعاد الشاشة زوجية (متطلب x264/NV12)."""
    return (max(int(width) - int(width) % 2, 2),
            max(int(height) - int(height) % 2, 2))


def output_size(base_w: int, base_h: int, max_width: int) -> tuple:
    """حجم الفيديو الناتج: لا يتجاوز ``max_width`` ويحفظ النسبة، بأبعاد زوجية."""
    out_w = min(int(max_width), int(base_w))
    out_w -= out_w % 2
    out_h = round(out_w * base_h / base_w)
    out_h -= out_h % 2
    return (max(out_w, 2), max(out_h, 2))


def seed_profile(exe: Path, record_dir: Path, *, video_width: int,
                 video_fps: int, base: tuple = (1920, 1080)) -> Path:
    """يكتب ملف التسجيل الشخصي على القرص **قبل** إقلاع OBS.

    OBS يبني مُرمِّز التسجيل والـ muxer عند تحميل الملف الشخصي، فتغيير
    الإعدادات لاحقًا عبر obs-websocket لا يُطبَّق على المخرجات القائمة ويفشل
    ``StartRecord`` بصمت («Starting the output failed»). لذا تُكتب الإعدادات
    هنا ويُشغَّل OBS بـ ``--profile``. الأبعاد الفعلية للشاشة تُضبط بعد
    الإقلاع بقياس المصدر (انظر ``ObsRecorder``)، وهنا قيمة افتراضية معقولة.
    """
    path = config_dir(exe) / "basic" / "profiles" / PROFILE_NAME / "basic.ini"
    base_w, base_h = canvas_size(*base)
    out_w, out_h = output_size(base_w, base_h, video_width)
    directory = str(record_dir).replace("\\", "/")
    _merge_ini(path, "General", {"Name": PROFILE_NAME})
    _merge_ini(path, "Output", {
        "Mode": "Simple", "FilenameFormatting": FILENAME_FORMAT})
    _merge_ini(path, "SimpleOutput", {
        "FilePath": directory, "RecFormat2": "mkv", "RecFormat": "mkv",
        "RecQuality": "Small", "RecEncoder": "x264", "RecTracks": 1,
        "RecAudioEncoder": "aac", "UseAdvanced": "false"})
    _merge_ini(path, "Video", {
        "BaseCX": base_w, "BaseCY": base_h, "OutputCX": out_w, "OutputCY": out_h,
        "FPSType": 2, "FPSNum": int(video_fps), "FPSDen": 1})
    return path


def launch_args(exe: Path, port: int, password: str) -> List[str]:
    """سطر تشغيل OBS: محمول، مصغَّر في الصينية، بلا تحديث ولا أسئلة."""
    return [
        str(exe),
        "--portable",
        "--profile", PROFILE_NAME,
        "--multi",                    # لا تحذير إن كان عند المستخدم OBS آخر
        "--minimize-to-tray",
        "--disable-updater",
        "--disable-shutdown-check",   # بلا «الوضع الآمن» بعد إغلاق قسري
        "--disable-missing-files-check",
        "--websocket_port", str(port),
        "--websocket_password", password,
        "--websocket_ipv4_only",
    ]
