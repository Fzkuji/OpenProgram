import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { jsonFetch, HttpError } from "../lib/net/fetch-client.ts";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [capabilities, manage, manageCss, plugins, pluginCatalog, skills, skillsList, skillCatalog, mcp, mcpCatalog, pluginStore, fetchClient] = await Promise.all([
  read("../components/capabilities/capabilities-page.tsx"),
  read("../components/ui/manage-page.tsx"),
  read("../components/ui/manage-page.module.css"),
  read("../components/plugins/plugins-page.tsx"),
  read("../components/plugins/views/marketplace-browser.tsx"),
  read("../components/skills/skills-page.tsx"),
  read("../components/skills/skills-list.tsx"),
  read("../components/skills/discovery/catalog-list.tsx"),
  read("../components/mcp/mcp-page.tsx"),
  read("../components/mcp/mcp-catalog-panel.tsx"),
  read("../lib/state/plugins-store.ts"),
  read("../lib/net/fetch-client.ts"),
]);

assert.match(manage, /summary\?: ReactNode/, "the shared subnav must accept an availability summary");
assert.match(manage, /action\?: ManageAction/, "the secondary task bar must own the Add action");
assert.match(manage, /styles\.subnavTab/, "secondary tabs must have their own visual treatment");
assert.match(manage, /onKeyDown=\{\(event\) => moveTab\(event, index\)\}/, "shared subnav tabs must support keyboard navigation");
assert.match(capabilities, /role="tabpanel"[\s\S]*ability-panel-tab-\$\{kind\}/, "top-level ability tabs must control a named panel");
assert.match(manage, /<button type="button" className=\{styles\.rowOpen\} onClick=\{onClick\}>\{content\}<\/button>/, "management rows must separate the open button from row actions");
assert.match(plugins, /text\("Discover", "发现"\)/, "plugins must use the shared Discover task name");
assert.match(skills, /text\("Installed", "已安装"\)/, "skills must use the shared Installed task name");
assert.match(mcp, /id: "discover", label: text\("Discover", "发现"\)/, "MCP must expose the shared Discover task");
assert.match(mcp, /const \[tab, setTab\] = useState<McpTab>\("installed"\)/, "MCP tabs must own page state");
assert.match(mcp, /tab === "discover" && \(/, "MCP Discover must render inline body content");
assert.doesNotMatch(mcp, /CatalogDialog|catalogOpen|openCatalog/, "MCP Discover must not be backed by a modal");
assert.doesNotMatch(mcp, /navAddItem/, "MCP must not duplicate the top-level Add action in its installed rail");
assert.match(mcp, /<CatalogPanel[\s\S]*query=\{filterValue\}/, "the shared search query must reach MCP Discover");
assert.doesNotMatch(capabilities, /Browse catalog|浏览目录|Discover MCP servers|发现 MCP 服务器/, "the top toolbar must not duplicate Discover");
assert.doesNotMatch(capabilities, /mcpCatalogOpen|onCatalogOpen|onCatalogClose/, "the hub must not control MCP catalog modal state");
assert.match(mcpCatalog, /catalogAbort\.current\?\.abort\(\)/, "a newer catalog request must cancel the previous request");
assert.match(mcpCatalog, /setCatalog\(\{ \.\.\.data, sourceUrl: target \}\)/, "catalog provenance must bind to the response URL");
assert.match(mcpCatalog, /install\(s, catalog\.sourceUrl\)/, "catalog installs must use response-bound provenance");
assert.match(mcpCatalog, /await onInstalled\(entry\.name\)/, "install progress must include the parent refresh");
assert.match(mcpCatalog, /catalogLoading[\s\S]*installing/, "catalog loading and installation must use separate state");
assert.match(mcpCatalog, /disabled=\{catalogLoading \|\| installing !== null\}/, "catalog switching must stay disabled while an install is refreshing");
assert.doesNotMatch(mcpCatalog, /setBusy|\bbusy\b/, "catalog loading must not clear installation progress");
assert.doesNotMatch(mcpCatalog, /await fetch\(/, "catalog requests must use the shared error-aware JSON client");
assert.match(manage, /export function ManageCatalogCard/, "all discovery pages must share one catalog card");
assert.match(pluginCatalog, /<ManageCatalogCard/, "Plugin discovery must use the shared catalog card");
assert.match(skillCatalog, /<ManageCatalogCard/, "Skill discovery must use the shared catalog card");
assert.match(mcpCatalog, /<ManageCatalogCard/, "MCP discovery must use the shared catalog card");
assert.doesNotMatch(capabilities, /Add plugin|Add skill|Add MCP server|添加插件|添加技能|添加 MCP 服务器/, "the top toolbar must contain no Add action");
assert.match(plugins, /action=\{\{[\s\S]*Add plugin/, "Plugins Add belongs to the secondary task bar");
assert.match(plugins, /const issueNames = new Set\(/, "plugin issue counts must deduplicate loader and row errors");
assert.match(skills, /action=\{\{[\s\S]*Add skill/, "Skills Add belongs to the secondary task bar");
assert.match(skillCatalog, /<ManageCatalogCard/, "Skill discovery must use the shared catalog card");
assert.match(skills, /ariaLabel=\{text\("Skill sections"/, "Skill task tabs must have an accessible name");
assert.match(skillsList, /className=\{shared\.groupToggle\}/, "Skill folders must separate their toggle button from the switch");
assert.match(skillsList, /aria-label=\{skill\.enabled[\s\S]*Disable \$\{skill\.name\}/, "Skill switches must name their target");
assert.match(skillsList, /aria-label=\{allOn[\s\S]*Disable \$\{node\.segment\}/, "Skill folder switches must name their target");
assert.match(mcp, /action=\{\{[\s\S]*Add MCP server/, "MCP Add belongs to the secondary task bar");
assert.match(mcp, /<ManageRow[\s\S]*Open MCP server details/, "MCP Installed must use the shared management row");
assert.match(mcp, /<Dialog open=\{selectedServer !== null\}/, "MCP details must use the standard detail dialog");
assert.match(mcp, /detailErr && <div className=\{shared\.errorBar\} role="alert">/, "MCP detail failures must be visible inside the dialog");
assert.doesNotMatch(mcp, /shared\.splitBody|styles\.serverItem/, "MCP must not retain its old split-list grammar");
assert.match(manageCss, /\.subnavTab/, "secondary tabs must have a dedicated visual treatment");
assert.match(manageCss, /\.catalogGrid[\s\S]*\.catalogCard/, "discovery cards must share one visual system");
assert.doesNotMatch(manageCss, /\.surface\s*\{/, "extension bodies must not add a decorative outer frame");
assert.match(pluginStore, /loadError: string \| null/, "plugin list failures must be visible state");
assert.match(plugins, /loading && plugins\.length === 0[\s\S]*Loading plugins/, "Plugins must distinguish loading from an empty collection");
assert.match(skills, /loading && skills\.length === 0[\s\S]*Loading skills/, "Skills must distinguish loading from an empty collection");
assert.match(fetchClient, /d\.detail/, "shared request errors must parse FastAPI detail responses");
assert.match(mcp, /\{actionErr && <div className=\{shared\.errorBar\} role="alert">\{actionErr\}<\/div>\}/, "failed MCP requests must render an alert instead of doing nothing");
assert.match(plugins, /catch \(e\)[\s\S]*setLog/, "manual plugin install failures must stay visible in the dialog");

const previousFetch = globalThis.fetch;
globalThis.fetch = async () => new Response(JSON.stringify({ detail: "server did not start" }), {
  status: 502,
  headers: { "Content-Type": "application/json" },
});
await assert.rejects(
  jsonFetch("/api/mcp/servers/broken/restart", { method: "POST" }),
  (error) => error instanceof HttpError && error.status === 502 && error.message === "server did not start",
  "a real non-2xx extension request must preserve the actionable backend detail",
);
globalThis.fetch = previousFetch;

console.log("check-extension-management: ok");
