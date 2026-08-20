import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const page = read("../components/memory/index.tsx");
const pageCss = read("../components/memory/memory-page.module.css");
const settings = read("../components/settings/memory-settings.tsx");
const settingsCss = read("../components/settings/memory-settings.module.css");
const settingsNav = read("../components/settings/settings-tabs-layout.tsx");
const settingsCache = read("../lib/prefs/settings-cache.ts");

assert.match(settingsNav, /href: "\/settings\/memory"/);
assert.match(page, /href="\/settings\/memory"/);
assert.match(settings, /\/api\/settings/);
assert.match(settings, /listEnabledModels/);
assert.doesNotMatch(
  settings,
  /getAgentSettings/,
  "Memory Settings must not initialize the provider runtime on first load",
);
assert.match(settings, /\/api\/memory\/status\?settings=true/);
assert.match(settings, /\/api\/memory\/embedding\/install/);
assert.match(settings, /embedding_available/);
assert.match(settings, /value="agent"/);
assert.match(settings, /Installing…/);
assert.match(
  settings,
  /styles\.saveButton[\s\S]{0,160}disabled=\{saving \|\| installing \|\| changed\.length === 0\}/,
  "install and save must not run concurrently",
);
assert.match(settings, /retrieval === "agent"/);
const blockingRequests = settings.match(/Promise\.all\(\[([\s\S]*?)\]\)/)?.[1] ?? "";
assert.doesNotMatch(
  blockingRequests,
  /\/api\/memory\/status/,
  "Memory status must not block the editable settings",
);
assert.doesNotMatch(settingsNav, /prefetchSettings/);
assert.doesNotMatch(settingsCache, /export function prefetchSettings/);
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
assert.match(settingsCss, /\.memoryPage\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(settingsCss, /\.lifecycle\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(settingsCss, /\.row\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*flex-start/s);
assert.match(settingsCss, /\.rowCopy\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-width:\s*0/s);
assert.match(settingsCss, /\.controls\s*\{[^}]*flex:\s*0 1 auto;[^}]*min-width:\s*7\.5rem/s);
assert.match(settingsCss, /\.select\s*\{[^}]*min-width:\s*0/s);
assert.match(settingsCss, /\.select:focus-visible\s*\{[^}]*outline:\s*none;[^}]*border-color:\s*var\(--text-secondary\)/s);
assert.doesNotMatch(settingsCss, /\.select:focus-visible\s*,\s*\.saveButton:focus-visible/);
assert.match(settingsCss, /\.chromeValue\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(settingsCss, /\.monoValue\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
assert.doesNotMatch(settingsCss, /JetBrains Mono/);
assert.doesNotMatch(settingsCss, /grid-template-columns:\s*minmax\(220px/);
assert.match(settings, /styles\.chromeValue[\s\S]{0,80}Local workspace · Git enabled/);
assert.match(settings, /styles\.monoValue[\s\S]{0,40}workspace_path/);
assert.equal((settings.match(/styles\.monoValue/g) || []).length, 1);

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
