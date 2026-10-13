// Offline checks for the OBS Seitenwechsel plugin.
//  1. pages.js mapping logic
//  2. ObsClient against a fake obs-websocket v5 server (with and without auth)
//  3. END TO END: the BUILT bin/plugin.js, started the way Stream Deck starts
//     it, against a fake Stream Deck app + fake OBS -> the switchToProfile
//     commands it sends.
// Run:  npm run build && node test_plugin.mjs
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import { ObsClient, authString } from "./src/obs.js";
import { normalizeSettings, pageFor, parseConfig, targetsForScene } from "./src/pages.js";

let pass = 0;
let fail = 0;
function check(name, cond, info = "") {
	if (cond) pass++;
	else fail++;
	console.log(cond ? "[PASS]" : "[FAIL]", name, info);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms = 4000) {
	const end = Date.now() + ms;
	while (Date.now() < end) {
		if (fn()) return true;
		await sleep(25);
	}
	return false;
}

// ---------------------------------------------------------------- 1. pages
const cfg = parseConfig(
	JSON.stringify({
		decks: { XLlinks: "XLlinks", XLrechts: "XLrechts" },
		scenes: { Intro: 1, Moderator: 2, iRacing: 3, Werbung: 4, "Schedule PCC": 5 },
	}),
);
const devs = [
	{ id: "L", name: "XLlinks", isConnected: true },
	{ id: "R", name: "XLrechts", isConnected: true },
	{ id: "P", name: "Stream Deck +", isConnected: true },
	{ id: "S", name: "Stream Deck", isConnected: true },
];
const t = targetsForScene(cfg, "iRacing", devs);
check("iRacing -> both XL, page index 2", t.length === 2 && t.every((x) => x.page === 2), JSON.stringify(t));
check("Plus / normal deck never touched", !t.some((x) => x.deviceId === "P" || x.deviceId === "S"));
check("scene name with space", pageFor(cfg, "Schedule PCC", "XLlinks") === 5);
check("scene name case / spaces tolerant", pageFor(cfg, "schedule  pcc", "XLrechts") === 5 && pageFor(cfg, "IRACING", "xllinks") === 3);
check("unknown scene -> no switch", targetsForScene(cfg, "Pause", devs).length === 0);
check("disconnected deck skipped", targetsForScene(cfg, "Intro", [{ id: "L", name: "XLlinks", isConnected: false }]).length === 0);
check("deck name tolerant (xl links)", targetsForScene(cfg, "Intro", [{ id: "L", name: "xl links", isConnected: true }]).length === 1);
check("empty config -> defaults", parseConfig("").obs.port === 4455 && pageFor(parseConfig(""), "Moderator", "XLrechts") === 2);

// settings window model: own page per deck, names mapped, decks off
const ui = normalizeSettings({
	version: 1,
	obs: { port: "4460", password: "x" },
	decks: { Main: "XLlinks", Cam: "XLrechts", Admin: "" },
	pages: { iRacing: { Main: 3, Cam: 1 }, Moderator: { Main: 2 }, Leer: {}, Kaputt: { Main: "abc", Cam: 0 } },
});
const andreas = [
	{ id: "M", name: "Main" },
	{ id: "C", name: "Cam" },
	{ id: "A", name: "Admin" },
];
const ti = targetsForScene(ui, "iRacing", andreas);
check("per-deck pages: Main 3, Cam 1", ti.length === 2 && ti.find((x) => x.deviceId === "M").page === 2 && ti.find((x) => x.deviceId === "C").page === 0, JSON.stringify(ti));
check("deck without page for a scene stays", targetsForScene(ui, "Moderator", andreas).map((x) => x.deviceId).join() === "M");
check("deck without profile (Admin) never switched", !ti.some((x) => x.deviceId === "A") && !("Admin" in ui.decks));
check("invalid pages dropped, empty scene kept", targetsForScene(ui, "Kaputt", andreas).length === 0 && "Leer" in ui.pages);
check("port as text -> number", ui.obs.port === 4460);
check("nothing saved / wrong version -> null (config.json)", normalizeSettings({}) === null && normalizeSettings(null) === null);

