import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const page = read("../components/memory/index.tsx");
const pageCss = read("../components/memory/memory-page.module.css");
const settings = read("../components/settings/memory-settings.tsx");
const settingsCss = read("../components/settings/memory-settings.module.css");
const settingsNav = read("../components/settings/settings-tabs-layout.tsx");
const settingsCache = read("../lib/prefs/settings-cache.ts");
const design = read("../../../docs/reference/design/memory/memory-settings-ui.html");

assert.match(settingsNav, /href: "\/settings\/memory"/);
assert.match(page, /href="\/settings\/memory"/);
assert.match(settings, /\/api\/settings\?scope=memory/);
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
assert.doesNotMatch(
  settings,
  /Save changes|styles\.saveBar|saveVersion/,
  "Memory settings persist immediately and must not expose a Save bar",
);
assert.doesNotMatch(design, /save-strip|saveSettings|Save changes/);
assert.doesNotMatch(design, /status restart[^>]*>Next start/);
assert.match(design, /Changing this switch requires restarting OpenProgram/);
assert.match(settings, /async function update[\s\S]*?method: "POST"/);
assert.match(settings, /if \(!settingsReady \|\| saving \|\| installing\) return/);
assert.match(settings, /\[key\]: previous/);
assert.match(settings, /setSettingsReady\(true\)/);
assert.match(settings, /const controlsDisabled = !settingsReady \|\| saving \|\| installing/);
assert.match(settings, /retrieval === "agent"/);
const blockingRequests = settings.match(/Promise\.all\(\[([\s\S]*?)\]\)/)?.[1] ?? "";
assert.doesNotMatch(
  blockingRequests,
  /\/api\/memory\/status/,
  "Memory status must not block the editable settings",
);
assert.doesNotMatch(
  settings,
  /Promise\.all\([\s\S]*?listEnabledModels/,
  "Writer model choices must not block the editable Memory settings",
);
assert.doesNotMatch(settingsNav, /prefetchSettings/);
assert.doesNotMatch(settingsCache, /export function prefetchSettings/);
assert.match(settings, /memory\.writer\.model/);
assert.match(settings, /memory\.retrieval\.method/);
assert.match(settings, /shared\.pageHeader/);
assert.match(settings, /shared\.pageTitle/);
assert.match(settings, /shared\.pageMeta/);
assert.match(settings, /shared\.pageTitle\}>\{t\("settings\.tab\.memory"\)\}/);
assert.match(settings, /const pageHeader =[\s\S]*?if \(!loaded\)[\s\S]*?\{pageHeader\}/);
assert.match(settings, /className=\{shared\.card\}/);
assert.match(settings, /className=\{`\$\{shared\.row\}/);
assert.equal(
  (settings.match(/<Switch[^>]+aria-label=/g) || []).length,
  4,
  "each Memory switch needs an accessible name",
);
assert.ok((settings.match(/disabled=\{controlsDisabled(?: \|\| retrieval === "agent")?\}/g) || []).length >= 9);
assert.match(settings, /disabled=\{saving \|\| installing\}/);
assert.match(settings, /role="alert"/);
assert.match(pageCss, /@media \(max-width: 720px\)/);
assert.match(pageCss, /grid-template-rows: auto minmax\(0, 1fr\)/);
assert.doesNotMatch(pageCss, /\.tabBar\s*\{[^}]*display:\s*none/s);
assert.match(
  settingsCss,
  /\.controls\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*justify-content:\s*flex-end;[^}]*gap:\s*8px/,
);
assert.match(
  settingsCss,
  /\.lifecycle\s*\{[^}]*flex-shrink:\s*0/s,
  "the lifecycle cards must keep their intrinsic height in the scrolling column",
);
assert.match(settingsCss, /\.lifecycle\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(settingsCss, /\.lifecycle\s*\{[^}]*border-radius:\s*10px;[^}]*background:\s*var\(--bg-tertiary\)/s);
assert.doesNotMatch(settingsCss, /\.card\s*\{/);
assert.doesNotMatch(settingsCss, /\.row\s*\{/);
assert.match(settingsCss, /\.rowDescription\s*\{[^}]*font-size:\s*13px/s);
assert.match(settingsCss, /\.controls\s*\{[^}]*flex:\s*0 1 auto;[^}]*min-width:\s*7\.5rem/s);
assert.match(settingsCss, /\.select\s*\{[^}]*min-width:\s*0/s);
assert.match(settingsCss, /@media \(max-width: 820px\)[\s\S]*?\.settingsRow\s*\{[^}]*flex-direction:\s*column;[^}]*align-items:\s*stretch;[^}]*gap:\s*10px;[^}]*\}[\s\S]*?\.controls\s*\{[^}]*min-width:\s*0;[^}]*\}[\s\S]*?\.select\s*\{[^}]*max-width:\s*100%;[^}]*\}/s);
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
const enableMemoryRow = settingRows.find((row) => row.includes('label={text("Enable Memory"')) ?? "";
assert.ok(enableMemoryRow, "expected Enable Memory row");
assert.doesNotMatch(
  enableMemoryRow,
  /<Status/,
  "Enable Memory must not present a permanent next-start badge as pending state",
);
assert.match(enableMemoryRow, /Changing this switch requires restarting OpenProgram/);
for (const row of settingRows) {
  const statusIdx = row.search(/<Status[\s>]/);
  const controlIdx = row.search(/<(?:select|Switch)\b/);
  if (statusIdx !== -1 && controlIdx !== -1) {
    assert.ok(statusIdx < controlIdx, `status chip must sit left of the control:\n${row}`);
  }
}

console.log("check-memory-settings: ok");
