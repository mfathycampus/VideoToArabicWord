"""جلسة تسجيل الشاشة: تثبيت ← تشغيل OBS ← ضبط ← تسجيل ← إيقاف ← ملف.

الواجهة (``ui/recording_dialog``) تستدعي هذه الفئة فقط ولا تعرف شيئًا عن
OBS. الناتج ملف MKV في مجلد الحفظ يُسلَّم لخط المعالجة كأي فيديو.

**الموافقة ليست من شأن هذه الفئة** — الواجهة تأخذ الإقرار من المستخدم
قبل ``start()``. لكن الفئة تكتب بجوار كل تسجيل ملف
``<الاسم>.recording.json`` فيه توقيت البدء والإيقاف وإقرار الموافقة، ليكون
للتسجيل أثرٌ قابل للمراجعة.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from config.settings import AppConfig
from core.exceptions import (
    InsufficientDiskSpaceError,
    RecordingControlError,
    RecordingToolDownloadError,
    RecordingToolUnavailableError,
)
from recording import obs_config, obs_installer, obs_log
from recording.obs_controller import CaptureSettings, ObsRecorder, ObsWebSocket
from recording.obs_process import ObsProcess
from recording.obs_release import ObsRelease, select_release
from utils.cancellation import CancellationToken
from utils.logger import logger

EventFn = Callable[[str], None]
ProgressFn = Callable[[float, str], None]

#: أول إقلاع لـ OBS قد يطول (تحميل الإضافات، ومضاد الفيروسات يفحصه).
_LAUNCH_TIMEOUT = 90.0


class RecorderState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"


@dataclass(frozen=True)
class RecordingResult:
    path: Path
    started_at: float
    stopped_at: float
    recovered: bool = False     # لم يُنهِ OBS التسجيل بنفسه فاستُرجع الملف

    @property
    def duration_seconds(self) -> float:
        return max(self.stopped_at - self.started_at, 0.0)


class MeetingRecorder:
    def __init__(self, config: AppConfig, *,
                 release: Optional[ObsRelease] = None,
                 root: Optional[Path] = None,
                 process_factory: Callable[..., ObsProcess] = ObsProcess,
                 client_factory: Callable[..., ObsWebSocket] = ObsWebSocket,
                 on_event: Optional[EventFn] = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self.release = release or select_release()
        self.root = root
        self._process_factory = process_factory
        self._client_factory = client_factory
        self._on_event = on_event
        self._clock = clock
        self._sleep = sleep

        self.state = RecorderState.IDLE
        self._process: Optional[ObsProcess] = None
        self._client: Optional[ObsWebSocket] = None
        self._recorder: Optional[ObsRecorder] = None
        self._started_at = 0.0
        self._consent = False

    # -- الأداة ----------------------------------------------------
    def tool_path(self) -> Optional[Path]:
        """مسار obs64.exe: المحدَّد يدويًا أولًا، ثم النسخة المنزَّلة."""
        custom = (self.config.recording.obs_path or "").strip()
        if custom:
            path = Path(custom)
            return path if path.is_file() else None
        return obs_installer.installed_exe(self.release, self.root)

    def tool_ready(self) -> bool:
        return self.tool_path() is not None

    def can_install(self) -> bool:
        rec = self.config.recording
        return rec.enabled and rec.allow_download and not rec.obs_path.strip()

    def install(self, progress: Optional[ProgressFn] = None,
                cancel: Optional[CancellationToken] = None) -> Path:
        rec = self.config.recording
        if not rec.enabled:
            raise RecordingToolUnavailableError(
                "تسجيل الشاشة معطّل بسياسة الجهاز.")
        if not rec.allow_download:
            raise RecordingToolDownloadError(
                "تنزيل أداة التسجيل ممنوع بسياسة الجهاز. اطلب من مسؤول "
                "تقنية المعلومات نشر OBS وتحديد مساره في الإعداد.")
        self._emit("تنزيل أداة التسجيل (OBS Studio) من المصدر الرسمي…")
        exe = obs_installer.download_and_install(
            self.release, self.root, progress=progress, cancel=cancel)
        self._emit("اكتمل تثبيت أداة التسجيل.")
        return exe

    # -- التسجيل ---------------------------------------------------
    def start(self, *, consent_confirmed: bool) -> None:
        """يشغّل OBS ويضبطه ويبدأ التسجيل. الاستدعاء حاجز (قد يطول)."""
        if self.state is not RecorderState.IDLE:
            raise RecordingControlError("هناك تسجيل جارٍ بالفعل.")
        if not consent_confirmed:
            raise RecordingControlError(
                "لا يبدأ التسجيل قبل إقرار علم المشاركين بموافقتهم.")
        rec = self.config.recording
        if not rec.enabled:
            raise RecordingToolUnavailableError(
                "تسجيل الشاشة معطّل بسياسة الجهاز.")
        exe = self.tool_path()
        if exe is None:
            raise RecordingToolUnavailableError(
                "أداة التسجيل غير مثبّتة بعد.")

        out_dir = rec.resolved_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(out_dir).free / (1024 ** 3)
        if free_gb < rec.min_free_gb:
            raise InsufficientDiskSpaceError(
                f"المساحة الحرة في «{out_dir}» ({free_gb:.1f} جيجابايت) أقل من "
                f"الحد الأدنى للتسجيل ({rec.min_free_gb:.1f}).")

        self.state = RecorderState.STARTING
        self._consent = True
        try:
            port = obs_config.find_free_port()
            password = obs_config.new_password()
            obs_config.seed_config(exe, self.release, port, password)
            obs_config.seed_profile(
                exe, out_dir, video_width=rec.video_width,
                video_fps=rec.video_fps)
            self._emit("تشغيل أداة التسجيل…")
            self._process = self._process_factory(
                exe, obs_config.launch_args(exe, port, password))
            self._process.start()
            self._client = self._connect(port, password)
            self._emit("ضبط الشاشة والصوت…")
            self._recorder = ObsRecorder(self._client, sleep=self._sleep)
            self._recorder.prepare(CaptureSettings(
                record_dir=out_dir,
                capture_system_audio=rec.capture_system_audio,
                capture_microphone=rec.capture_microphone,
                video_width=rec.video_width,
                video_fps=rec.video_fps))
            self._recorder.start()
        except BaseException as exc:
            tail = obs_log.log_tail(exe)
            self._teardown()
            self.state = RecorderState.IDLE
            if tail and isinstance(exc, RecordingControlError):
                raise RecordingControlError(
                    f"{exc}\n\nمن سجلّ OBS:\n{tail}") from exc
            raise
        self._started_at = self._clock()
        self.state = RecorderState.RECORDING
        self._emit("بدأ التسجيل.")

    def stop(self) -> RecordingResult:
        """يوقف التسجيل ويعيد الملف. يغلق OBS في كل الأحوال."""
        if self.state is not RecorderState.RECORDING:
            raise RecordingControlError("لا يوجد تسجيل جارٍ.")
        self.state = RecorderState.STOPPING
        recovered = False
        path: Optional[Path] = None
        try:
            try:
                raw = self._recorder.stop()
                path = Path(raw) if raw else None
            except RecordingControlError as exc:
                logger.warning(f"إيقاف التسجيل عبر OBS فشل: {exc}")
                recovered = True
        finally:
            self._teardown()
            self.state = RecorderState.IDLE
        stopped_at = self._clock()

        if path is None or not path.is_file():
            path = self._latest_recording(since=self._started_at)
            recovered = True
        if path is None:
            raise RecordingControlError(
                "انتهى التسجيل لكن لم يُعثر على الملف في مجلد الحفظ.")
        self._write_sidecar(path, stopped_at, recovered)
        self._emit(f"حُفظ التسجيل: {path.name}")
        return RecordingResult(path, self._started_at, stopped_at, recovered)

    def abort(self) -> None:
        """إيقاف طارئ (إغلاق البرنامج). يحاول إنهاء الملف قبل الإغلاق."""
        if self.state is RecorderState.RECORDING:
            try:
                self._recorder.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"إيقاف طارئ: {exc}")
        self._teardown()
        self.state = RecorderState.IDLE

    def elapsed(self) -> float:
        if self.state is RecorderState.RECORDING:
            return max(self._clock() - self._started_at, 0.0)
        return 0.0

    # -- داخلي -----------------------------------------------------
    def _connect(self, port: int, password: str) -> ObsWebSocket:
        deadline = time.monotonic() + _LAUNCH_TIMEOUT
        last: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self._process is not None and not self._process.alive():
                raise RecordingToolUnavailableError(
                    "أُغلقت أداة التسجيل فور تشغيلها. جرّب التشغيل مرة أخرى، "
                    "وإن تكرّر فقد يكون مضاد الفيروسات يحجبها.")
            client = self._client_factory("127.0.0.1", port, password)
            try:
                client.open()
                return client
            except RecordingToolUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - الخادم لم يجهز بعد
                last = exc
                self._sleep(1.0)
        raise RecordingControlError(
            f"لم تستجب أداة التسجيل في {int(_LAUNCH_TIMEOUT)} ثانية ({last}). "
            "إن ظهرت نافذة من ويندوز تطلب السماح لـ OBS بالشبكة فاضغط «إلغاء» "
            "أو «السماح» ثم أعد المحاولة.")

    def _teardown(self) -> None:
        if self._client is not None:
            self._client.close()
        if self._process is not None:
            self._process.close()
        self._client = self._process = self._recorder = None

    def _latest_recording(self, since: float) -> Optional[Path]:
        folder = self.config.recording.resolved_output_dir()
        candidates = [p for p in folder.glob("*.mkv")
                      if p.stat().st_mtime >= since - 5]
        return max(candidates, key=lambda p: p.stat().st_mtime, default=None)

    def _write_sidecar(self, path: Path, stopped_at: float,
                       recovered: bool) -> None:
        def stamp(value: float) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(value))
        try:
            path.with_suffix(".recording.json").write_text(json.dumps({
                "started_at": stamp(self._started_at),
                "stopped_at": stamp(stopped_at),
                "duration_seconds": round(stopped_at - self._started_at, 1),
                "consent_confirmed": self._consent,
                "recorder": f"OBS Studio {self.release.version}",
                "recovered_after_error": recovered,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(f"تعذّرت كتابة سجلّ التسجيل: {exc}")

    def _emit(self, message: str) -> None:
        logger.info(message)
        if self._on_event:
            self._on_event(message)
