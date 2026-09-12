import { build } from "esbuild";
import { readFileSync } from "node:fs";
const root = new URL("../", import.meta.url);
const cssPlugin = { name: "css-module-test", setup(buildApi) { buildApi.onResolve({ filter: /\.module\.css$/ }, (args) => ({ path: args.path, namespace: "css-module-test" })); buildApi.onLoad({ filter: /.*/, namespace: "css-module-test" }, () => ({ contents: "export default new Proxy({}, {get: (_, key) => String(key)})", loader: "js" })); } };
const output = process.argv[2];
await build({ entryPoints: [new URL("./document-window-browser-entry.tsx", import.meta.url).pathname], outfile: output, bundle: true, format: "iife", platform: "browser", sourcemap: false, plugins: [cssPlugin], tsconfig: new URL("../tsconfig.json", import.meta.url).pathname, define: { "process.env.NODE_ENV": '"production"' }, loader: { ".svg": "dataurl" } });