// --------------------------------------------------------- fake OBS server
function fakeObs(port, { password = "", scene = "Intro" } = {}) {
	const wss = new WebSocketServer({ port });
	const state = { scene, clients: [], identified: 0, authFailed: 0, listRequests: 0, list: ["Intro", "Moderator", "iRacing", "Werbung", "Schedule PCC"] };
	const salt = "c2FsdA==";
	const challenge = "Y2hhbGxlbmdl";
	wss.on("connection", (ws) => {
		state.clients.push(ws);
		const hello = { obsWebSocketVersion: "5.5.0", rpcVersion: 1 };
		if (password) hello.authentication = { salt, challenge };
		ws.send(JSON.stringify({ op: 0, d: hello }));
		ws.on("message", (raw) => {
			const m = JSON.parse(raw.toString());
			if (m.op === 1) {
				if (password && m.d.authentication !== authString(password, salt, challenge)) {
					state.authFailed++;
					ws.close(4009, "Authentication failed");
					return;
				}
				state.identified++;
				ws.send(JSON.stringify({ op: 2, d: { negotiatedRpcVersion: 1 } }));
			} else if (m.op === 6 && m.d.requestType === "GetSceneList") {
				state.listRequests++;
				// like obs-websocket: bottom of the dock first
				const scenes = [...state.list].reverse().map((sceneName, i) => ({ sceneName, sceneIndex: i }));
				ws.send(JSON.stringify({ op: 7, d: { requestType: m.d.requestType, requestId: m.d.requestId, requestStatus: { result: true, code: 100 }, responseData: { scenes } } }));
			} else if (m.op === 6 && m.d.requestType === "GetCurrentProgramScene") {
				ws.send(JSON.stringify({ op: 7, d: { requestType: m.d.requestType, requestId: m.d.requestId, requestStatus: { result: true, code: 100 }, responseData: { currentProgramSceneName: state.scene, sceneName: state.scene } } }));
			}
		});
	});
	state.setScene = (name) => {
		state.scene = name;
		for (const c of state.clients) if (c.readyState === 1) c.send(JSON.stringify({ op: 5, d: { eventType: "CurrentProgramSceneChanged", eventIntent: 4, eventData: { sceneName: name } } }));
	};
	state.renameScene = (from, to) => {
		state.list = state.list.map((n) => (n === from ? to : n));
		for (const c of state.clients) if (c.readyState === 1) c.send(JSON.stringify({ op: 5, d: { eventType: "SceneNameChanged", eventIntent: 4, eventData: { oldSceneName: from, sceneName: to } } }));
	};
	state.close = () => new Promise((r) => { for (const c of wss.clients) c.terminate(); wss.close(r); });
	return state;
}

// ----------------------------------------------------------- 2. ObsClient
{
	const obsSrv = fakeObs(48455, { scene: "Moderator" });
	const c = new ObsClient({ port: 48455 });
	const scenes = [];
	c.on("scene", (s) => scenes.push(s));
	c.start();
	check("client: current scene on connect", await waitFor(() => scenes[0] === "Moderator"), JSON.stringify(scenes));
	obsSrv.setScene("iRacing");
	check("client: scene change event", await waitFor(() => scenes.at(-1) === "iRacing"));
	check("client: scene list in OBS dock order", await waitFor(() => (c.scenes || []).join() === "Intro,Moderator,iRacing,Werbung,Schedule PCC"), JSON.stringify(c.scenes));
	obsSrv.renameScene("Werbung", "Sponsoren");
	check("client: scene list follows a rename", await waitFor(() => (c.scenes || []).includes("Sponsoren")), JSON.stringify(c.scenes));
	c.stop();
	await obsSrv.close();
}
{
	const obsSrv = fakeObs(48456, { password: "geheim", scene: "Intro" });
	const c = new ObsClient({ port: 48456, password: "geheim" });
	let got = null;
	c.on("scene", (s) => (got = s));
	c.start();
	check("client: auth with password", await waitFor(() => got === "Intro") && obsSrv.authFailed === 0);
	c.stop();
	// new address from the settings window -> reconnects there at once
	const other = fakeObs(48458, { scene: "Werbung" });
	const c2 = new ObsClient({ port: 48456, password: "geheim" });
	let got2 = null;
	c2.on("scene", (s) => (got2 = s));
	c2.start();
	await waitFor(() => got2 === "Intro");
	c2.configure({ port: 48458 });
	check("client: configure() switches to the new port", await waitFor(() => got2 === "Werbung") && c2.url.endsWith(":48458"));
	c2.stop();
	await other.close();
	await obsSrv.close();
}
{
	// OBS not running yet -> client retries and connects once it is up
	const c = new ObsClient({ port: 48457 });
	let got = null;
	c.on("scene", (s) => (got = s));
	c.start();
	await sleep(600);
	const obsSrv = fakeObs(48457, { scene: "Werbung" });
	check("client: reconnects when OBS starts later", await waitFor(() => got === "Werbung", 6000));
	c.stop();
	await obsSrv.close();
}

