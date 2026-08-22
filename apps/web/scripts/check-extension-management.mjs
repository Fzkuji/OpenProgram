import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { jsonFetch, HttpError } from "../lib/net/fetch-client.ts";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [capabilities, manage, plugins, skills, mcp, mcpCatalog, pluginStore, fetchClient] = await Promise.all([
  read("../components/capabilities/capabilities-page.tsx"),
  read("../components/ui/manage-page.tsx"),
  read("../components/plugins/plugins-page.tsx"),
  read("../components/skills/skills-page.tsx"),
  read("../components/mcp/mcp-page.tsx"),
  read("../components/mcp/mcp-catalog-panel.tsx"),
  read("../lib/state/plugins-store.ts"),
  read("../lib/net/fetch-client.ts"),
]);

assert.match(manage, /summary\?: ReactNode/, "the shared subnav must accept an availability summary");
assert.match(manage, /onKeyDown=\{\(event\) => moveTab\(event, index\)\}/, "shared subnav tabs must support keyboard navigation");
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
assert.match(mcpCatalog, /min-w-0 flex-col[\s\S]*break-all[\s\S]*self-end sm:self-auto/, "catalog rows must keep actions visible at narrow widths");
assert.match(capabilities, /text\("Add plugin", "添加插件"\)/);
assert.match(capabilities, /text\("Add skill", "添加技能"\)/);
assert.match(capabilities, /text\("Add MCP server", "添加 MCP 服务器"\)/);
assert.match(pluginStore, /loadError: string \| null/, "plugin list failures must be visible state");
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
