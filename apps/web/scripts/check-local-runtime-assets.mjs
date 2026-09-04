import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");

const runtimeFiles = [
  "components/app-shell.tsx",
  "components/chat/messages/markdown.ts",
  "components/settings/provider-icon.tsx",
  "lib/format-utils/markdown.tsx",
  "lib/net/use-ws.ts",
  "lib/runtime-bridge/conversations.ts",
  "lib/runtime-bridge/markdown-render.ts",
  "public/html/index.html",
];
const runtimeSource = runtimeFiles.map((path) => read(path)).join("\n");

assert.doesNotMatch(
  runtimeSource,
  /https?:\/\/(?:cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|cdn\.simpleicons\.org|models\.dev\/logos|unpkg\.com)/,
  "product runtime source and templates must not load assets from a CDN",
);
assert.doesNotMatch(
  runtimeSource,
  /externalLibsReady|loadExternalLibs/,
  "WebSocket and shell startup must not wait for an external dependency loader",
);
assert.equal(
  existsSync(fileURLToPath(new URL("lib/external-libs.ts", root))),
  false,
  "the runtime CDN loader must be removed",
);
assert.match(
  read("lib/runtime-bridge/markdown-render.ts"),
  /from ["']marked["']/,
  "chat markdown must use the bundled marked dependency",
);
assert.match(
  read("lib/runtime-bridge/markdown-render.ts"),
  /from ["']katex\/contrib\/auto-render["']/,
  "chat math rendering must use bundled KaTeX auto-render",
);
assert.match(
  read("app/layout.tsx"),
  /import ["']katex\/dist\/katex\.min\.css["']/,
  "the root layout must bundle KaTeX CSS",
);

const packageJson = JSON.parse(read("package.json"));
assert.equal(
  typeof packageJson.dependencies?.katex,
  "string",
  "apps/web must declare KaTeX as a production dependency",
);

console.log("local runtime assets contract: ok");
