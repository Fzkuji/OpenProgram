import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { groupTools } from "../components/functions/tool-groups.ts";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/agents/agents-page.tsx", root), "utf8");
const sidebar = readFileSync(new URL("components/sidebar/sidebar.tsx", root), "utf8");
const sender = readFileSync(new URL("components/chat/composer/legacy-send.ts", root), "utf8");
const route = readFileSync(new URL("../openprogram/webui/routes/tree.py", root), "utf8");

assert.match(page, /fetch\("\/api\/agents"/);
assert.match(page, /fetch\(`\/api\/agents\/\$\{encodeURIComponent\(selectedAgent\.id\)\}`[\s\S]*method:\s*"PATCH"/);
assert.match(page, /mode:\s*"automatic"/);
assert.match(page, /mode:\s*"selected"/);
assert.match(page, /mode:\s*"none"/);
assert.match(page, /Access preset/);
assert.match(page, /Programs available/);
assert.match(page, /All Programs/);
for (const label of ["Built-in Functions", "Connected Services", "Agentic Functions", "Applications"]) {
  assert.match(page, new RegExp(label));
}
assert.match(route, /_mcp_server[\s\S]*"source": "mcp" if mcp_server else "builtin"/);
assert.match(sidebar, /href="\/agents"[\s\S]*nav\.agents/);
assert.match(sender, /toolsProfile\s*!==\s*"__agent__"[\s\S]*payload\.tools_profile\s*=\s*toolsProfile/);

const rows = [
  { name: "read", group: "file", source: "builtin" },
  { name: "linear__get_issue", group: "connected", source: "mcp", server: "linear" },
  { name: "linear__save_issue", group: "connected", source: "mcp", server: "linear" },
  { name: "drawio__set_page", group: "connected", source: "mcp", server: "drawio" },
];
const grouped = groupTools(rows.filter((tool) => tool.source === "mcp"), "server");
assert.deepEqual(grouped.map((group) => [group.name, group.items.length]), [
  ["drawio", 1],
  ["linear", 2],
]);

console.log("agent tool configuration checks passed");
