"""بدائل OBS للاختبار: خادم websocket مزيّف وحالة OBS مزيّفة.

لا يوجد OBS في بيئة الاختبار (ولا ويندوز)، فنختبر ما يخصّنا: البروتوكول
وتسلسل الطلبات وتدبير الحالة، لا OBS نفسه.
"""
from __future__ import annotations

import json
from pathlib import Path

from recording.obs_controller import ObsRequestFailed, compute_auth


class FakeSocket:
    """مقبس websocket مزيّف يؤدي دور خادم obs-websocket (مع توثيق)."""

    def __init__(self, password="secret", handlers=None, require_auth=True):
        self.password = password
        self.handlers = handlers or {}
        self.require_auth = require_auth
        self.salt, self.challenge = "pepper", "c-h-a-l"
        self.outbox: list[str] = []
        self.closed = False
        self.identified = False
        self.requests: list[tuple[str, dict]] = []
        hello = {"obsWebSocketVersion": "5.6.0", "rpcVersion": 1}
        if require_auth:
            hello["authentication"] = {"challenge": self.challenge,
                                       "salt": self.salt}
        self.outbox.append(json.dumps({"op": 0, "d": hello}))

    def send(self, text: str) -> None:
        msg = json.loads(text)
        if msg["op"] == 1:
            expected = compute_auth(self.password, self.salt, self.challenge)
            if self.require_auth and msg["d"].get("authentication") != expected:
                self.closed = True
                return          # OBS يغلق الاتصال عند فشل التوثيق
            self.identified = True
            self.outbox.append(json.dumps(
                {"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        elif msg["op"] == 6:
            d = msg["d"]
            self.requests.append((d["requestType"], d.get("requestData", {})))
            handler = self.handlers.get(d["requestType"])
            if handler is None:
                status = {"result": False, "code": 204, "comment": "unknown"}
                body = {}
            else:
                body = handler(d.get("requestData", {}))
                status = {"result": True, "code": 100}
                if body is False:
                    status, body = {"result": False, "code": 600,
                                    "comment": "not found"}, {}
            reply = {"requestType": d["requestType"],
                     "requestId": d["requestId"], "requestStatus": status}
            if body:
                reply["responseData"] = body
            self.outbox.append(json.dumps({"op": 7, "d": reply}))

    def recv(self) -> str:
        if self.closed or not self.outbox:
            raise ConnectionError("closed")
        return self.outbox.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeObsClient:
    """يحاكي حالة OBS كما يراها ``ObsRecorder`` (بلا بروتوكول)."""

    def __init__(self, source=(2560, 1440), stop_path=None, fail_stop=False):
        self.profiles = ["Untitled"]
        self.profile = "Untitled"
        self.collections = ["Untitled"]
        self.collection = "Untitled"
        self.scenes: list[str] = []
        self.inputs: dict[str, dict] = {}     # name -> {kind, muted, tracks}
        self.scene_items: dict[str, int] = {}
        self.next_item = 1
        self.params: dict[tuple[str, str], str] = {}
        self.video: dict = {"baseWidth": 1920, "baseHeight": 1080}
        self.transform: dict = {}
        self.monitors = [
            {"itemName": "Display 2", "itemValue": "\\\\?\\DISPLAY2"},
            {"itemName": "Display 1 (Primary)", "itemValue": "\\\\?\\DISPLAY1"}]
        self.source = source
        self.recording = False
        self.stop_path = stop_path
        self.fail_stop = fail_stop
        self.log: list[str] = []
        self.closed = False

    # واجهة ObsWebSocket
    def open(self): self.log.append("open")

    def close(self): self.closed = True

    def request(self, kind, data=None):
        data = data or {}
        self.log.append(kind)
        return getattr(self, "_" + kind)(data)

    # --- الطلبات ---
    def _GetProfileList(self, d):
        return {"currentProfileName": self.profile, "profiles": self.profiles}

    def _CreateProfile(self, d):
        self.profiles.append(d["profileName"]); self.profile = d["profileName"]
        return {}

    def _SetCurrentProfile(self, d):
        self.profile = d["profileName"]; return {}

    def _GetSceneCollectionList(self, d):
        return {"currentSceneCollectionName": self.collection,
                "sceneCollections": self.collections}

    def _CreateSceneCollection(self, d):
        self.collections.append(d["sceneCollectionName"])
        self.collection = d["sceneCollectionName"]; return {}

    def _SetCurrentSceneCollection(self, d):
        self.collection = d["sceneCollectionName"]; return {}

    def _GetSceneList(self, d):
        return {"scenes": [{"sceneName": s} for s in self.scenes]}

    def _CreateScene(self, d):
        self.scenes.append(d["sceneName"]); return {}

    def _SetCurrentProgramScene(self, d): return {}

    def _GetInputKindList(self, d):
        return {"inputKinds": ["monitor_capture", "wasapi_output_capture",
                               "wasapi_input_capture"]}

    def _GetInputList(self, d):
        return {"inputs": [{"inputName": n} for n in self.inputs]}

    def _CreateInput(self, d):
        self.inputs[d["inputName"]] = {"kind": d["inputKind"], "muted": False,
                                       "tracks": None}
        item = self.next_item; self.next_item += 1
        self.scene_items[d["inputName"]] = item
        return {"sceneItemId": item}

    def _GetSceneItemId(self, d):
        if d["sourceName"] not in self.scene_items:
            raise ObsRequestFailed("GetSceneItemId", 600)
        return {"sceneItemId": self.scene_items[d["sourceName"]]}

    def _CreateSceneItem(self, d):
        item = self.next_item; self.next_item += 1
        self.scene_items[d["sourceName"]] = item
        return {"sceneItemId": item}

    def _SetInputAudioTracks(self, d):
        self.inputs[d["inputName"]]["tracks"] = d["inputAudioTracks"]; return {}

    def _SetInputMute(self, d):
        self.inputs[d["inputName"]]["muted"] = d["inputMuted"]; return {}

    def _GetSceneItemTransform(self, d):
        return {"sceneItemTransform": {"sourceWidth": self.source[0],
                                       "sourceHeight": self.source[1]}}

    def _GetVideoSettings(self, d): return dict(self.video)

    def _GetInputPropertiesListPropertyItems(self, d):
        return {"propertyItems": list(self.monitors)}

    def _SetInputSettings(self, d):
        self.inputs[d["inputName"]].setdefault("settings", {}).update(
            d["inputSettings"]); return {}

    def _GetProfileParameter(self, d):
        return {"parameterValue": self.params.get(
            (d["parameterCategory"], d["parameterName"]), "Small")}

    def _SetVideoSettings(self, d):
        self.video = {**self.video, **d}; return {}

    def _SetSceneItemTransform(self, d):
        self.transform = d["sceneItemTransform"]; return {}

    def _SetProfileParameter(self, d):
        self.params[(d["parameterCategory"], d["parameterName"])] = \
            d["parameterValue"]
        return {}

    def _SetRecordDirectory(self, d):
        raise ObsRequestFailed("SetRecordDirectory", 204)

    def _StartRecord(self, d):
        self.recording = True; return {}

    def _GetRecordStatus(self, d): return {"outputActive": self.recording}

    def _StopRecord(self, d):
        if self.fail_stop:
            from core.exceptions import RecordingControlError
            raise RecordingControlError("انقطع")
        self.recording = False
        if self.stop_path:
            Path(self.stop_path).write_bytes(b"mkv")
        return {"outputPath": str(self.stop_path) if self.stop_path else None}


class FakeProcess:
    """بديل ``ObsProcess``."""

    def __init__(self, exe, args, alive=True):
        self.exe, self.args = exe, args
        self._alive = alive
        self.started = self.closed = False

    def start(self): self.started = True

    def alive(self): return self._alive and not self.closed

    def close(self, grace=8.0): self.closed = True
