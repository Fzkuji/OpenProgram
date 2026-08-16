import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/agents/agents-page.tsx", root), "utf8");
const pageStyles = readFileSync(new URL("components/agents/agents-page.module.css", root), "utf8");
const sidebar = readFileSync(new URL("components/sidebar/sidebar.tsx", root), "utf8");
const sender = readFileSync(new URL("components/chat/composer/legacy-send.ts", root), "utf8");

assert.match(page, /fetch\("\/api\/agents"/);
assert.match(page, /fetch\(`\/api\/agents\/\$\{encodeURIComponent\(draft\.id\)\}`[\s\S]*method:\s*"PATCH"/);
assert.match(page, /:\s*"\/api\/agents"[\s\S]*fetch\(url,\s*\{[\s\S]*?method:\s*"POST"/);
assert.match(page, /\/default`[\s\S]*method:\s*"POST"/);
assert.match(page, /method:\s*"DELETE"/);
assert.match(page, /Overview[\s\S]*Model & Instructions[\s\S]*Programs[\s\S]*Skills[\s\S]*MCP[\s\S]*Sessions/);
assert.match(page, /mode:\s*"automatic"\s*\|\s*"selected"\s*\|\s*"none"/);
assert.match(page, /Access preset/);
assert.match(page, /Functions[\s\S]*Agentic Functions[\s\S]*Applications/);
assert.match(page, /Browse programs/);
assert.match(page, /fetch\("\/api\/skills"/);
assert.match(page, /fetch\("\/api\/mcp\/servers"/);
assert.match(page, /async function openPicker[\s\S]*Promise\.all\([\s\S]*fetch\("\/api\/programs"/);
assert.match(page, /ManagePageHeader,\s*ManageRow,\s*managePageStyles/);
assert.match(page, /settings-page\.module\.css/);
assert.match(page, /<ManagePageHeader[\s\S]*tabs=\{TABS\.map/);
assert.match(page, /className=\{managePageStyles\.splitBody\}/);
assert.match(page, /<ManageRow[\s\S]*styles\.agentSelected/);
assert.doesNotMatch(page, /styles\.(?:header|layout|agentRail|detailHeader|tabs)\b/);
assert.doesNotMatch(pageStyles, /\.(?:header|layout|agentRail|detailHeader|tabs)\s*\{/);
assert.match(sidebar, /href="\/agents"[\s\S]*nav\.agents/);
assert.match(sender, /toolsProfile\s*!==\s*"__agent__"[\s\S]*payload\.tools_profile\s*=\s*toolsProfile/);

console.log("agent tool configuration checks passed");
