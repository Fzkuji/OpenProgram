import { build } from "esbuild";
import { fileURLToPath } from "node:url";
const entries = new URL("../", import.meta.url);
const cssPlugin = { name: "css-module-test", setup(buildApi) { buildApi.onResolve({ filter: /\.module\.css$/ }, (args) => ({ path: args.path, namespace: "css-module-test" })); buildApi.onLoad({ filter: /.*/, namespace: "css-module-test" }, () => ({ contents: "export default new Proxy({}, {get: (_, key) => String(key)})", loader: "js" })); } };
const output = process.argv[2];
await build({ entryPoints: [fileURLToPath(new URL(process.argv[3] ?? "./document-window-browser-entry.tsx", entries))], outfile: output, bundle: true, format: "iife", platform: "browser", sourcemap: false, plugins: [cssPlugin], tsconfig: fileURLToPath(new URL("../../tsconfig.json", import.meta.url)), jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".svg": "dataurl" } });
