import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const page = read("../components/memory/index.tsx");
const pageCss = read("../components/memory/memory-page.module.css");
const settings = read("../components/settings/memory-settings.tsx");
const settingsCss = read("../components/settings/memory-settings.module.css");
const settingsNav = read("../components/settings/settings-tabs-layout.tsx");

assert.match(settingsNav, /href: "\/settings\/memory"/);
assert.match(page, /href="\/settings\/memory"/);
assert.match(settings, /\/api\/settings/);
assert.match(settings, /listEnabledModels/);
assert.match(settings, /\/api\/memory\/status/);
assert.match(settings, /embedding_available/);
assert.match(settings, /memory\.writer\.model/);
assert.match(settings, /memory\.retrieval\.method/);
assert.equal(
  (settings.match(/<Switch[^>]+aria-label=/g) || []).length,
  4,
  "each Memory switch needs an accessible name",
);
assert.match(settings, /disabled=\{saving\}/);
assert.match(settings, /role=\{messageKind === "error" \? "alert" : "status"\}/);
assert.match(settings, /saveVersion\.current !== startedVersion/);
assert.match(pageCss, /@media \(max-width: 720px\)/);
assert.match(pageCss, /grid-template-rows: auto minmax\(0, 1fr\)/);
assert.doesNotMatch(pageCss, /\.tabBar\s*\{[^}]*display:\s*none/s);
assert.match(
  settingsCss,
  /\.controls\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*justify-content:\s*flex-end;[^}]*gap:\s*8px/,
);

const settingRows = [...settings.matchAll(/<SettingsRow[\s\S]*?<\/SettingsRow>/g)].map((match) => match[0]);
assert.ok(settingRows.length >= 4, "expected Memory setting rows");
for (const row of settingRows) {
  const statusIdx = row.search(/<Status[\s>]/);
  const controlIdx = row.search(/<(?:select|Switch)\b/);
  if (statusIdx !== -1 && controlIdx !== -1) {
    assert.ok(statusIdx < controlIdx, `status chip must sit left of the control:\n${row}`);
  }
}

console.log("check-memory-settings: ok");
