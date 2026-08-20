import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const layout = source("components/settings/settings-tabs-layout.tsx");
const settingsHome = source("app/(shell)/settings/page.tsx");
const userMenu = source("components/user-menu-footer.tsx");
const mainMenu = source("components/center-tabs/main-menu.tsx");
const agentSelector = source("components/chat/top-bar/agent-selector.tsx");
const appShell = source("components/app-shell.tsx");
const providers = source("components/settings/providers/index.tsx");
const providerItem = source("components/settings/providers/provider-item.tsx");
const memory = source("components/settings/memory-settings.tsx");
const memoryCss = source("components/settings/memory-settings.module.css");
const usageCss = source("components/settings/token-usage/usage.module.css");
const usagePage = source("components/settings/token-usage/index.tsx");
const channelsCss = source("components/settings/channels/channels.module.css");
const channelsPage = source("components/settings/channels/index.tsx");
const system = source("components/settings/system-settings.tsx");
const general = source("components/settings/general-section.tsx");
const browser = source("components/settings/browser-settings.tsx");
const loading = source("app/(shell)/settings/loading.tsx");
const detail = source("components/settings/providers/detail.tsx");
const searchDetail = source("components/settings/search-providers/detail.tsx");
const searchIndex = source("components/settings/search-providers/index.tsx");
const addCustom = source("components/settings/providers/add-custom-provider.tsx");
const accountManager = source("components/settings/providers/account-manager.tsx");
const modelList = source("components/settings/providers/model-list.tsx");
const sidebar = source("components/sidebar/sidebar.tsx");
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

// Settings home is General. Client entries must not land on /settings
// (desktop often skips the server redirect, and the unmatched-path
// fallback used to treat that as Providers).
assert.match(settingsHome, /redirect\("\/settings\/general"\)/);
assert.match(layout, /pathname\.startsWith\("\/settings\/providers"\)\) return "providers"/);
assert.match(layout, /return "providers";\s*return "general";/);
assert.match(userMenu, /router\.push\("\/settings\/general"\)/);
assert.doesNotMatch(userMenu, /router\.push\("\/settings"\)/);
assert.match(mainMenu, /router\.push\("\/settings\/general"\)/);
assert.doesNotMatch(mainMenu, /router\.push\("\/settings"\)/);
assert.match(agentSelector, /href="\/settings\/general"/);
assert.doesNotMatch(agentSelector, /href="\/settings"/);
assert.match(loading, /pathname\.split\("\/"\)\[2\] \|\| "general"/);
const warmRoutes = appShell.match(/const WARM_ROUTES = \[([\s\S]*?)\];/)?.[1] ?? "";
assert.match(warmRoutes, /"\/settings\/general"/);
assert.ok(
  warmRoutes.indexOf('"/settings/general"') < warmRoutes.indexOf('"/settings/providers"'),
  "prefetch Settings home (General) before Providers",
);

