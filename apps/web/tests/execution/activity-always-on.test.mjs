// Always-on is a setting of the conversation's Agent, not an agent of its
// own: the Activity overview has no Always-on group; the main agent's row
// carries the always-on line and its page holds the details in one row.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const panel = readFileSync(new URL("../../components/right-sidebar/running/running-panel.tsx", import.meta.url), "utf8");
const agentPage = readFileSync(new URL("../../components/right-sidebar/running/agent-page.tsx", import.meta.url), "utf8");

test("the overview lists no separate Always-on group", () => {
  assert.doesNotMatch(panel, /section\("always-on"/);
});

test("the checked-out main agent's row carries the always-on line", () => {
  assert.match(panel, /const always = alwaysOn && scope\.active \? alwaysStatus\(\) : null;/);
  assert.match(panel, /extra=\{always && <Status/);
  // The conversation's own agent is always listed, so its row is always there to carry the line.
  assert.match(panel, /const agentRows = scopes\.map\(/);
  assert.match(panel, /agentRows\.map\(entry => scopeRow\(entry\.scope, entry\.facts, entry\.list\)\), agentsReady/);
});

test("the agent page opens the always-on row from the chart's next wake", () => {
  assert.match(agentPage, /<AlwaysOnRow context=\{context\} scope=\{scope\} \/>/);
  assert.match(agentPage, /onOpenNextWake=\{\(\) => setAlwaysOnOpen\(true\)\}/);
});
