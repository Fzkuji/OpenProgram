// Rule: extension pages share one management grammar — every discovery page renders the shared
// ManageCatalogCard, and Add lives in each page's secondary task bar, never in the hub's top toolbar.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

assert.match(read("components/ui/manage-page.tsx"), /export function ManageCatalogCard/);
for (const discovery of [
  "components/plugins/views/marketplace-browser.tsx",
  "components/skills/discovery/catalog-list.tsx",
  "components/mcp/mcp-catalog-panel.tsx",
]) {
  assert.match(read(discovery), /<ManageCatalogCard/, `${discovery} must use the shared catalog card`);
}

const hub = read("components/capabilities/capabilities-page.tsx");
assert.doesNotMatch(hub, /Add plugin|Add skill|Add MCP server|添加插件|添加技能|添加 MCP 服务器/, "the top toolbar must contain no Add action");
for (const [page, label] of [
  ["components/plugins/plugins-page.tsx", "Add plugin"],
  ["components/skills/skills-page.tsx", "Add skill"],
  ["components/mcp/mcp-page.tsx", "Add MCP server"],
]) {
  assert.match(read(page), new RegExp(`action=\\{\\{[\\s\\S]*${label}`), `${page}: Add belongs to the secondary task bar`);
}
console.log("extension management checks passed");
