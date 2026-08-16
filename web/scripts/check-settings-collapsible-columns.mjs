import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const layout = source("components/settings/settings-tabs-layout.tsx");
const providers = source("components/settings/providers/index.tsx");
const providerItem = source("components/settings/providers/provider-item.tsx");
const memory = source("components/settings/memory-settings.tsx");
const loading = source("app/(shell)/settings/loading.tsx");
const detail = source("components/settings/providers/detail.tsx");
const modelList = source("components/settings/providers/model-list.tsx");
const css = source("components/settings/settings-page.module.css");

assert.match(layout, /localStorage\.getItem\("settingsNavOpen"\)/);
assert.match(layout, /localStorage\.setItem\("settingsNavOpen",/);
assert.match(layout, /aria-expanded=\{navOpen\}/);
assert.match(layout, /styles\.settingsNavCollapsed/);
assert.match(layout, /styles\.railItemIcon/);
assert.match(layout, /styles\.railItemLabel/);
assert.match(layout, /styles\.railHeader[\s\S]*styles\.railTitle[\s\S]*settings\.title[\s\S]*sidebarToggleClass/);
assert.doesNotMatch(layout, /styles\.topbar/);

const tabsBlock = layout.match(/const tabs = \[([\s\S]*?)\n  \];/)?.[1] ?? "";
const tabOrder = [...tabsBlock.matchAll(/id: "([^"]+)"/g)].map((match) => match[1]);
assert.deepEqual(tabOrder, ["general", "providers", "memory", "search", "browser", "channels", "usage", "system"]);

assert.match(providers, /localStorage\.getItem\("providerListOpen"\)/);
assert.match(providers, /localStorage\.setItem\("providerListOpen",/);
assert.match(providers, /aria-expanded=\{listOpen\}/);
assert.match(providers, /styles\.providerListCollapsed/);
assert.match(providers, /styles\.providersToolbar[\s\S]*SearchInput[\s\S]*sidebarToggleClass/);
assert.doesNotMatch(providers, /styles\.railHeader/);
assert.doesNotMatch(providers, /styles\.railTitle/);
assert.match(providers, /styles\.pageHeader[\s\S]*styles\.pageTitle[\s\S]*settings\.tab\.providers[\s\S]*styles\.pageMeta[\s\S]*styles\.pageBody/);
assert.doesNotMatch(memory, /shared\.pageHeader/);
assert.match(loading, /const headerless = tab === "memory"/);
assert.match(loading, /providers: "settings\.tab\.providers"/);
assert.match(loading, /\{!headerless[\s\S]*styles\.pageHeader/);
assert.match(providerItem, /title=\{p\.label\}/);

assert.match(css, /\.body\.settingsNavCollapsed\s*\{[^}]*grid-template-columns:\s*49px minmax\(0, 1fr\)/s);
assert.match(css, /\.railHeader\s*\{[^}]*height:\s*32px/s);
assert.match(css, /\.railItems\s*\{[^}]*margin-top:\s*15px/s);
assert.match(css, /\.providersLayout\.providerListCollapsed\s*\{[^}]*grid-template-columns:\s*49px minmax\(0, 1fr\)/s);
assert.match(css, /\.providersLayout\s*\{[^}]*grid-template-columns:\s*min\(calc\(var\(--sidebar-width\) - 1px\),\s*42%\) minmax\(0,\s*1fr\)/s);
assert.match(css, /\.settingsNavCollapsed\s+\.railItemLabel\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.settingsNavCollapsed\s+\.nav\s*>\s*\.railHeader\s+\.railTitle\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.providerListCollapsed\s+\.providerLabel[\s\S]*display:\s*none/s);
assert.match(css, /\.providersToolbar\s*\{[^}]*display:\s*flex/s);
assert.doesNotMatch(css, /\.providerListCollapsed\s+\.railTitle/);
assert.match(css, /\.providerListCollapsed\s+\.providersStickyHeader[\s\S]*\.providerItem/s);
assert.match(css, /\.detail\s*\{[^}]*min-width:\s*0[^}]*container-type:\s*inline-size/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)/);
assert.match(css, /@container[\s\S]*\.detailTitle\s*\{[^}]*overflow-wrap:\s*anywhere/s);
assert.match(css, /@container[\s\S]*\.detailSectionTitle\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container[\s\S]*\.detailRow\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container[\s\S]*\.acctRow\s*\{[^}]*grid-template-areas:[^}]*"drag content content status"[^}]*"\. validate active remove"/s);
assert.match(css, /@container[\s\S]*\.acctContent\s*\{[^}]*flex-direction:\s*column/s);
assert.match(css, /\.modelActions\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(detail, /styles\.detailRow[\s\S]*model id/);
assert.match(detail, /styles\.detailHeaderActions[\s\S]*Switch[\s\S]*provider\.custom/);
assert.match(modelList, /className=\{styles\.modelRowHeader\}/);
assert.match(modelList, /className=\{styles\.modelFact\}/);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*"\. validate"[\s\S]*"\. active"[\s\S]*"\. remove"/s);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*\.acctKey\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*\.acctKey\s*>\s*input\s*\{[^}]*flex:\s*1 1 100%[^}]*width:\s*100%/s);
assert.match(css, /\.modelCapabilities\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)[\s\S]*\.modelFact\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)[\s\S]*\.detailHeader\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*40px minmax\(0,\s*1fr\)/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)[\s\S]*\.detailHeaderActions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@media \(max-width:\s*900px\)\s*\{\s*\.view\s*\{\s*padding-left:\s*49px;/s);

console.log("settings collapsible-column checks passed");
