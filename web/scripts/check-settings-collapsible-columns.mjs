import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const layout = source("components/settings/settings-tabs-layout.tsx");
const providers = source("components/settings/providers/index.tsx");
const providerItem = source("components/settings/providers/provider-item.tsx");
const css = source("components/settings/settings-page.module.css");

assert.match(layout, /localStorage\.getItem\("settingsNavOpen"\)/);
assert.match(layout, /localStorage\.setItem\("settingsNavOpen",/);
assert.match(layout, /aria-expanded=\{navOpen\}/);
assert.match(layout, /styles\.settingsNavCollapsed/);
assert.match(layout, /styles\.railItemIcon/);
assert.match(layout, /styles\.railItemLabel/);

assert.match(providers, /localStorage\.getItem\("providerListOpen"\)/);
assert.match(providers, /localStorage\.setItem\("providerListOpen",/);
assert.match(providers, /aria-expanded=\{listOpen\}/);
assert.match(providers, /styles\.providerListCollapsed/);
assert.match(providerItem, /title=\{p\.label\}/);

assert.match(css, /\.body\.settingsNavCollapsed\s*\{[^}]*grid-template-columns:\s*49px minmax\(0, 1fr\)/s);
assert.match(css, /\.providersLayout\.providerListCollapsed\s*\{[^}]*grid-template-columns:\s*49px minmax\(0, 1fr\)/s);
assert.match(css, /\.settingsNavCollapsed\s+\.railItemLabel\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.providerListCollapsed\s+\.providerLabel[\s\S]*display:\s*none/s);
assert.match(css, /\.providerListCollapsed\s+\.providersStickyHeader[\s\S]*\.providerItem/s);
assert.match(css, /@media \(max-width:\s*900px\)\s*\{\s*\.view\s*\{\s*padding-left:\s*49px;/s);

console.log("settings collapsible-column checks passed");
