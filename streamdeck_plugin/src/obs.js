// Minimal obs-websocket v5 client: connects, authenticates if OBS asks
// for it, reports the current program scene and every scene change.
// Also keeps the list of scenes (for the settings window). Reconnects on
// its own (OBS started later, OBS restarted, ...).
//
// Events: "scene" (name), "scenes" ([names]), "status" ({ connected, error })
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import WebSocket from "ws";

const OP_HELLO = 0;
const OP_IDENTIFY = 1;
const OP_IDENTIFIED = 2;
const OP_EVENT = 5;
const OP_REQUEST = 6;
const OP_REQUEST_RESPONSE = 7;
const SUB_SCENES = 1 << 2; // EventSubscription.Scenes
const LIST_EVENTS = new Set(["SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged"]);

const RETRY_MIN_MS = 2000;
const RETRY_MAX_MS = 10000;

export function authString(password, salt, challenge) {
	const sha = (s) => createHash("sha256").update(s).digest("base64");
	return sha(sha(password + salt) + challenge);
}

export class ObsClient extends EventEmitter {
	constructor({ host = "127.0.0.1", port = 4455, password = "" } = {}) {
		super();
		this.url = `ws://${host}:${port}`;
		this.password = password || "";
		this.ws = null;
		this.connected = false;
		this.scene = null;
		this.scenes = null; // null = not known (OBS not connected yet)
		this.retryMs = RETRY_MIN_MS;
		this.timer = null;
		this.stopped = false;
	}

	start() {
		this.stopped = false;
		this._open();
	}

	stop() {
		this.stopped = true;
		clearTimeout(this.timer);
		if (this.ws) this.ws.terminate();
	}

	/** New address / password (settings window): reconnect right away. */
	configure({ host = "127.0.0.1", port = 4455, password = "" } = {}) {
		const url = `ws://${host}:${port}`;
		if (url === this.url && (password || "") === this.password) return false;
		this.url = url;
		this.password = password || "";
		if (this.stopped || !this.ws) return true;
		const old = this.ws;
		this.ws = null;
		old.terminate();
		this.lastError = null;
		if (this.connected) {
			this.connected = false;
			this.emit("status", { connected: false, error: null });
		}
		this.retryMs = RETRY_MIN_MS;
		this._open();
		return true;
	}

	_open() {
		clearTimeout(this.timer);
		const ws = new WebSocket(this.url, "obswebsocket.json");
		this.ws = ws;
		ws.on("message", (raw) => this.ws === ws && this._onMessage(raw));
		ws.on("close", () => this.ws === ws && this._onClose());
		ws.on("error", (err) => {
			this.lastError = err.message;
		});
	}

	_onClose() {
		const was = this.connected;
		this.connected = false;
		this.scenes = null;
		if (was || this.lastError) this.emit("status", { connected: false, error: this.lastError || null });
		if (this.stopped) return;
		this.timer = setTimeout(() => this._open(), this.retryMs);
		this.retryMs = Math.min(this.retryMs * 2, RETRY_MAX_MS);
	}

	_send(op, d) {
		if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ op, d }));
	}

	_onMessage(raw) {
		let msg;
		try {
			msg = JSON.parse(raw.toString());
		} catch {
			return;
		}
		const d = msg.d || {};
		if (msg.op === OP_HELLO) {
			const identify = { rpcVersion: 1, eventSubscriptions: SUB_SCENES };
			if (d.authentication) {
				identify.authentication = authString(this.password, d.authentication.salt, d.authentication.challenge);
			}
			this._send(OP_IDENTIFY, identify);
		} else if (msg.op === OP_IDENTIFIED) {
			this.connected = true;
			this.lastError = null;
			this.retryMs = RETRY_MIN_MS;
			this.emit("status", { connected: true, error: null });
			this._send(OP_REQUEST, { requestType: "GetCurrentProgramScene", requestId: "current-scene" });
			this._send(OP_REQUEST, { requestType: "GetSceneList", requestId: "scene-list" });
		} else if (msg.op === OP_REQUEST_RESPONSE && d.requestId === "current-scene") {
			const r = d.responseData || {};
			this._setScene(r.currentProgramSceneName || r.sceneName);
		} else if (msg.op === OP_REQUEST_RESPONSE && d.requestId === "scene-list") {
			this._setSceneList((d.responseData || {}).scenes);
		} else if (msg.op === OP_EVENT && d.eventType === "CurrentProgramSceneChanged") {
			this._setScene((d.eventData || {}).sceneName);
		} else if (msg.op === OP_EVENT && LIST_EVENTS.has(d.eventType)) {
			this._send(OP_REQUEST, { requestType: "GetSceneList", requestId: "scene-list" });
		}
	}

	// obs-websocket lists scenes bottom-up (index 0 = last in the OBS dock);
	// sorted so the settings window shows them in the order of the dock.
	_setSceneList(list) {
		if (!Array.isArray(list)) return;
		this.scenes = [...list]
			.sort((a, b) => (b.sceneIndex ?? 0) - (a.sceneIndex ?? 0))
			.map((s) => s.sceneName)
			.filter(Boolean);
		this.emit("scenes", this.scenes);
	}

	_setScene(name) {
		if (!name) return;
		this.scene = name;
		this.emit("scene", name);
	}
}