// Providers list is always expanded. Search fills the sidebar header;
// Settings nav collapse (settingsNavOpen) is the only Settings rail toggle.
assert.doesNotMatch(providers, /providerListOpen/);
assert.doesNotMatch(providers, /listOpen/);
assert.doesNotMatch(providers, /toggleList/);
assert.doesNotMatch(providers, /styles\.providerListCollapsed/);
assert.doesNotMatch(providers, /sidebarToggleClass/);
assert.doesNotMatch(providers, /Collapse provider list|Expand provider list/);
assert.doesNotMatch(providers, /PanelLeftCloseIcon|PanelLeftOpenIcon/);
assert.match(providers, /styles\.providersToolbar[\s\S]*styles\.providerSearch[\s\S]*SearchInput/);
assert.match(providers, /AddCustomProvider/);
assert.match(addCustom, /styles\.addCustomTrigger/);
assert.match(addCustom, /styles\.addCustomForm/);
assert.doesNotMatch(addCustom, /calc\(100%\s*-\s*16px\)/);
assert.doesNotMatch(addCustom, /margin:\s*["']8px 8px 0["']/);
assert.doesNotMatch(addCustom, /margin:\s*["']8px["']/);
assert.doesNotMatch(addCustom, /width:\s*["']calc/);
assert.match(css, /\.addCustomTrigger\s*,\s*\.addCustomForm\s*\{[^}]*width:\s*100%/s);
assert.match(css, /\.addCustomForm input\s*\{[^}]*width:\s*100%/s);
assert.doesNotMatch(css, /\.addCustom(?:Trigger|Form)\s*(?:,\s*\.addCustom(?:Trigger|Form)\s*)?\{[^}]*margin(?:-left|-right)?:\s*8px/s);
assert.doesNotMatch(css, /\.addCustom(?:Trigger|Form)\s*(?:,\s*\.addCustom(?:Trigger|Form)\s*)?\{[^}]*padding(?:-left|-right)?:\s*(?:8|10)px/s);
assert.match(providers, /styles\.providersGroupLabel[\s\S]*Enabled/);
assert.match(providers, /styles\.providersGroupLabel[\s\S]*Not enabled/);
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
assert.match(css, /\.railTitle\s*\{[^}]*font-size:\s*18px[^}]*line-height:\s*1\.2(?:;|\s)/s);
assert.match(css, /\.pageTitle\s*\{[^}]*font-size:\s*18px[^}]*overflow-wrap:\s*anywhere/s);
assert.match(sidebar, /className="text-\[20px\] font-bold tracking-\[-0\.01em\] whitespace-nowrap"/);
assert.match(css, /\.railItems\s*\{[^}]*margin-top:\s*15px/s);
assert.doesNotMatch(css, /\.providerListCollapsed/);
assert.match(css, /\.providersLayout\s*\{[^}]*grid-template-columns:\s*min\(calc\(var\(--sidebar-width\) - 1px\),\s*42%\) minmax\(0,\s*1fr\)/s);
assert.match(css, /\.settingsNavCollapsed\s+\.railItemLabel\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.settingsNavCollapsed\s+\.nav\s*>\s*\.railHeader\s+\.railTitle\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.providersToolbar\s*\{[^}]*display:\s*flex[^}]*width:\s*100%/s);
assert.match(css, /\.providerSearch\s*\{[^}]*width:\s*100%/s);
assert.match(css, /\.detail\s*\{[^}]*min-width:\s*0[^}]*container-type:\s*inline-size/s);
assert.match(detail, /className=\{styles\.detailSurface\}/);
assert.match(searchDetail, /className=\{styles\.detailSurface\}/);
assert.match(searchDetail, /styles\.detailHeader/);
assert.match(searchDetail, /styles\.detailTitle\b/);
assert.equal(
  (searchDetail.match(/styles\.detailSurface/g) || []).length,
  1,
  "SearchProviderDetail owns exactly one detailSurface",
);
assert.match(searchIndex, /styles\.detailSurface[\s\S]*styles\.detailEmpty/);
assert.match(searchIndex, /styles\.providerListItems/);
assert.match(searchIndex, /styles\.providersToolbar[\s\S]*styles\.providerSearch[\s\S]*SearchInput/);
assert.doesNotMatch(
  searchIndex,
  /selected \?[\s\S]*styles\.detailSurface[\s\S]*SearchProviderDetail/,
);
assert.doesNotMatch(searchDetail, /rounded-md p-3 text-\[14px\]/);
assert.doesNotMatch(searchDetail, /fontSize:\s*12/);
assert.doesNotMatch(searchDetail, /className="flex items-center gap-3"/);
assert.match(searchDetail, /styles\.searchDescription/);
assert.match(searchDetail, /styles\.detailStatus/);
assert.match(searchDetail, /styles\.codeName/);
assert.match(css, /@container\s*\(max-width:\s*680px\)/);
assert.match(css, /@container[\s\S]*\.detailTitle\s*\{[^}]*overflow-wrap:\s*anywhere/s);
assert.match(css, /@container[\s\S]*\.detailSectionTitle\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container[\s\S]*\.detailRow\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /\.modelActions\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(detail, /styles\.detailRow[\s\S]*model id/);
assert.match(detail, /styles\.detailHeaderActions[\s\S]*Switch[\s\S]*provider\.custom/);
assert.match(detail, /styles\.providerEnabledControl[\s\S]*aria-label=\{text\("Enable this provider"/);
assert.match(accountManager, /className=\{styles\.acctCardHeader\}/);
assert.match(accountManager, /styles\.acctStatusButton/);
assert.match(accountManager, /className=\{styles\.acctUseRow\}/);
assert.match(accountManager, /className=\{styles\.addCredentialTrigger\}/);
assert.match(accountManager, /const \[addingKey, setAddingKey\] = useState\(false\)/);
assert.match(accountManager, /const \[newKeyName, setNewKeyName\] = useState\(""\)/);
assert.match(accountManager, /aria-expanded=\{addingKey\}/);
assert.match(accountManager, /accounts\.length === 0 \|\| addingKey/);
assert.match(accountManager, /onClick=\{toggleAddingKey\}/);
assert.match(accountManager, /function toggleAddingKey\(\)[\s\S]*if \(addingKey\)[\s\S]*setNewKey\(""\)[\s\S]*setNewKeyName\(""\)/);
assert.match(accountManager, /name: newKeyName\.trim\(\)/);
assert.match(accountManager, /className=\{styles\.addCredentialCard\}/);
assert.match(accountManager, /styles\.addCredentialFieldsSingle/);
assert.doesNotMatch(accountManager, /className=\{styles\.acctCellBtn\}/);
assert.match(accountManager, /aria-label=\{text\("Account name", "账号名称"\)\}/);
assert.match(accountManager, /const validateLabel = `\$\{statusText\}\. \$\{text\("Validate account", "验证账号"\)\}`/);
assert.match(accountManager, /styles\.acctStatusButton[\s\S]*onClick=\{validate\}[\s\S]*aria-label=\{validateLabel\}/);
assert.match(accountManager, /title=\{text\("Rename", "重命名"\)\}[\s\S]{0,180}aria-label=\{text\("Rename account", "重命名账号"\)\}/);
assert.match(accountManager, /title=\{text\("Replace API key", "替换 API 密钥"\)\}[\s\S]{0,180}aria-label=\{text\("Replace API key", "替换 API 密钥"\)\}/);
assert.match(accountManager, /title=\{text\("Remove account", "删除账号"\)\}[\s\S]{0,180}aria-label=\{text\("Remove account", "删除账号"\)\}/);
assert.match(css, /\.acctRow\s*\{[^}]*border:\s*1px solid var\(--border\)[^}]*background:\s*var\(--bg-input\)/s);
assert.match(css, /\.acctCardHeader\s*\{[^}]*display:\s*flex/s);
assert.match(css, /\.acctStatusButton\s*\{[^}]*display:\s*inline-flex/s);
assert.match(css, /\.iconBtn\s*\{[^}]*width:\s*28px[^}]*height:\s*28px[^}]*flex:\s*0 0 28px[^}]*border-radius:\s*6px/s);
assert.match(css, /\.acctStatusButton\s*\{[^}]*height:\s*28px[^}]*border:\s*1px solid transparent[^}]*padding:\s*0 8px[^}]*border-radius:\s*6px/s);
assert.match(css, /\.iconBtn svg,\s*\.acctStatusButton svg\s*\{[^}]*width:\s*14px[^}]*height:\s*14px[^}]*flex:\s*0 0 14px/s);
assert.match(css, /\.acctUseRow\s*\{[^}]*border-top:\s*1px solid var\(--border\)/s);
assert.match(css, /\.addCredentialTrigger\s*\{[^}]*border:\s*1px dashed var\(--border\)/s);
assert.match(css, /\.addCredentialFieldsSingle\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)[\s\S]*\.detailSurface\s*\{[^}]*padding-left:\s*12px[^}]*padding-right:\s*12px/s);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*\.detailSurface\s*\{[^}]*padding-left:\s*8px[^}]*padding-right:\s*8px/s);
assert.match(modelList, /className=\{styles\.modelRowHeader\}/);
assert.match(modelList, /className=\{styles\.modelFact\}/);
assert.match(modelList, /className=\{styles\.modelToggle\}[\s\S]*onClick=\{\(e\) => e\.stopPropagation\(\)\}/);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*\.acctKey\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container\s*\(max-width:\s*300px\)[\s\S]*\.acctKey\s*>\s*input\s*\{[^}]*flex:\s*1 1 100%[^}]*width:\s*100%/s);
assert.match(css, /\.modelCapabilities\s*\{[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)\s*\{\s*\.modelCapabilities:empty\s*\{[^}]*display:\s*none/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)[\s\S]*\.modelRowHeader\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*14px 20px minmax\(0,\s*1fr\) auto[^}]*column-gap:\s*8px[^}]*row-gap:\s*8px/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)[\s\S]*\.modelCapabilities\s*\{[^}]*grid-column:\s*3\s*\/\s*-1[^}]*grid-row:\s*2[^}]*padding-left:\s*0/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)[\s\S]*\.modelToggle\s*\{[^}]*grid-column:\s*4[^}]*grid-row:\s*1[^}]*justify-self:\s*end/s);
assert.match(css, /@container\s*\(max-width:\s*420px\)[\s\S]*\.modelFact\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)[\s\S]*\.detailHeader\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*40px minmax\(0,\s*1fr\) auto/s);
assert.match(css, /@container\s*\(max-width:\s*680px\)[\s\S]*\.detailHeaderActions\s*\{[^}]*grid-column:\s*3[^}]*flex-wrap:\s*wrap/s);
assert.match(css, /@media \(max-width:\s*900px\)\s*\{\s*\.view\s*\{\s*padding-left:\s*49px;/s);

// System Ports rows: copy stays in the left column; the input/switch
// shrink-wraps on the right. Shared `.label` is content-sized, so long
// bind-address / allowed-origins help must not wrap at full row width.
assert.match(system, /styles\.row[\s\S]*styles\.rowTop[\s\S]*styles\.systemRow/);
assert.match(system, /\{r\.help \?/);
assert.match(css, /\.systemRow\s*\{[^}]*align-items:\s*flex-start/s);
assert.match(css, /\.systemRow\s+\.label\s*\{[^}]*flex:\s*1 1 auto[^}]*min-width:\s*0/s);
assert.match(css, /\.systemRow\s+\.control\s*\{[^}]*flex:\s*0 0 auto[^}]*margin-left:\s*auto[^}]*min-width:\s*7\.5rem/s);

