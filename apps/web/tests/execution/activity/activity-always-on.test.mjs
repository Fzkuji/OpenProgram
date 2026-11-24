// Always-on is a setting of the conversation's Agent, not an agent of its
// own: the Activity overview has no Always-on group; the chart's first row
// says when it wakes next, and the main agent's page holds the details in one row.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const panel = readFileSync(new URL("../../../components/right-sidebar/running/running-panel.tsx", import.meta.url), "utf8");
const agentPage = readFileSync(new URL("../../../components/right-sidebar/running/pages/agent-page.tsx", import.meta.url), "utf8");

test("the overview lists no separate Always-on group", () => {
  assert.doesNotMatch(panel, /section\("always-on"/);
});

test("the next wake is the chart's first row, on the checked-out branch, and opens the main agent's page", () => {
  assert.match(panel, /nextWake=\{nextWakeOf\(ongoing, currentScope\.active, text\)\}/);
  assert.match(panel, /onOpenNextWake=\{\(\) => open\(\{ kind: "main", id: currentScope\.key \}\)\}/);
  // Before the first run there is no chart: the main agent's one row carries the always-on line.
  assert.match(panel, /const always = alwaysOn \? alwaysStatus\(\) : null;/);
  assert.match(panel, /\{!hasChart && chartRead \? mainRow\(\) : null\}/);
});

test("the agent page opens the always-on row from the chart's next wake", () => {
  assert.match(agentPage, /<AlwaysOnRow context=\{context\} scope=\{scope\} \/>/);
  assert.match(agentPage, /onOpenNextWake=\{\(\) => setAlwaysOnOpen\(true\)\}/);
});

test("the next wake says first what holds the agent back: an interrupted check-in stands out, quiet hours are named", async () => {
  const { installLoader } = await import("../../_support/load.mjs");
  installLoader();
  const { nextWakeOf } = await import("../../../components/right-sidebar/running/running-format.ts");
  const text = english => english;
  const work = extra => ({ always_on: { enabled: true, next_wake_at: 1_800_000_000, config: { wake_minutes: 60 }, ...extra } });
  assert.deepEqual(nextWakeOf(work({}), true, text), { at: 1_800_000_000, line: "Always-on · hourly", strong: false });
  assert.deepEqual(nextWakeOf(work({ last_wake: { outcome: "interrupted" } }), true, text), { at: 1_800_000_000, line: "Interrupted · hourly", strong: true });
  assert.equal(nextWakeOf(work({ last_wake: { outcome: "budget" }, quiet_now: true }), true, text).line, "Paused: today's budget is used · Quiet hours · hourly");
  // Only the branch the reader is on has check-ins.
  assert.equal(nextWakeOf(work({}), false, text), null);
  assert.equal(nextWakeOf({ always_on: { enabled: false } }, true, text), null);
});
