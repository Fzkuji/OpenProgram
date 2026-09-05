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
const desktopChrome = read("../desktop/theme-chrome.js");
const desktopPreload = read("../desktop/preload.js");
const layout = read("app/layout.tsx");

function quotedValues(source, declaration) {
  const match = source.match(new RegExp(`(?:export\\s+)?const\\s+${declaration}\\s*=\\s*\\[([\\s\\S]*?)\\]`));
  assert.ok(match, `${declaration} must be a literal array`);
  return [...match[1].matchAll(/["']([^"']+)["']/g)].map((item) => item[1]);
}

const themeIds = quotedValues(themeConfig, "THEME_IDS");
const customSlots = themeIds.filter((id) => id === "custom" || id === "custom-light");
const builtins = themeIds.filter((id) => !customSlots.includes(id));
const importedThemes = [...globals.matchAll(/@import "\.\/styles\/themes\/([^".]+)\.css";/g)]
  .map((match) => match[1]);
assert.deepEqual(
  importedThemes,
  [...builtins, "custom-light"],
  "CSS theme imports must be the built-ins plus the custom-light fallback sheet",
);
assert.deepEqual(customSlots, ["custom", "custom-light"]);
const themeStyles = quotedValues(themeConfig, "THEME_STYLES");
assert.deepEqual(themeStyles, ["beige", "neutral", "aurora"]);
assert.doesNotMatch(themeConfig, /custom:\s*\{\s*dark:\s*"custom"/);
assert.match(desktopChrome, /THEME_STYLES = \["beige", "neutral", "aurora"\]/);
assert.match(themeConfig, /THEME_DEFAULT_ACCENTS/);

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
  assert.match(
    themeConfig,
    new RegExp(`["']?${id.replaceAll("-", "\\-")}["']?:\\s*"${values[0]}"`),
    `THEME_DEFAULT_ACCENTS.${id} must stay sourced from ${id} --accent-orange`,
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
  assert.match(themeConfig, new RegExp(`${token.replaceAll("-", "\\-")}\\s*:`), `Custom CSS template is missing ${token}`);
}
assert.match(themeConfig, /html\[data-theme="beige-dark"\]/);
assert.match(themeConfig, /html\[data-theme="beige-light"\]/);
assert.match(settings, /CUSTOM_CSS_TEMPLATE/);
assert.match(settings, /type="color"/);
assert.match(settings, /Enable custom CSS/);
assert.match(settings, /Insert template/);
assert.match(settings, /Reset to theme default/);
assert.doesNotMatch(settings, /pick the "Custom" color style/);
assert.doesNotMatch(settings, /选择「自定义」颜色风格/);
assert.match(settings, /THEME_STYLE_PAIRS\[style\]\[m\]/);
assert.match(settings, /THEME_STYLE_PAIRS\[s\]\[mode\]/);

const beigeLight = read("app/styles/themes/beige-light.css");
const customLight = read("app/styles/themes/custom-light.css");
assert.match(customLight, /\[data-theme=["']custom-light["']\]/);
assert.deepEqual(tokens(customLight), requiredTokens, "custom-light must define the complete theme contract");
for (const token of requiredTokens) {
  assert.equal(
    tokenValue(customLight, token),
    tokenValue(beigeLight, token),
    `custom-light ${token} must copy beige-light`,
  );
}

function cssFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return extname(entry.name) === ".css" ? [path] : [];
  });
}
for (const file of cssFiles(join(root, "app", "styles")).concat(cssFiles(join(root, "components")))) {
  const projectPath = relative(root, file).replaceAll("\\", "/");
  if (projectPath.startsWith("app/styles/themes/")) continue;
  assert.doesNotMatch(
    readFileSync(file, "utf8"),
    /\[data-theme=["'](?:beige-dark|beige-light|dark|light|aurora|aurora-light)["']\]/,
    `${projectPath} must consume tokens instead of branching on a theme id`,
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
const desktopThemeIds = quotedValues(desktopChrome, "THEME_IDS");
assert.deepEqual(desktopThemeIds, themeIds);
assert.match(desktopMain, /MENU_THEME_ID_SET\.has\(theme\)/);
assert.match(desktopMain, /require\("\.\/theme-chrome"\)/);
assert.match(desktopMain, /backgroundColor:\s*currentChrome\.bg/);
assert.doesNotMatch(desktopMain, /#141416/);
assert.match(desktopPreload, /theme:\s*\{/);
assert.match(desktopPreload, /theme:set-chrome/);
assert.match(bridgeTypes, /export interface DesktopThemeApi/);
assert.match(bridge, /theme\?:\s*DesktopThemeApi/);

function chromeField(source, field) {
  const match = source.match(/const THEME_CHROME = \{([\s\S]*?)\n\};/);
  assert.ok(match, "THEME_CHROME must be a literal object");
  return Object.fromEntries(
    [...match[1].matchAll(new RegExp(
      `(?:["']([a-z0-9-]+)["']|([a-z0-9-]+))\\s*:\\s*\\{[\\s\\S]*?\\b${field}:\\s*["']([^"']+)["']`,
      "g",
    ))].map((item) => [item[1] || item[2], item[3]]),
  );
}
const chromeBgs = chromeField(desktopChrome, "bg");
const chromeLinks = chromeField(desktopChrome, "link");
assert.deepEqual(Object.keys(chromeBgs).sort(), builtins.slice().sort());
for (const id of builtins) {
  const source = read(`app/styles/themes/${id}.css`);
  assert.equal(
    chromeBgs[id].toLowerCase(),
    tokenValue(source, "--bg-primary").toLowerCase(),
    `desktop THEME_CHROME.${id}.bg must be ${id} --bg-primary`,
  );
  assert.equal(
    chromeLinks[id].toLowerCase(),
    tokenValue(source, "--accent-orange").toLowerCase(),
    `desktop THEME_CHROME.${id}.link must be ${id} --accent-orange`,
  );
}
assert.match(desktopChrome, /if \(accent\) chrome\.link = accent/);
assert.match(desktopMain, /accentColor/);
assert.match(bridgeTypes, /accentColor\?:\s*string/);
assert.doesNotMatch(layout, /localStorage\.getItem\('agentic_custom_css'\)/);
assert.match(desktopChrome, /custom:\s*"beige-dark"/);
assert.match(desktopChrome, /"custom-light":\s*"beige-light"/);
assert.equal(chromeBgs["beige-dark"], "#262624");
assert.equal(chromeBgs["beige-light"], "#faf9f5");

assert.match(base, /button, input, select, textarea, optgroup\s*\{[^}]*font-family:\s*inherit/s);
assert.match(layout, /style\.setProperty\('--font-sans', FONTS\[f\]\)/);

console.log(`theme contract checks passed (${builtins.length} built-ins, ${requiredTokens.length} tokens each)`);