// Memory rows match the System contract: copy grows on the left,
// controls shrink-wrap on the right, chips stay left of the control.
assert.match(memoryCss, /\.row\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*flex-start/s);
assert.match(memoryCss, /\.rowCopy\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-width:\s*0/s);
assert.match(memoryCss, /\.controls\s*\{[^}]*flex:\s*0 0 auto;[^}]*justify-content:\s*flex-end;[^}]*min-width:\s*7\.5rem/s);
assert.doesNotMatch(memoryCss, /grid-template-columns:\s*minmax\(220px/);
assert.match(memory, /styles\.chromeValue[\s\S]{0,80}Local workspace · Git enabled/);
assert.match(memory, /styles\.monoValue[\s\S]{0,40}workspace_path/);

// Memory / Usage / Channels chrome follow the General font picker.
assert.match(memoryCss, /\.memoryPage\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(memoryCss, /\.monoValue\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
assert.match(usagePage, /styles\.page[\s\S]*local\.usagePage/);
assert.match(usageCss, /\.usagePage\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(usageCss, /\.cardValue\s*\{[^}]*font-family:\s*var\(--font-sans\)[^}]*font-variant-numeric:\s*tabular-nums/s);
assert.match(usageCss, /\.modelCell\s*\{[^}]*font-family:\s*var\(--font-mono/s);
assert.match(channelsPage, /shellStyles\.page[\s\S]*styles\.channelsPage/);
assert.match(channelsCss, /\.channelsPage\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(channelsCss, /\.rowTable code\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
assert.match(channelsCss, /\.codeBlock\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);

// Shared Settings shell: one --font-sans on `.page` so General / Providers
// / Search / Browser / System inherit the General font picker.
assert.match(css, /\.page\s*\{[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(css, /\.systemControl\s*\{[^}]*font:\s*inherit[^}]*font-family:\s*var\(--font-sans\)/s);
assert.match(system, /t\("settings\.tab\.system"\)/);
assert.match(system, /className=\{styles\.systemControl\}/);
assert.doesNotMatch(system, /const inputStyle/);

// Browser matches General / System: h2 title, Switch, systemRow isolation.
assert.match(browser, /<h2 className=\{styles\.pageTitle\}/);
assert.doesNotMatch(browser, /<h1\b/);
assert.match(browser, /styles\.systemRow/);
assert.match(browser, /<Switch[\s\S]*Browsing history/);
assert.match(browser, /<Switch[\s\S]*Cookies/);
assert.doesNotMatch(browser, /<input type="checkbox"/);
assert.match(browser, /styles\.label[\s\S]{0,80}Bookmarks are not removed/);
assert.doesNotMatch(browser, /styles\.pageMeta[\s\S]{0,80}Bookmarks are not removed/);

// Installation type is chrome, not a code value.
assert.match(general, /styles\.control\}>\{installType\}/);

console.log("settings collapsible-column checks passed");
