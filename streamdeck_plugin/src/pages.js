// Which deck shows which page for an OBS scene. Pure functions, no SDK —
// tested by test_plugin.mjs.
//
// Settings model (what the settings window edits and what is stored in the
// Stream Deck app's global settings):
//   {
//     version: 1,
//     obs:   { host, port, password },
//     decks: { "<deck name>": "<bundled profile>" },       // decks to switch
//     pages: { "<OBS scene>": { "<deck name>": <page> } }   // page 1-based
//   }
// config.json (the older, file-based format with ONE page per scene for all
// decks) is converted into it by fromConfig().

const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, "");

export const DEFAULT_CONFIG = {
	obs: { host: "127.0.0.1", port: 4455, password: "" },
	// deck name (as set in the Stream Deck app) -> profile bundled with the plugin
	decks: { XLlinks: "XLlinks", XLrechts: "XLrechts" },
	// OBS scene name -> page number (1 = first page), same page on every deck
	scenes: { Intro: 1, Moderator: 2, iRacing: 3, Werbung: 4, "Schedule PCC": 5 },
};

const MAX_PAGE = 99;

function cleanObs(raw = {}) {
	return {
		host: String(raw.host || DEFAULT_CONFIG.obs.host),
		port: Number(raw.port) || DEFAULT_CONFIG.obs.port,
		password: String(raw.password || ""),
	};
}

function cleanPage(v) {
	const n = Math.round(Number(v));
	return Number.isFinite(n) && n >= 1 && n <= MAX_PAGE ? n : null;
}

/** config.json text -> settings model. */
export function parseConfig(text) {
	const raw = text ? JSON.parse(text) : {};
	return fromConfig({
		obs: raw.obs,
		decks: raw.decks || DEFAULT_CONFIG.decks,
		scenes: raw.scenes || DEFAULT_CONFIG.scenes,
	});
}

/** File format (one page per scene, all decks) -> settings model. */
export function fromConfig(cfg) {
	const decks = { ...(cfg.decks || {}) };
	const pages = {};
	for (const [scene, page] of Object.entries(cfg.scenes || {})) {
		const p = cleanPage(page);
		if (!p) continue;
		pages[scene] = {};
		for (const deck of Object.keys(decks)) pages[scene][deck] = p;
	}
	return { version: 1, obs: cleanObs(cfg.obs), decks, pages };
}

/**
 * Sanitises settings coming from the settings window / the app's storage.
 * Returns null for "nothing stored" (-> use config.json).
 */
export function normalizeSettings(raw) {
	if (!raw || typeof raw !== "object" || raw.version !== 1) return null;
	const decks = {};
	for (const [name, profile] of Object.entries(raw.decks || {})) {
		if (name && profile) decks[String(name)] = String(profile);
	}
	const pages = {};
	for (const [scene, perDeck] of Object.entries(raw.pages || {})) {
		if (!scene || !perDeck || typeof perDeck !== "object") continue;
		const row = {};
		for (const [deck, page] of Object.entries(perDeck)) {
			const p = cleanPage(page);
			if (p) row[String(deck)] = p;
		}
		pages[String(scene)] = row; // an empty row keeps the scene listed
	}
	return { version: 1, obs: cleanObs(raw.obs), decks, pages };
}

/** Key of `obj` matching `name`: exact first, then ignoring case and spaces. */
function findKey(obj, name) {
	if (Object.prototype.hasOwnProperty.call(obj, name)) return name;
	return Object.keys(obj).find((k) => norm(k) === norm(name)) ?? null;
}

/** Profile for a deck name, or null (= this deck is never switched). */
export function profileForDeck(cfg, deckName) {
	const k = findKey(cfg.decks, deckName);
	return k === null ? null : cfg.decks[k] || null;
}

/** Page (1-based) a deck shows for a scene, or null (= deck stays). */
export function pageFor(cfg, scene, deckName) {
	const sk = findKey(cfg.pages, scene);
	if (sk === null) return null;
	const row = cfg.pages[sk] || {};
	const dk = findKey(row, deckName);
	return dk === null ? null : cleanPage(row[dk]);
}

/**
 * Switches to make for a scene: [{ deviceId, deckName, profile, page }]
 * with page 0-based, as switchToProfile wants it. Unknown scene or no page
 * for a deck -> that deck stays where it is.
 * devices: [{ id, name, isConnected }]
 */
export function targetsForScene(cfg, scene, devices) {
	const out = [];
	for (const d of devices) {
		if (d.isConnected === false) continue;
		const profile = profileForDeck(cfg, d.name);
		if (!profile) continue;
		const page = pageFor(cfg, scene, d.name);
		if (page) out.push({ deviceId: d.id, deckName: d.name, profile, page: page - 1 });
	}
	return out;
}
