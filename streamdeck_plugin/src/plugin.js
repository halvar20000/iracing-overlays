// OBS Seitenwechsel — when the OBS program scene changes, the XL decks
// jump to the page that belongs to that scene (config.json next to the
// manifest). Only profiles bundled with this plugin can be switched to
// (Stream Deck rule), hence XLlinks / XLrechts ship inside the plugin.
import { readFileSync } from "node:fs";
import streamDeck, { SingletonAction } from "@elgato/streamdeck";
import { ObsClient } from "./obs.js";
import { parseConfig, targetsForScene } from "./pages.js";

const log = streamDeck.logger;

function loadConfig() {
	try {
		return parseConfig(readFileSync(new URL("../config.json", import.meta.url), "utf8"));
	} catch (err) {
		log.error(`config.json unreadable (${err.message}) - using defaults`);
		return parseConfig("");
	}
}

const cfg = loadConfig();
const obs = new ObsClient(cfg.obs);

// Every deck Stream Deck told us about. Deliberately NOT filtered on
// isConnected: the SDK marks the decks from the registration info as
// disconnected until a separate deviceDidConnect arrives, and a switch sent
// to an unplugged deck is simply ignored.
function deviceList() {
	const out = [];
	streamDeck.devices.forEach((d) => out.push({ id: d.id, name: d.name }));
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

// Optional key: shows OBS connection + current scene; pressing it
// re-applies the pages for the current scene (after browsing by hand).
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
});
obs.on("status", ({ connected, error }) => {
	log.info(connected ? `OBS connected (${obs.url})` : `OBS not connected${error ? ": " + error : ""}`);
	status.refresh();
});

// A deck plugged in later gets the page of the scene that is on air now.
streamDeck.devices.onDeviceDidConnect((ev) => {
	if (obs.connected && obs.scene) applyScene(obs.scene, ev.device.id);
});

await streamDeck.connect();
log.info(`decks seen: ${deviceList().map((d) => `"${d.name}"`).join(", ") || "none"}`);
obs.start();
