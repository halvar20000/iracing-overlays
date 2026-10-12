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
import { parseConfig, pageForScene, targetsForScene } from "./src/pages.js";

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
check("scene name with space", pageForScene(cfg, "Schedule PCC") === 5);
check("scene name case / spaces tolerant", pageForScene(cfg, "schedule  pcc") === 5 && pageForScene(cfg, "IRACING") === 3);
check("unknown scene -> no switch", targetsForScene(cfg, "Pause", devs).length === 0);
check("disconnected deck skipped", targetsForScene(cfg, "Intro", [{ id: "L", name: "XLlinks", isConnected: false }]).length === 0);
check("deck name tolerant (xl links)", targetsForScene(cfg, "Intro", [{ id: "L", name: "xl links", isConnected: true }]).length === 1);
check("empty config -> defaults", parseConfig("").obs.port === 4455 && pageForScene(parseConfig(""), "Moderator") === 2);

// --------------------------------------------------------- fake OBS server
function fakeObs(port, { password = "", scene = "Intro" } = {}) {
	const wss = new WebSocketServer({ port });
	const state = { scene, clients: [], identified: 0, authFailed: 0 };
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
			} else if (m.op === 6 && m.d.requestType === "GetCurrentProgramScene") {
				ws.send(JSON.stringify({ op: 7, d: { requestType: m.d.requestType, requestId: m.d.requestId, requestStatus: { result: true, code: 100 }, responseData: { currentProgramSceneName: state.scene, sceneName: state.scene } } }));
			}
		});
	});
	state.setScene = (name) => {
		state.scene = name;
		for (const c of state.clients) if (c.readyState === 1) c.send(JSON.stringify({ op: 5, d: { eventType: "CurrentProgramSceneChanged", eventIntent: 4, eventData: { sceneName: name } } }));
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
{
	const obsSrv = fakeObs(4455, { scene: "Intro" }); // plugin's config.json port
	const sd = new WebSocketServer({ port: 48600 });
	const switches = [];
	let registered = false;
	sd.on("connection", (ws) => {
		ws.on("message", (raw) => {
			const m = JSON.parse(raw.toString());
			if (m.event === "registerPlugin") {
				registered = true;
				// like the real app: announce the connected decks after registration
				for (const d of info.devices) ws.send(JSON.stringify({ event: "deviceDidConnect", device: d.id, deviceInfo: { name: d.name, size: d.size, type: d.type } }));
			}
			if (m.event === "switchToProfile") switches.push({ device: m.device, profile: m.payload.profile, page: m.payload.page });
		});
	});
	const info = {
		application: { font: "", language: "de", platform: "windows", platformVersion: "10", version: "7.6.0.23012" },
		plugin: { uuid: "com.halvar.obspages", version: "1.0.0.0" },
		devicePixelRatio: 2,
		colors: {},
		devices: [
			{ id: "DEV-L", name: "XLlinks", size: { columns: 8, rows: 4 }, type: 2 },
			{ id: "DEV-R", name: "XLrechts", size: { columns: 8, rows: 4 }, type: 2 },
			{ id: "DEV-P", name: "Stream Deck +", size: { columns: 4, rows: 2 }, type: 7 },
		],
	};
	const dir = fileURLToPath(new URL("./com.halvar.obspages.sdPlugin/", import.meta.url));
	const proc = spawn(process.execPath, ["bin/plugin.js", "-port", "48600", "-pluginUUID", "com.halvar.obspages", "-registerEvent", "registerPlugin", "-info", JSON.stringify(info)], { cwd: dir, stdio: ["ignore", "pipe", "pipe"] });
	let stderr = "";
	proc.stderr.on("data", (d) => (stderr += d));

	check("e2e: plugin registers with Stream Deck", await waitFor(() => registered), stderr.slice(0, 300));
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
	proc.kill();
	await obs2.close();
	await new Promise((r) => sd.close(r));
}

console.log(`\n${pass}/${pass + fail} checks passed`);
process.exit(fail ? 1 : 0);
