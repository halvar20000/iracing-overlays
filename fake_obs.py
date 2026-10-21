"""
Fake OBS (obs-websocket v5) for the tests — a tiny in-process server with
scenes, scene items, media inputs, stream and record status. Not used at runtime.
Needs the `websockets` package (already in requirements.txt for racecontrol).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from obs_ws import auth_string


class FakeObs:
    def __init__(self, scenes: dict, media: dict | None = None, password: str = "", port: int = 0):
        # scenes: {scene: {source: enabled}}, media: {source: duration_s}
        self.scenes = {k: dict(v) for k, v in scenes.items()}
        self.ids = {}
        n = 1
        for sc, items in self.scenes.items():
            for src in items:
                self.ids[(sc, src)] = n
                n += 1
        self.media = {k: {"duration": d, "started": None} for k, d in (media or {}).items()}
        self.current = next(iter(self.scenes))
        self.streaming = False
        self.recording = False       # StartRecord / PauseRecord / ResumeRecord / StopRecord
        self.rec_paused = False
        self.rec_segments = []       # [[start, end]] wall-clock spans actually recorded
        self.rec_path = "C:/Videos/fake_recording.mp4"
        self.password = password
        self.calls = []          # (requestType, requestData)
        self.port = port
        self._loop = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)

    # -- server ---------------------------------------------------------------
    def _run(self):
        import websockets
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def main():
            async with websockets.serve(self._handler, "127.0.0.1", self.port) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self._ready.set()
                await asyncio.Future()
        self._loop.run_until_complete(main())

    async def _handler(self, ws):
        try:
            await self._serve(ws)
        except Exception:            # client went away (wrong / missing password tests)
            pass

    async def _serve(self, ws):
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"challenge": "chal", "salt": "salt"}
        await ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(await ws.recv())
        if self.password and ident["d"].get("authentication") != auth_string(self.password, "salt", "chal"):
            await ws.close(4009, "Authentication failed")
            return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        async for raw in ws:
            m = json.loads(raw)
            if m.get("op") != 6:
                continue
            d = m["d"]
            ok, data, comment = self.handle(d["requestType"], d.get("requestData") or {})
            await ws.send(json.dumps({"op": 7, "d": {
                "requestType": d["requestType"], "requestId": d["requestId"],
                "requestStatus": {"result": ok, "code": 100 if ok else 600, "comment": comment},
                "responseData": data}}))

    # -- requests ---------------------------------------------------------------
    def handle(self, rt, rd):
        self.calls.append((rt, rd))
        if rt == "GetSceneList":
            return True, {"currentProgramSceneName": self.current,
                          "scenes": [{"sceneName": s} for s in self.scenes]}, None
        if rt == "GetCurrentProgramScene":
            return True, {"currentProgramSceneName": self.current}, None
        if rt == "SetCurrentProgramScene":
            if rd["sceneName"] not in self.scenes:
                return False, None, f"No source was found by the name of `{rd['sceneName']}`."
            self.current = rd["sceneName"]
            return True, None, None
        if rt == "GetSceneItemList":
            sc = self.scenes.get(rd["sceneName"])
            if sc is None:
                return False, None, "No scene"
            return True, {"sceneItems": [{"sourceName": s, "sceneItemId": self.ids[(rd["sceneName"], s)],
                                          "sceneItemEnabled": e} for s, e in sc.items()]}, None
        if rt == "GetSceneItemId":
            key = (rd["sceneName"], rd["sourceName"])
            if key not in self.ids:
                return False, None, f"No scene items were found in the specified scene by that name or offset."
            return True, {"sceneItemId": self.ids[key]}, None
        if rt == "SetSceneItemEnabled":
            for (sc, src), i in self.ids.items():
                if sc == rd["sceneName"] and i == rd["sceneItemId"]:
                    self.scenes[sc][src] = bool(rd["sceneItemEnabled"])
                    return True, None, None
            return False, None, "No item"
        if rt == "TriggerMediaInputAction":
            m = self.media.get(rd["inputName"])
            if m is None:
                return False, None, "No input"
            m["started"] = time.time() if rd["mediaAction"].endswith("RESTART") else None
            return True, None, None
        if rt == "GetMediaInputStatus":
            m = self.media.get(rd["inputName"])
            if m is None:
                return False, None, "No input"
            if m["started"] is None:
                return True, {"mediaState": "OBS_MEDIA_STATE_STOPPED", "mediaDuration": int(m["duration"] * 1000),
                              "mediaCursor": 0}, None
            el = time.time() - m["started"]
            state = "OBS_MEDIA_STATE_ENDED" if el >= m["duration"] else "OBS_MEDIA_STATE_PLAYING"
            return True, {"mediaState": state, "mediaDuration": int(m["duration"] * 1000),
                          "mediaCursor": int(min(el, m["duration"]) * 1000)}, None
        if rt == "SetInputSettings":
            m = self.media.get(rd["inputName"])
            if m is None:
                return False, None, "No input"
            m["file"] = (rd.get("inputSettings") or {}).get("local_file")
            return True, None, None
        if rt == "GetStreamStatus":
            return True, {"outputActive": self.streaming}, None
        if rt == "GetRecordStatus":
            return True, {"outputActive": self.recording, "outputPaused": self.rec_paused}, None
        if rt == "StartRecord":
            if self.recording:
                return False, None, "Output already active"
            self.recording, self.rec_paused = True, False
            self.rec_segments.append([time.time(), None])
            return True, None, None
        if rt == "PauseRecord":
            if not self.recording or self.rec_paused:
                return False, None, "Not recording or already paused"
            self.rec_paused = True
            self.rec_segments[-1][1] = time.time()
            return True, None, None
        if rt == "ResumeRecord":
            if not self.recording or not self.rec_paused:
                return False, None, "Not paused"
            self.rec_paused = False
            self.rec_segments.append([time.time(), None])
            return True, None, None
        if rt == "StopRecord":
            if not self.recording:
                return False, None, "Output not active"
            if not self.rec_paused:
                self.rec_segments[-1][1] = time.time()
            self.recording = self.rec_paused = False
            return True, {"outputPath": self.rec_path}, None
        return False, None, f"unknown request {rt}"
