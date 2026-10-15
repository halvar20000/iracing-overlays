"""
Minimal obs-websocket v5 client (OBS 28+, Tools → WebSocket Server Settings,
default port 4455). Synchronous, thread-safe, reconnects on the next call.
Only what the show runner needs: scenes, scene-item visibility, media inputs,
stream status.

Uses the `websocket-client` package (pip install websocket-client).
Protocol: Hello (op 0) → Identify (op 1, with the auth string when OBS asks
for a password) → Identified (op 2); Request (op 6) → RequestResponse (op 7).
Tested by test_obs_ws.py against a fake OBS server.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import threading


class ObsError(RuntimeError):
    pass


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    def __init__(self, host="127.0.0.1", port=4455, password="", timeout=3.0):
        self.host, self.port, self.password, self.timeout = host, int(port), password or "", timeout
        self._ws = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.last_error = ""

    # -- connection -----------------------------------------------------------
    def _connect(self):
        import websocket                      # websocket-client
        ws = websocket.create_connection(f"ws://{self.host}:{self.port}", timeout=self.timeout)
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            ws.close()
            raise ObsError("unexpected first message from OBS")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = (hello.get("d") or {}).get("authentication")
        if auth:
            if not self.password:
                ws.close()
                raise ObsError("OBS verlangt ein Passwort (show_config.json obs.password)")
            ident["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        ws.send(json.dumps({"op": 1, "d": ident}))
        msg = json.loads(ws.recv())
        if msg.get("op") != 2:
            ws.close()
            raise ObsError("OBS hat die Anmeldung abgelehnt (Passwort?)")
        self._ws = ws

    def close(self):
        with self._lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def call(self, request_type: str, data: dict | None = None) -> dict:
        """One request -> responseData (dict). Raises ObsError."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._ws is None:
                        self._connect()
                    rid = str(next(self._ids))
                    self._ws.send(json.dumps({"op": 6, "d": {"requestType": request_type,
                                                            "requestId": rid,
                                                            "requestData": data or {}}}))
                    while True:
                        msg = json.loads(self._ws.recv())
                        if msg.get("op") == 7 and (msg.get("d") or {}).get("requestId") == rid:
                            break
                    d = msg["d"]
                    st = d.get("requestStatus") or {}
                    if not st.get("result"):
                        raise ObsError(f"{request_type}: {st.get('comment') or st.get('code')}")
                    self.last_error = ""
                    return d.get("responseData") or {}
                except ObsError as e:
                    self.last_error = str(e)
                    raise
                except Exception as e:            # socket gone / OBS closed -> reconnect once
                    try:
                        if self._ws is not None:
                            self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                    self.last_error = f"OBS nicht erreichbar: {e}"
                    if attempt == 2:
                        raise ObsError(self.last_error) from None
        raise ObsError("unreachable")

    # -- helpers ----------------------------------------------------------------
    def scenes(self) -> list[str]:
        return [s.get("sceneName") for s in self.call("GetSceneList").get("scenes", [])]

    def current_scene(self) -> str:
        return self.call("GetCurrentProgramScene").get("currentProgramSceneName") or ""

    def set_scene(self, name: str):
        self.call("SetCurrentProgramScene", {"sceneName": name})

    def scene_items(self, scene: str) -> dict:
        """{source name: (item id, enabled)}"""
        items = self.call("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
        return {i.get("sourceName"): (i.get("sceneItemId"), bool(i.get("sceneItemEnabled"))) for i in items}

    def set_visible(self, scene: str, source: str, visible: bool):
        item = self.call("GetSceneItemId", {"sceneName": scene, "sourceName": source})["sceneItemId"]
        self.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item,
                                          "sceneItemEnabled": bool(visible)})

    def media_restart(self, source: str):
        self.call("TriggerMediaInputAction", {"inputName": source,
                                              "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"})

    def media_stop(self, source: str):
        self.call("TriggerMediaInputAction", {"inputName": source,
                                              "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"})

    def media_status(self, source: str) -> dict:
        """{state: OBS_MEDIA_STATE_…, duration_s, cursor_s}"""
        d = self.call("GetMediaInputStatus", {"inputName": source})
        dur, cur = d.get("mediaDuration"), d.get("mediaCursor")
        return {"state": d.get("mediaState") or "",
                "duration_s": dur / 1000.0 if dur else None,
                "cursor_s": cur / 1000.0 if cur is not None else None}

    def streaming(self) -> bool:
        return bool(self.call("GetStreamStatus").get("outputActive"))
