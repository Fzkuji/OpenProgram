import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const page = read("../components/memory/index.tsx");
const pageCss = read("../components/memory/memory-page.module.css");
const settings = read("../components/settings/memory-settings.tsx");
const settingsNav = read("../components/settings/settings-tabs-layout.tsx");

assert.match(settingsNav, /href: "\/settings\/memory"/);
assert.match(page, /href="\/settings\/memory"/);
assert.match(settings, /\/api\/settings/);
assert.match(settings, /listEnabledModels/);
assert.match(settings, /\/api\/memory\/status/);
assert.match(settings, /embedding_available/);
assert.match(settings, /memory\.writer\.model/);
assert.match(settings, /memory\.retrieval\.method/);
assert.match(pageCss, /@media \(max-width: 720px\)/);
assert.match(pageCss, /grid-template-rows: auto minmax\(0, 1fr\)/);
assert.doesNotMatch(pageCss, /\.tabBar\s*\{[^}]*display:\s*none/s);

console.log("check-memory-settings: ok");
