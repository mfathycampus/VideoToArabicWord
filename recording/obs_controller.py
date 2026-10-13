"""التحكم في OBS عبر obs-websocket v5 (مضمَّن في OBS 28+).

طبقتان:

* ``ObsWebSocket`` — عميل بروتوكول: مصافحة وتوثيق وطلب/استجابة.
* ``ObsRecorder`` — ما نريده من OBS: ملف شخصي ومشهد ومصادر وإخراج، ثم
  بدء التسجيل وإيقافه.

كلتاهما تقبل حقن الاتصال (للاختبار)، فلا حاجة لتشغيل OBS لفحص المنطق.
المرجع: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.exceptions import RecordingControlError
from recording.obs_config import (
    COLLECTION_NAME,
    INPUT_DESKTOP_AUDIO,
    INPUT_MICROPHONE,
    INPUT_SCREEN,
    PROFILE_NAME,
    SCENE_NAME,
)

RPC_VERSION = 1
_OP_HELLO, _OP_IDENTIFY, _OP_IDENTIFIED, _OP_REQUEST, _OP_RESPONSE = 0, 1, 2, 6, 7
#: رمز حالة obs-websocket: المورد غير موجود.
CODE_NOT_FOUND = 600


class ObsRequestFailed(RecordingControlError):
    """طلب رفضه OBS. ``code`` هو رمز obs-websocket."""

    def __init__(self, request_type: str, code: int, comment: str = "") -> None:
        self.request_type = request_type
        self.code = code
        self.comment = comment
        super().__init__(
            f"رفض OBS الطلب «{request_type}» (الرمز {code})"
            + (f": {comment}" if comment else ""))


def compute_auth(password: str, salt: str, challenge: str) -> str:
    """توثيق obs-websocket: ``b64(sha256(b64(sha256(pw+salt)) + challenge))``."""
    secret = base64.b64encode(
        hashlib.sha256((password + salt).encode("utf-8")).digest()).decode()
    return base64.b64encode(
        hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode()


def _default_connect(url: str, timeout: float):
    """يفتح اتصال obs-websocket بعميلنا القياسي (بلا حزم خارجية)."""
    from urllib.parse import urlparse

    from recording.ws_client import WebSocketClient

    parsed = urlparse(url)
    return WebSocketClient(
        parsed.hostname or "127.0.0.1", parsed.port or 4455, timeout=timeout,
        subprotocol="obswebsocket.json").connect()


class ObsWebSocket:
    """عميل obs-websocket v5 بسيط: طلب واحد في كل مرة، بلا أحداث."""

    def __init__(self, host: str, port: int, password: str, *,
                 connect: Optional[Callable] = None,
                 timeout: float = 10.0) -> None:
        self.host, self.port, self.password = host, port, password
        self.timeout = timeout
        self._connect = connect or _default_connect
        self._ws = None
        self.obs_version = ""
        self.websocket_version = ""

    # -- الاتصال ---------------------------------------------------
    def open(self) -> None:
        self._ws = self._connect(f"ws://{self.host}:{self.port}", self.timeout)
        try:
            hello = self._recv()
            if hello.get("op") != _OP_HELLO:
                raise RecordingControlError("ردّ غير متوقع من OBS عند المصافحة.")
            data = hello.get("d", {})
            self.websocket_version = str(data.get("obsWebSocketVersion", ""))
            identify: Dict[str, Any] = {
                "rpcVersion": RPC_VERSION,
                "eventSubscriptions": 0,
            }
            auth = data.get("authentication")
            if auth:
                identify["authentication"] = compute_auth(
                    self.password, auth["salt"], auth["challenge"])
            self._send({"op": _OP_IDENTIFY, "d": identify})
            reply = self._recv()
            if reply.get("op") != _OP_IDENTIFIED:
                raise RecordingControlError("رفض OBS التوثيق.")
        except RecordingControlError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RecordingControlError(
                f"فشلت المصافحة مع OBS (غالبًا كلمة المرور أو إغلاق الاتصال): {exc}"
            ) from exc

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001 - الإغلاق لا يجوز أن يفشل
                pass

    # -- الطلبات ---------------------------------------------------
    def request(self, request_type: str,
                data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._ws is None:
            raise RecordingControlError("لا اتصال مفتوح مع OBS.")
        request_id = uuid.uuid4().hex
        payload: Dict[str, Any] = {
            "requestType": request_type, "requestId": request_id}
        if data:
            payload["requestData"] = data
        self._send({"op": _OP_REQUEST, "d": payload})
        while True:
            message = self._recv()
            if message.get("op") != _OP_RESPONSE:
                continue
            body = message.get("d", {})
            if body.get("requestId") != request_id:
                continue
            status = body.get("requestStatus", {})
            if not status.get("result"):
                raise ObsRequestFailed(request_type, int(status.get("code", 0)),
                                       str(status.get("comment", "")))
            return body.get("responseData") or {}

    # -- داخلي -----------------------------------------------------
    def _send(self, message: Dict[str, Any]) -> None:
        try:
            self._ws.send(json.dumps(message))
        except Exception as exc:  # noqa: BLE001 - أي عطل شبكي
            raise RecordingControlError(f"انقطع الاتصال بـ OBS: {exc}") from exc

    def _recv(self) -> Dict[str, Any]:
        try:
            raw = self._ws.recv()
            if not raw:
                raise RecordingControlError("أغلق OBS الاتصال.")
            return json.loads(raw)
        except RecordingControlError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RecordingControlError(f"انقطع الاتصال بـ OBS: {exc}") from exc


# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CaptureSettings:
    record_dir: Path
    capture_system_audio: bool = True
    capture_microphone: bool = True
    video_width: int = 1600
    video_fps: int = 10


class ObsRecorder:
    """يهيّئ OBS لتسجيل الشاشة ويبدأ التسجيل ويوقفه."""

    def __init__(self, client, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self._sleep = sleep
        self._clock = clock

    # -- التهيئة ---------------------------------------------------
    def prepare(self, settings: CaptureSettings) -> None:
        self._ensure_profile()
        self._ensure_collection()
        self._ensure_scene()
        kinds = self._input_kinds()
        screen_kind = next(
            (k for k in ("monitor_capture", "display_capture") if k in kinds), None)
        if screen_kind is None:
            raise RecordingControlError(
                "نسخة OBS هذه لا تدعم التقاط الشاشة (monitor_capture).")
        for needed in ("wasapi_output_capture", "wasapi_input_capture"):
            if needed not in kinds:
                raise RecordingControlError(
                    f"نسخة OBS هذه لا تدعم مصدر الصوت «{needed}».")

        screen_id = self._ensure_input(INPUT_SCREEN, screen_kind, {})
        self._ensure_input(INPUT_DESKTOP_AUDIO, "wasapi_output_capture",
                           {"device_id": "default"})
        self._ensure_input(INPUT_MICROPHONE, "wasapi_input_capture",
                           {"device_id": "default"})
        for name, enabled in ((INPUT_DESKTOP_AUDIO, settings.capture_system_audio),
                              (INPUT_MICROPHONE, settings.capture_microphone)):
            # كل الصوت في المسار 1: خط المعالجة يقرأ أول مسار صوتي فقط
            # (utils/ffmpeg_service: -map 0:a:0).
            self.client.request("SetInputAudioTracks", {
                "inputName": name,
                "inputAudioTracks": {str(i): i == 1 for i in range(1, 7)},
            })
            self.client.request("SetInputMute", {
                "inputName": name, "inputMuted": not enabled})

        self._select_monitor()
        self._fit_screen(screen_id)
        self._verify_output_profile()

    def _ensure_profile(self) -> None:
        listing = self.client.request("GetProfileList")
        if PROFILE_NAME not in listing.get("profiles", []):
            self.client.request("CreateProfile", {"profileName": PROFILE_NAME})
        elif listing.get("currentProfileName") != PROFILE_NAME:
            self.client.request("SetCurrentProfile", {"profileName": PROFILE_NAME})
        self._wait(lambda: self.client.request("GetProfileList").get(
            "currentProfileName") == PROFILE_NAME,
            "تعذّر تفعيل ملف تعريف التسجيل في OBS.")

    def _ensure_collection(self) -> None:
        listing = self.client.request("GetSceneCollectionList")
        if COLLECTION_NAME not in listing.get("sceneCollections", []):
            self.client.request("CreateSceneCollection",
                                {"sceneCollectionName": COLLECTION_NAME})
        elif listing.get("currentSceneCollectionName") != COLLECTION_NAME:
            self.client.request("SetCurrentSceneCollection",
                                {"sceneCollectionName": COLLECTION_NAME})
        self._wait(lambda: self.client.request("GetSceneCollectionList").get(
            "currentSceneCollectionName") == COLLECTION_NAME,
            "تعذّر تفعيل مجموعة المشاهد في OBS.")

    def _ensure_scene(self) -> None:
        scenes = self.client.request("GetSceneList").get("scenes", [])
        if SCENE_NAME not in [s.get("sceneName") for s in scenes]:
            self.client.request("CreateScene", {"sceneName": SCENE_NAME})
        self.client.request("SetCurrentProgramScene", {"sceneName": SCENE_NAME})

    def _input_kinds(self) -> list:
        return self.client.request(
            "GetInputKindList", {"unversioned": True}).get("inputKinds", [])

    def _ensure_input(self, name: str, kind: str, settings: dict) -> int:
        """يضمن وجود المصدر داخل مشهدنا ويعيد معرّف عنصر المشهد."""
        existing = [i.get("inputName") for i in
                    self.client.request("GetInputList").get("inputs", [])]
        if name not in existing:
            created = self.client.request("CreateInput", {
                "sceneName": SCENE_NAME, "inputName": name, "inputKind": kind,
                "inputSettings": settings, "sceneItemEnabled": True})
            return int(created["sceneItemId"])
        try:
            return int(self.client.request("GetSceneItemId", {
                "sceneName": SCENE_NAME, "sourceName": name})["sceneItemId"])
        except ObsRequestFailed as exc:
            if exc.code != CODE_NOT_FOUND:
                raise
            return int(self.client.request("CreateSceneItem", {
                "sceneName": SCENE_NAME, "sourceName": name,
                "sceneItemEnabled": True})["sceneItemId"])

    def _select_monitor(self) -> None:
        """يحدّد شاشة حقيقية: مصدر الشاشة بلا ``monitor_id`` يلتقط شاشة وهمية."""
        try:
            items = self.client.request("GetInputPropertiesListPropertyItems", {
                "inputName": INPUT_SCREEN, "propertyName": "monitor_id"}
            ).get("propertyItems", [])
        except ObsRequestFailed:
            return  # يبقى الافتراضي؛ مصدر display_capture القديم لا يحتاجه
        usable = [i for i in items if i.get("itemValue")]
        if not usable:
            raise RecordingControlError("لم يجد OBS أي شاشة لالتقاطها.")
        chosen = next((i for i in usable
                       if "primary" in str(i.get("itemName", "")).lower()
                       or "رئيس" in str(i.get("itemName", ""))), usable[0])
        self.client.request("SetInputSettings", {
            "inputName": INPUT_SCREEN, "overlay": True,
            "inputSettings": {"monitor_id": chosen["itemValue"]}})

    def _fit_screen(self, screen_item_id: int) -> None:
        """الشاشة تملأ اللوحة بلا اقتصاص مهما اختلف حجم المصدر."""
        video = self.client.request("GetVideoSettings")
        base_w = int(video.get("baseWidth") or 1920)
        base_h = int(video.get("baseHeight") or 1080)
        self.client.request("SetSceneItemTransform", {
            "sceneName": SCENE_NAME, "sceneItemId": screen_item_id,
            "sceneItemTransform": {
                "positionX": 0.0, "positionY": 0.0, "alignment": 5,
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
                "boundsWidth": float(base_w), "boundsHeight": float(base_h)}})

    def _verify_output_profile(self) -> None:
        """إعدادات الإخراج تُكتب قبل الإقلاع؛ هنا نتأكد فقط أن OBS حمّلها."""
        try:
            got = self.client.request("GetProfileParameter", {
                "parameterCategory": "SimpleOutput",
                "parameterName": "RecQuality"}).get("parameterValue")
        except ObsRequestFailed:
            return
        if got not in (None, "Small"):
            raise RecordingControlError(
                "لم يحمّل OBS ملف التسجيل المعدّ مسبقًا (RecQuality).")

    # -- التسجيل ---------------------------------------------------
    def start(self, timeout: float = 15.0) -> None:
        self.client.request("StartRecord")
        self._wait(lambda: bool(self.status().get("outputActive")),
                   "لم يبدأ OBS التسجيل.", timeout)

    def status(self) -> dict:
        return self.client.request("GetRecordStatus")

    def stop(self, timeout: float = 30.0) -> Optional[str]:
        """يوقف التسجيل ويعيد مسار الملف كما يراه OBS."""
        result = self.client.request("StopRecord")
        self._wait(lambda: not self.status().get("outputActive"),
                   "لم يُنهِ OBS كتابة الملف في المهلة.", timeout)
        return result.get("outputPath")

    # -- مساعد -----------------------------------------------------
    def _wait(self, condition: Callable[[], bool], message: str,
              timeout: float = 8.0) -> None:
        deadline = self._clock() + timeout
        while True:
            try:
                if condition():
                    return
            except ObsRequestFailed:
                pass  # OBS يعيد تحميل الحالة بعد تبديل الملف الشخصي
            if self._clock() >= deadline:
                raise RecordingControlError(message)
            self._sleep(0.4)


def _even(value: float) -> int:
    value = int(round(value))
    return max(value - (value % 2), 2)
