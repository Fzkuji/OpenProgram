import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/agents/agents-page.tsx", root), "utf8");
const sidebar = readFileSync(new URL("components/sidebar/sidebar.tsx", root), "utf8");
const sender = readFileSync(new URL("components/chat/composer/legacy-send.ts", root), "utf8");

assert.match(page, /fetch\("\/api\/agents"/);
assert.match(page, /fetch\(`\/api\/agents\/\$\{encodeURIComponent\(selectedAgent\.id\)\}`[\s\S]*method:\s*"PATCH"/);
assert.match(page, /mode:\s*"automatic"/);
assert.match(page, /mode:\s*"selected"/);
assert.match(page, /mode:\s*"none"/);
assert.match(page, /Access preset/);
assert.match(page, /Functions[\s\S]*Agentic Functions[\s\S]*Applications/);
assert.match(sidebar, /href="\/agents"[\s\S]*nav\.agents/);
assert.match(sender, /toolsProfile\s*!==\s*"__agent__"[\s\S]*payload\.tools_profile\s*=\s*toolsProfile/);

console.log("agent tool configuration checks passed");
