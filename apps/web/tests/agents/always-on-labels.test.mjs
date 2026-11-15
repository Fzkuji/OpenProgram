import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/chat/transcript/channel-speaker") {
      return { url: stub(`export const channelName = (channel) => channel;`), shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const labels = await import("../../components/agents/always-on-labels.ts");
const en = (english) => english;
const zh = (_english, chinese) => chinese;

test("the new wake outcomes read in plain words in both languages", () => {
  assert.equal(labels.outcomeLabel("nothing_new", en), "Checked, nothing new");
  assert.equal(labels.outcomeLabel("skipped", en), "Skipped, nothing to check");
  assert.equal(labels.outcomeLabel("budget", en), "Paused: today's budget is used");
  assert.equal(labels.outcomeLabel("interrupted", en), "Interrupted");
  assert.equal(labels.outcomeLabel("reported", en), "Checked and reported");
  assert.equal(labels.outcomeLabel("check_in", en), "Checked in", "old state still reads");
  for (const outcome of ["nothing_new", "skipped", "budget", "interrupted", "reported"]) {
    assert.match(labels.outcomeLabel(outcome, zh), /[一-鿿]/);
  }
});

test("report kinds have a label, a tag and help; reports without a kind are findings", () => {
  assert.equal(labels.reportKindLabel("approval", en), "Needs your decision");
  assert.equal(labels.reportKindLabel("done", en), "Finished work");
  assert.equal(labels.reportKindLabel("finding", en), "Findings");
  assert.equal(labels.reportKindTag(undefined, en), "Finding");
  assert.equal(labels.reportKindTag("approval", zh), "待决定");
  assert.ok(labels.reportKindHelp("done", en));
});

test("costs and today's spending read as short amounts", () => {
  assert.equal(labels.usdLabel(0.0042), "$0.0042");
  assert.equal(labels.usdLabel(3.5), "$3.50");
  assert.equal(labels.usdLabel(0), "$0.00");
  assert.equal(labels.wakeCostLabel({ input_tokens: 12300, output_tokens: 950, usd: 0.12 }, en), "$0.12 · 12.3k in, 950 out");
  assert.equal(labels.wakeCostLabel({ usd: null }, en), "");
  assert.equal(labels.wakeCostLabel({ input_tokens: 2000, output_tokens: 10, usd: null }, en), "2.0k in, 10 out · price unknown, not counted in today's spend");
  assert.equal(labels.spendLabel(0.4, 2, en), "$0.40 of $2.00");
  assert.equal(labels.spendLabel(0.4, null, en), "$0.40 (no budget)");
});

test("the alive line answers from the wake log in both languages", () => {
  const now = 100000;
  const health = { last_at: now - 720, wakes_today: 6, failed_today: 0, failed_in_a_row: 0 };
  assert.equal(labels.aliveLabel(health, now + 600, false, now, en), "Yes: last woke 12 min ago · 6 in 24 hours, none failed");
  assert.equal(labels.aliveLabel({ ...health, failed_today: 1 }, now + 600, false, now, zh), "是：上次唤醒在12 分钟前 · 24 小时内 6 次，1 次失败");
  assert.equal(labels.aliveLabel(null, now + 600, false, now, en), "No wake yet");
  assert.equal(labels.aliveLabel(health, now - 60, true, now, en), "Yes: working now");
  assert.equal(labels.aliveLabel({ ...health, failed_in_a_row: 3 }, now + 600, false, now, en), "Failing: the last 3 wakes all failed");
  assert.equal(labels.outcomeLabel("failed", en), "The wake failed");
});

test("a wake long past due reads as stopped, and a tick's lateness does not", () => {
  const now = 100000;
  const health = { last_at: now - 3600, wakes_today: 2, failed_today: 0, failed_in_a_row: 0 };
  assert.equal(labels.aliveLabel(health, now - 7200, false, now, en), "Looks stopped: a wake was due 2 h ago and has not happened");
  assert.match(labels.aliveLabel(health, now - 60, false, now, en), /^Yes: /);
});
