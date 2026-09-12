import { build } from "esbuild";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
const root = new URL("../", import.meta.url);
const cssPlugin = { name: "css-module-test", setup(api) {
  api.onResolve({ filter: /\.module\.css$/ }, (args) => ({ path: args.path, namespace: "css-module-test" }));
  api.onLoad({ filter: /.*/, namespace: "css-module-test" }, () => ({ contents: "export default new Proxy({}, {get: (_, key) => String(key)})", loader: "js" }));
} };
const output = process.argv[2];
if (!output) throw new Error("output path is required");
const entry = new URL("./office-document-browser-entry.tsx", import.meta.url);
if (!existsSync(fileURLToPath(entry))) throw new Error("Office browser entry is missing");
await build({ entryPoints: [fileURLToPath(entry)], outfile: output, bundle: true, format: "iife", platform: "browser",
  sourcemap: false, plugins: [cssPlugin], tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)), jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' }, loader: { ".svg": "dataurl" } });
