import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const read = (path) => readFileSync(join(root, path), "utf8");

const themeConfig = read("lib/prefs/theme-config.ts");
const globals = read("app/globals.css");
const base = read("app/styles/base.css");
const settings = read("components/settings/general-section.tsx");
const composer = read("components/chat/composer/composer.module.css");
const settingsCss = read("components/settings/settings-page.module.css");
const dagNodes = read("app/styles/dag/nodes.css");
const dagNodeRenderer = read("lib/runtime-bridge/dag/render/nodes.ts");
const dagEdgeRenderer = read("lib/runtime-bridge/dag/render/edges.ts");
const bridge = read("lib/desktop-bridge.ts");
const bridgeTypes = read("lib/desktop-bridge-types.ts");
const browserControls = read("components/center-tabs/browser-controls.tsx");
const mainMenu = read("components/center-tabs/main-menu.tsx");
const tabMenu = read("components/center-tabs/use-tab-menu.ts");
const mainOverlay = read("app/menu-overlay/main-menu/page.tsx");
const contextOverlay = read("app/menu-overlay/context-menu/page.tsx");
const desktopMain = read("../desktop/main.js");
const layout = read("app/layout.tsx");

function quotedValues(source, declaration) {
  const match = source.match(new RegExp(`(?:export\\s+)?const\\s+${declaration}\\s*=\\s*\\[([\\s\\S]*?)\\]`));
  assert.ok(match, `${declaration} must be a literal array`);
  return [...match[1].matchAll(/["']([^"']+)["']/g)].map((item) => item[1]);
}

const themeIds = quotedValues(themeConfig, "THEME_IDS");
const builtins = themeIds.filter((id) => id !== "custom");
const importedThemes = [...globals.matchAll(/@import "\.\/styles\/themes\/([^".]+)\.css";/g)]
  .map((match) => match[1]);
assert.deepEqual(importedThemes, builtins, "CSS theme imports must follow THEME_IDS exactly");

const requiredTokens = [
  "--accent-cyan", "--accent-fill", "--accent-green", "--accent-orange",
  "--accent-orange-hover", "--accent-purple", "--accent-red", "--accent-yellow",
  "--assistant-msg-bg", "--bg-hover", "--bg-hover-contrast", "--bg-input",
  "--bg-primary", "--bg-secondary", "--bg-selected", "--bg-tertiary", "--border",
  "--border-light", "--border-popover", "--chip-bg", "--chip-ring",
  "--composer-backdrop-filter", "--composer-ring", "--composer-ring-focus",
  "--composer-shadow", "--composer-shadow-focus", "--composer-surface",
  "--dag-ghost", "--danger-soft", "--effort-off-bg", "--focus-ring",
  "--meter-fill", "--meter-track", "--nav-color", "--nav-color-hover",
  "--primary-foreground", "--provider-icon-bg", "--scrim", "--scrim-strong",
  "--selection-bg", "--shadow", "--shadow-dialog", "--shadow-popover",
  "--shadow-sidebar-left", "--shadow-sidebar-right", "--shadow-sm", "--stream-info",
  "--stream-tool", "--success-soft", "--surface-popover", "--surface-tooltip",
  "--text-bright", "--text-muted", "--text-on-tooltip", "--text-primary",
  "--text-secondary", "--user-msg-bg", "--warning-soft",
].sort();

function tokens(source) {
  return [...new Set([...source.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((match) => match[1]))].sort();
}

for (const id of builtins) {
  const source = read(`app/styles/themes/${id}.css`);
  assert.match(source, new RegExp(`\\[data-theme=["']${id}["']\\]`));
  assert.deepEqual(tokens(source), requiredTokens, `${id} must define the complete theme contract`);
}
const expectedAccent = {
  "beige-dark": ["#d97757", "#d97757", "#c86a4a"],
  "beige-light": ["#c15f3c", "#c15f3c", "#a94e30"],
  dark: ["#6ea8fe", "#3b82f6", "#2563eb"],
  light: ["#2563eb", "#2563eb", "#1d4ed8"],
  aurora: ["#4fd6c0", "#35b8a4", "#2ea38f"],
  "aurora-light": ["#0f766e", "#0f766e", "#115e59"],
};
function tokenValue(source, token) {
  return source.match(new RegExp(`${token.replaceAll("-", "\\-")}\\s*:\\s*([^;]+);`))?.[1].trim();
}
for (const [id, values] of Object.entries(expectedAccent)) {
  const source = read(`app/styles/themes/${id}.css`);
  assert.deepEqual(
    ["--accent-orange", "--accent-fill", "--accent-orange-hover"].map((token) => tokenValue(source, token)),
    values,
    `${id} must use its assigned primary colour family`,
  );
}
for (const token of requiredTokens) {
  assert.ok(tokens(base).includes(token), `:root fallback is missing ${token}`);
}
for (const alias of [
  ["--theme-accent", "--accent-orange"],
  ["--theme-accent-fill", "--accent-fill"],
  ["--theme-accent-fill-hover", "--accent-orange-hover"],
  ["--accent-blue", "--theme-accent"],
]) {
  assert.equal(tokenValue(base, alias[0]), `var(${alias[1]})`, `${alias[0]} must remain a theme-aware compatibility alias`);
}
for (const token of ["--accent-orange", "--accent-fill", "--accent-orange-hover"]) {
  assert.match(settings, new RegExp(`${token.replaceAll("-", "\\-")}\\s*:`), `Custom CSS template is missing ${token}`);
}

function cssFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return extname(entry.name) === ".css" ? [path] : [];
  });
}
for (const file of cssFiles(join(root, "app", "styles")).concat(cssFiles(join(root, "components")))) {
  if (file.includes(`${join("styles", "themes")}/`)) continue;
  assert.doesNotMatch(
    readFileSync(file, "utf8"),
    /\[data-theme=["'](?:beige-dark|beige-light|dark|light|aurora|aurora-light)["']\]/,
    `${relative(root, file)} must consume tokens instead of branching on a theme id`,
  );
}
assert.match(composer, /background:\s*var\(--composer-surface\)/);
assert.match(composer, /backdrop-filter:\s*var\(--composer-backdrop-filter\)/);
assert.match(composer, /box-shadow:\s*var\(--composer-shadow\)/);
assert.match(composer, /box-shadow:\s*var\(--composer-shadow-focus\)/);
assert.match(settingsCss, /\.providerIcon\s*\{[^}]*background:\s*var\(--provider-icon-bg\)/s);
assert.match(dagNodes, /theme contract provides --dag-ghost/);
assert.match(dagNodeRenderer, /var\(--dag-ghost/);
assert.match(dagEdgeRenderer, /var\(--dag-ghost/);

assert.match(bridgeTypes, /theme\?:\s*ThemeId/);
for (const caller of [browserControls, mainMenu, tabMenu]) {
  assert.match(caller, /activeThemeId\(\)/);
  assert.doesNotMatch(caller, /theme\s*===\s*["']dark["']/);
}
for (const overlay of [mainOverlay, contextOverlay]) {
  assert.match(overlay, /isThemeId\(theme\)/);
}
const desktopThemeIds = quotedValues(desktopMain, "MENU_THEME_IDS");
assert.deepEqual(desktopThemeIds, themeIds);
assert.match(desktopMain, /MENU_THEME_ID_SET\.has\(theme\)/);

assert.match(base, /button, input, select, textarea, optgroup\s*\{[^}]*font-family:\s*inherit/s);
assert.match(layout, /style\.setProperty\('--font-sans', FONTS\[f\]\)/);

console.log(`theme contract checks passed (${builtins.length} built-ins, ${requiredTokens.length} tokens each)`);
