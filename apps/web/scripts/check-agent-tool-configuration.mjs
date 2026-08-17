import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { groupTools } from "../components/functions/tool-groups.ts";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/agents/agents-page.tsx", root), "utf8");
const pageStyles = readFileSync(new URL("components/agents/agents-page.module.css", root), "utf8");
const sidebar = readFileSync(new URL("components/sidebar/sidebar.tsx", root), "utf8");
const primaryNav = readFileSync(new URL("components/sidebar/sidebar-primary-nav.tsx", root), "utf8");
const sender = readFileSync(new URL("components/chat/composer/submit/send-chat-message.ts", root), "utf8");
const route = readFileSync(new URL("../server/openprogram_server/_webui/routes/tree.py", root), "utf8");
const manageHeaderMarkup = page.match(/<ManagePageHeader[\s\S]*?\/>/)?.[0];
const fontDeclarations = pageStyles.match(/\bfont(?:-size)?\s*:[^;}]+/g) ?? [];

assert.match(page, /fetch\("\/api\/agents"/);
assert.match(page, /fetch\(`\/api\/agents\/\$\{encodeURIComponent\(draft\.id\)\}`[\s\S]*method:\s*"PATCH"/);
assert.match(page, /:\s*"\/api\/agents"[\s\S]*fetch\(url,\s*\{[\s\S]*?method:\s*"POST"/);
assert.match(page, /\/default`[\s\S]*method:\s*"POST"/);
assert.match(page, /method:\s*"DELETE"/);
assert.match(page, /Overview[\s\S]*Model & Instructions[\s\S]*Programs[\s\S]*Skills[\s\S]*MCP[\s\S]*Sessions/);
assert.match(page, /mode:\s*"automatic"\s*\|\s*"selected"\s*\|\s*"none"/);
assert.match(page, /Access preset/);
assert.match(page, /Functions[\s\S]*Connected Services[\s\S]*Agentic Functions[\s\S]*Applications/);
assert.match(page, /Browse programs/);
assert.match(page, /fetch\("\/api\/skills"/);
assert.match(page, /fetch\("\/api\/mcp\/servers"/);
assert.match(page, /async function openPicker[\s\S]*Promise\.all\([\s\S]*fetch\("\/api\/programs"/);
assert.match(page, /ManagePageHeader,\s*ManageRow,\s*managePageStyles/);
assert.match(page, /settings-page\.module\.css/);
assert.ok(manageHeaderMarkup, "Agents must reuse ManagePageHeader");
assert.doesNotMatch(manageHeaderMarkup, /\btabs=/, "Agent configuration tabs belong below the selected Agent header");
assert.match(page, /from "@\/components\/ui\/tabs"/);
assert.match(page, /<Tabs[\s\S]*value=\{tab\}[\s\S]*onValueChange/);
assert.match(page, /styles\.agentPageHeader[\s\S]*<TabsList/);
assert.match(page, /className=\{managePageStyles\.splitBody\}/);
assert.match(page, /<ManageRow[\s\S]*styles\.agentSelected/);
assert.doesNotMatch(page, /styles\.(?:header|layout|agentRail|detailHeader|tabs)\b/);
assert.doesNotMatch(pageStyles, /\.(?:header|layout|agentRail|detailHeader|tabs)\s*\{/);
assert.ok(
  fontDeclarations.every((declaration) =>
    /^font-size:var\(--fs-(?:sm|base|md|lg)\)$/.test(declaration)
    || declaration === "font:var(--fs-sm) var(--font-mono)"),
  `Agents typography must use the shared scale, found: ${fontDeclarations.join(", ")}`,
);
assert.match(pageStyles, /\.configTab\{[^}]*font-size:var\(--fs-base\)/);
assert.match(pageStyles, /\.formGrid label[^}]*font-size:var\(--fs-base\)/);
assert.match(primaryNav, /href:\s*"\/agents"[\s\S]*nav\.agents/);
assert.match(sender, /toolsProfile\s*!==\s*"__agent__"[\s\S]*payload\.tools_profile\s*=\s*toolsProfile/);
assert.match(route, /_mcp_server[\s\S]*"source": "mcp" if mcp_server else "builtin"/);

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
