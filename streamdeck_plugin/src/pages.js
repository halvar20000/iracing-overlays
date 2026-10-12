// Which deck shows which page for an OBS scene. Pure functions, no SDK —
// tested by test_plugin.mjs.

const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, "");

export const DEFAULT_CONFIG = {
	obs: { host: "127.0.0.1", port: 4455, password: "" },
	// deck name (as set in the Stream Deck app) -> profile bundled with the plugin
	decks: { XLlinks: "XLlinks", XLrechts: "XLrechts" },
	// OBS scene name -> page number (1 = first page)
	scenes: { Intro: 1, Moderator: 2, iRacing: 3, Werbung: 4, "Schedule PCC": 5 },
};

export function parseConfig(text) {
	const raw = text ? JSON.parse(text) : {};
	const cfg = {
		obs: { ...DEFAULT_CONFIG.obs, ...(raw.obs || {}) },
		decks: raw.decks || DEFAULT_CONFIG.decks,
		scenes: raw.scenes || DEFAULT_CONFIG.scenes,
	};
	cfg.obs.port = Number(cfg.obs.port) || 4455;
	return cfg;
}

/** Page number (1-based) for a scene; exact name first, then ignoring case and spaces. */
export function pageForScene(cfg, scene) {
	if (scene in cfg.scenes) return Number(cfg.scenes[scene]) || null;
	const hit = Object.keys(cfg.scenes).find((k) => norm(k) === norm(scene));
	return hit ? Number(cfg.scenes[hit]) || null : null;
}

/** Profile for a deck name (exact, then ignoring case and spaces), or null. */
export function profileForDeck(cfg, deckName) {
	if (deckName in cfg.decks) return cfg.decks[deckName];
	const hit = Object.keys(cfg.decks).find((k) => norm(k) === norm(deckName));
	return hit ? cfg.decks[hit] : null;
}

/**
 * Switches to make for a scene: [{ deviceId, deckName, profile, page }]
 * with page 0-based, as switchToProfile wants it. Unknown scene -> [],
 * so the decks stay where they are.
 * devices: [{ id, name, isConnected }]
 */
export function targetsForScene(cfg, scene, devices) {
	const page = pageForScene(cfg, scene);
	if (!page) return [];
	const out = [];
	for (const d of devices) {
		if (d.isConnected === false) continue;
		const profile = profileForDeck(cfg, d.name);
		if (profile) out.push({ deviceId: d.id, deckName: d.name, profile, page: page - 1 });
	}
	return out;
}