// ------------------------------------------------- 3. end to end (bin/)
// Fake Stream Deck app: registration, decks, global settings storage (kept
// across plugin restarts like the real app), the OBS-Status key and its
// settings window (sendToPlugin / sendToPropertyInspector).
{
	const obsSrv = fakeObs(4455, { scene: "Intro" }); // plugin's config.json port
	const sd = new WebSocketServer({ port: 48600 });
	const switches = [];
	const toPI = [];
	let globalSettings = {};
	let registered = 0;
	let sock = null;
	const info = {
		application: { font: "", language: "de", platform: "windows", platformVersion: "10", version: "7.6.0.23012" },
		plugin: { uuid: "com.halvar.obspages", version: "1.1.0.0" },
		devicePixelRatio: 2,
		colors: {},
		devices: [
			{ id: "DEV-L", name: "XLlinks", size: { columns: 8, rows: 4 }, type: 2 },
			{ id: "DEV-R", name: "XLrechts", size: { columns: 8, rows: 4 }, type: 2 },
			{ id: "DEV-P", name: "Stream Deck +", size: { columns: 4, rows: 2 }, type: 7 },
		],
	};
	sd.on("connection", (ws) => {
		sock = ws;
		ws.on("message", (raw) => {
			const m = JSON.parse(raw.toString());
			if (m.event === "registerPlugin") {
				registered++;
				// like the real app: announce the connected decks after registration
				for (const d of info.devices) ws.send(JSON.stringify({ event: "deviceDidConnect", device: d.id, deviceInfo: { name: d.name, size: d.size, type: d.type } }));
			}
			if (m.event === "getGlobalSettings") ws.send(JSON.stringify({ event: "didReceiveGlobalSettings", id: m.id, payload: { settings: globalSettings } }));
			if (m.event === "setGlobalSettings") globalSettings = m.payload;
			if (m.event === "switchToProfile") switches.push({ device: m.device, profile: m.payload.profile, page: m.payload.page });
			if (m.event === "sendToPropertyInspector") toPI.push(m.payload);
		});
	});
	const keyCtx = "KEY-1";
	const key = { action: "com.halvar.obspages.status", context: keyCtx, device: "DEV-P" };
	function openSettingsWindow() {
		sock.send(JSON.stringify({ event: "willAppear", ...key, payload: { controller: "Keypad", coordinates: { column: 0, row: 0 }, isInMultiAction: false, settings: {} } }));
		sock.send(JSON.stringify({ event: "propertyInspectorDidAppear", ...key }));
	}
	const fromPI = (payload) => sock.send(JSON.stringify({ event: "sendToPlugin", ...key, payload }));
	const lastState = () => toPI.filter((p) => p.type === "state").at(-1);

	const dir = fileURLToPath(new URL("./com.halvar.obspages.sdPlugin/", import.meta.url));
	let stderr = "";
	const startPlugin = () => {
		const p = spawn(process.execPath, ["bin/plugin.js", "-port", "48600", "-pluginUUID", "com.halvar.obspages", "-registerEvent", "registerPlugin", "-info", JSON.stringify(info)], { cwd: dir, stdio: ["ignore", "pipe", "pipe"] });
		p.stderr.on("data", (d) => (stderr += d));
		return p;
	};
	let proc = startPlugin();

	check("e2e: plugin registers with Stream Deck", await waitFor(() => registered === 1), stderr.slice(0, 300));
	check("e2e: on start -> both XL to page 1 (Intro)", await waitFor(() => switches.length >= 2), JSON.stringify(switches));
	await sleep(300);
	check(
		"e2e: XLlinks/XLrechts profiles, page index 0, Plus untouched",
		switches.some((s) => s.device === "DEV-L" && s.profile === "XLlinks" && s.page === 0) &&
			switches.some((s) => s.device === "DEV-R" && s.profile === "XLrechts" && s.page === 0) &&
			switches.every((s) => s.page === 0) &&
			!switches.some((s) => s.device === "DEV-P"),
		JSON.stringify(switches),
	);
	switches.length = 0;
	obsSrv.setScene("iRacing");
	check("e2e: iRacing -> page 3 on both", await waitFor(() => switches.length >= 2) && switches.every((s) => s.page === 2), JSON.stringify(switches));
	switches.length = 0;
	obsSrv.setScene("Schedule PCC");
	check("e2e: Schedule PCC -> page 5", await waitFor(() => switches.length >= 2) && switches.every((s) => s.page === 4), JSON.stringify(switches));
	switches.length = 0;
	obsSrv.setScene("Irgendwas");
	await sleep(500);
	check("e2e: unknown scene -> decks stay", switches.length === 0, JSON.stringify(switches));
	// OBS restart: plugin reconnects and re-applies the scene on air
	await obsSrv.close();
	await sleep(300);
	const obs2 = fakeObs(4455, { scene: "Moderator" });
	check("e2e: OBS restarted -> reconnect, Moderator -> page 2", await waitFor(() => switches.some((s) => s.page === 1), 8000), JSON.stringify(switches));

	// ---- settings window
	openSettingsWindow();
	fromPI({ type: "get" });
	check("e2e/ui: window gets the state", await waitFor(() => lastState()?.obs?.scenes?.length === 5), JSON.stringify(lastState())?.slice(0, 300));
	let st = lastState();
	check(
		"e2e/ui: OBS scenes, decks with type, bundled profiles, source config.json",
		st.obs.connected && st.obs.scene === "Moderator" && st.decks.length === 3 && st.decks.find((d) => d.id === "DEV-P").type === 7 &&
			st.profiles.map((p) => `${p.name}:${p.deviceType}`).join() === "XLlinks:2,XLrechts:2" && st.source === "config",
		JSON.stringify(st).slice(0, 400),
	);
	// Andreas-style: only the left XL, its own page per scene; right XL off
	switches.length = 0;
	toPI.length = 0;
	fromPI({ type: "save", settings: { obs: { port: 4455, password: "" }, decks: { XLlinks: "XLlinks" }, pages: { iRacing: { XLlinks: 4 }, Moderator: { XLlinks: 2 } } } });
	check("e2e/ui: save -> stored in the app's global settings", await waitFor(() => globalSettings.version === 1 && globalSettings.pages?.iRacing?.XLlinks === 4), JSON.stringify(globalSettings));
	check("e2e/ui: save -> window shows source 'app'", await waitFor(() => lastState()?.source === "app"));
	obs2.setScene("iRacing");
	check("e2e/ui: new settings live: only XLlinks, page 4", await waitFor(() => switches.length >= 1) && (await sleep(300), switches.length === 1 && switches[0].device === "DEV-L" && switches[0].page === 3), JSON.stringify(switches));
	switches.length = 0;
	obs2.setScene("Werbung");
	await sleep(400);
	check("e2e/ui: scene without a page -> nothing switched", switches.length === 0, JSON.stringify(switches));
	switches.length = 0;
	fromPI({ type: "apply" });
	await sleep(400);
	check("e2e/ui: 'apply' with a scene without page -> nothing", switches.length === 0);

	// plugin update / app restart: settings come back from the app, not config.json
	proc.kill();
	await sleep(300);
	switches.length = 0;
	obs2.setScene("iRacing");
	proc = startPlugin();
	check("e2e/ui: after restart the saved settings apply (only XLlinks p4)", await waitFor(() => switches.length >= 1, 6000) && (await sleep(400), switches.length === 1 && switches[0].page === 3), JSON.stringify(switches));

	// OBS port changed in the window -> reconnects there
	const obs3 = fakeObs(4466, { scene: "Moderator" });
	openSettingsWindow();
	switches.length = 0;
	fromPI({ type: "save", settings: { ...globalSettings, obs: { port: 4466, password: "" } } });
	check("e2e/ui: new OBS port -> connects there (Moderator -> page 2)", await waitFor(() => switches.some((s) => s.page === 1), 6000), JSON.stringify(switches));

	// reset -> config.json again
	toPI.length = 0;
	fromPI({ type: "reset" });
	check("e2e/ui: reset -> source config.json, settings cleared", await waitFor(() => lastState()?.source === "config") && Object.keys(globalSettings).length === 0);

	proc.kill();
	await obs2.close();
	await obs3.close();
	await new Promise((r) => sd.close(r));
}

console.log(`\n${pass}/${pass + fail} checks passed`);
process.exit(fail ? 1 : 0);
