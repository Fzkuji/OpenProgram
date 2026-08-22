import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { jsonFetch, HttpError } from "../lib/net/fetch-client.ts";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [capabilities, manage, plugins, skills, mcp, pluginStore, fetchClient] = await Promise.all([
  read("../components/capabilities/capabilities-page.tsx"),
  read("../components/ui/manage-page.tsx"),
  read("../components/plugins/plugins-page.tsx"),
  read("../components/skills/skills-page.tsx"),
  read("../components/mcp/mcp-page.tsx"),
  read("../lib/state/plugins-store.ts"),
  read("../lib/net/fetch-client.ts"),
]);

assert.match(manage, /summary\?: ReactNode/, "the shared subnav must accept an availability summary");
assert.match(manage, /onKeyDown=\{\(event\) => moveTab\(event, index\)\}/, "shared subnav tabs must support keyboard navigation");
assert.match(plugins, /text\("Discover", "发现"\)/, "plugins must use the shared Discover task name");
assert.match(skills, /text\("Installed", "已安装"\)/, "skills must use the shared Installed task name");
assert.match(mcp, /id: "discover", label: text\("Discover", "发现"\)/, "MCP must expose the shared Discover task");
assert.match(mcp, /id === "discover"\) openCatalog\(\)/, "MCP Discover must open its catalog");
assert.match(capabilities, /onCatalogOpen=\{\(\) => setMcpCatalogOpen\(true\)\}/, "the controlled MCP page must wire Discover to the hub catalog state");
assert.doesNotMatch(capabilities, /Browse catalog|浏览目录/, "the hub must not restore MCP-only task wording");
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
