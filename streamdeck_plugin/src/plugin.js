// OBS Seitenwechsel — when the OBS program scene changes, the decks jump to
// the page that belongs to that scene. Only profiles bundled with this
// plugin can be switched to (Stream Deck rule), hence XLlinks / XLrechts
// ship inside the plugin.
//
// Settings: edited in the settings window of the "OBS-Status" key (ui/
// settings.html) and kept in the Stream Deck app's GLOBAL settings — they
// survive plugin updates. Nothing saved there yet -> config.json next to the
// manifest (the original file-based setup).
import { readFileSync } from "node:fs";
import streamDeck, { SingletonAction } from "@elgato/streamdeck";
import { ObsClient } from "./obs.js";
import { normalizeSettings, parseConfig, targetsForScene } from "./pages.js";

const log = streamDeck.logger;

function readJson(rel) {
	return readFileSync(new URL(rel, import.meta.url), "utf8");
}

function loadFileConfig() {
	try {
		return parseConfig(readJson("../config.json"));
	} catch (err) {
		log.error(`config.json unreadable (${err.message}) - using defaults`);
		return parseConfig("");
	}
}

// Profiles bundled in the manifest: [{ name, deviceType }]
function bundledProfiles() {
	try {
		return (JSON.parse(readJson("../manifest.json")).Profiles || []).map((p) => ({ name: p.Name, deviceType: p.DeviceType }));
	} catch {
		return [];
	}
}

const fileCfg = loadFileConfig();
const profiles = bundledProfiles();
let cfg = fileCfg;
let source = "config"; // "config" = config.json, "app" = saved in the settings window
const obs = new ObsClient(cfg.obs);

// Every deck Stream Deck told us about. Deliberately NOT filtered on
// isConnected: the SDK marks the decks from the registration info as
// disconnected until a separate deviceDidConnect arrives, and a switch sent
// to an unplugged deck is simply ignored.
function deviceList() {
	const out = [];
	streamDeck.devices.forEach((d) => out.push({ id: d.id, name: d.name, type: d.type, connected: d.isConnected }));
	return out;
}

async function applyScene(scene, onlyDeviceId = null) {
	const targets = targetsForScene(cfg, scene, deviceList()).filter((t) => !onlyDeviceId || t.deviceId === onlyDeviceId);
	if (!targets.length) {
		log.info(`scene "${scene}": no page configured / no matching deck - nothing switched`);
		return;
	}
	for (const t of targets) {
		try {
			await streamDeck.profiles.switchToProfile(t.deviceId, t.profile, t.page);
			log.info(`scene "${scene}" -> ${t.deckName}: profile ${t.profile}, page ${t.page + 1}`);
		} catch (err) {
			log.error(`switch ${t.deckName} failed: ${err.message}`);
		}
	}
}

function useSettings(raw) {
	const s = normalizeSettings(raw);
	cfg = s || fileCfg;
	source = s ? "app" : "config";
	if (obs.configure(cfg.obs)) log.info(`OBS address changed -> ${obs.url}`);
	log.info(`settings from ${source === "app" ? "settings window" : "config.json"}: decks ${JSON.stringify(cfg.decks)}`);
}

// ---------------------------------------------------- settings window (PI)
function uiState() {
	return {
		type: "state",
		source,
		settings: cfg,
		profiles,
		decks: deviceList(),
		obs: { connected: obs.connected, scene: obs.scene, scenes: obs.scenes, url: obs.url, error: obs.connected ? null : obs.lastError || null },
	};
}

function pushState() {
	streamDeck.ui.sendToPropertyInspector(uiState()).catch(() => {});
}

streamDeck.ui.onSendToPlugin(async (ev) => {
	const msg = ev.payload || {};
	if (msg.type === "save") {
		const s = normalizeSettings({ ...msg.settings, version: 1 });
		if (!s) return;
		await streamDeck.settings.setGlobalSettings(s);
		useSettings(s);
	} else if (msg.type === "reset") {
		await streamDeck.settings.setGlobalSettings({});
		useSettings(null);
	} else if (msg.type === "apply") {
		if (obs.connected && obs.scene) await applyScene(obs.scene);
	}
	pushState();
});
streamDeck.ui.onDidAppear(() => setTimeout(pushState, 50));

// ------------------------------------------------------- OBS-Status key
// Shows OBS connection + current scene; pressing it re-applies the pages for
// the current scene (after browsing by hand). Its settings window is where
// everything is configured.
class StatusAction extends SingletonAction {
	manifestId = "com.halvar.obspages.status";

	title() {
		if (!obs.connected) return "OBS\naus";
		return `OBS\n${obs.scene || "?"}`;
	}

	async refresh() {
		for (const a of this.actions) await a.setTitle(this.title());
	}

	async onWillAppear(ev) {
		await ev.action.setTitle(this.title());
	}

	async onKeyDown(ev) {
		if (obs.connected && obs.scene) await applyScene(obs.scene);
		else await ev.action.showAlert();
	}
}

const status = new StatusAction();
streamDeck.actions.registerAction(status);

obs.on("scene", (scene) => {
	applyScene(scene);
	status.refresh();
	pushState();
});
obs.on("scenes", pushState);
obs.on("status", ({ connected, error }) => {
	log.info(connected ? `OBS connected (${obs.url})` : `OBS not connected${error ? ": " + error : ""}`);
	status.refresh();
	pushState();
});

// A deck plugged in later gets the page of the scene that is on air now.
streamDeck.devices.onDeviceDidConnect((ev) => {
	if (obs.connected && obs.scene) applyScene(obs.scene, ev.device.id);
	pushState();
});
streamDeck.devices.onDeviceDidDisconnect(pushState);

await streamDeck.connect();
log.info(`decks seen: ${deviceList().map((d) => `"${d.name}"`).join(", ") || "none"}`);
try {
	const timeout = new Promise((_, no) => setTimeout(() => no(new Error("no answer")), 3000));
	useSettings(await Promise.race([streamDeck.settings.getGlobalSettings(), timeout]));
} catch (err) {
	log.error(`global settings unreadable (${err.message}) - using config.json`);
}
obs.start();
