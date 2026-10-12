// Bundles src/plugin.js (+ @elgato/streamdeck and ws) into one file the
// Stream Deck app runs with its own Node — the installed plugin needs no
// node_modules.
import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";

export default {
	input: "src/plugin.js",
	output: {
		file: "com.halvar.obspages.sdPlugin/bin/plugin.js",
		format: "es",
		sourcemap: false,
	},
	plugins: [
		nodeResolve({ preferBuiltins: true, exportConditions: ["node"] }),
		// ws tries require("bufferutil") / require("utf-8-validate") inside a
		// try/catch (optional native speed-ups); left untouched they simply
		// fail there and ws uses its plain-JS code.
		commonjs({ ignore: ["bufferutil", "utf-8-validate"] }),
		{
			// bin/plugin.js is an ES module; tell Node so.
			name: "emit-module-package-file",
			generateBundle() {
				this.emitFile({ fileName: "package.json", source: '{ "type": "module" }\n', type: "asset" });
			},
		},
	],
};
