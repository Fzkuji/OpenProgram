// What the model picker and the spent-quota card say about a provider
// (docs/reference/design/providers/provider-management.html#chat): the
// state word, the bar colour rule, the trigger mark, where a fix leads, the
// card's title and reset line, and a provider_quota frame applied live.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";

installLoader();
const S = await import("../../../lib/models/provider-state.ts");

const en = (english) => english;
const zh = (_english, chinese) => chinese;
// 2026-11-14 10:00 local time.
const NOW = new Date(2026, 10, 14, 10, 0).getTime() / 1000;
const at = (h, m = 0, dayOffset = 0) => new Date(2026, 10, 14 + dayOffset, h, m).getTime() / 1000;

const provider = (fields = {}) => ({
  id: "opencode-go", configured: true, state: "ready", until: null, fix: null,
  quota: null, balance: null, stale: false, attention: false, ...fields,
});
const quota = (left, fields = {}) => ({
  limit_key: "primary", label: "5h", left_fraction: left, resets_at: at(14, 30),
  tone: S.quotaTone(left), ring: 1 - left >= 0.5, warn: false, ...fields,
});

test("a provider that needs action says so in one word, in its tone", () => {
  assert.deepEqual(S.stateWord(provider({ state: "no_credit" }), en), { label: "No credit", tone: "danger" });
  assert.deepEqual(S.stateWord(provider({ state: "signed_out" }), en), { label: "Sign in", tone: "warning" });
  assert.deepEqual(S.stateWord(provider({ state: "invalid_key" }), en), { label: "Invalid key", tone: "danger" });
  assert.deepEqual(S.stateWord(provider({ state: "not_configured" }), zh), { label: "未配置", tone: "muted" });
  assert.deepEqual(S.stateWord(provider({ state: "rate_limited", until: at(14, 2) }), en, NOW),
    { label: "Rate limited · 14:02", tone: "warning" });
  assert.equal(S.stateWord(provider(), en), null);
  assert.equal(S.stateWord(provider({ state: "unknown" }), en), null);
  assert.equal(S.needsAction(provider({ state: "rate_limited" })), false);
  assert.equal(S.needsAction(provider({ state: "not_running" })), true);
});

test("the bar colour turns at 80 % and 95 % used, and when spent", () => {
  assert.equal(S.quotaTone(0.5), "normal");
  assert.equal(S.quotaTone(0.21), "normal");
  assert.equal(S.quotaTone(0.2), "warning");
  assert.equal(S.quotaTone(0.06), "warning");
  assert.equal(S.quotaTone(0.05), "danger");
  assert.equal(S.quotaTone(0.4, "exhausted"), "danger");
  assert.equal(S.quotaTone(null), "normal");
});

test("reset times read as a clock today, a weekday this week, a date later", () => {
  assert.equal(S.clockLabel(at(14, 30), en, NOW), "14:30");
  const tomorrow = at(9, 5, 1);
  assert.match(S.clockLabel(tomorrow, en, NOW), /^[A-Z][a-z]{2} 09:05$/);
  assert.match(S.clockLabel(tomorrow, zh, NOW), /^周. 09:05$/);
  assert.equal(S.clockLabel(at(9, 0, 20), en, NOW), "12/4");
});

test("the trigger is marked, in red, only when the provider cannot answer or its quota is nearly spent", () => {
  assert.equal(S.triggerMark(provider({ quota: quota(0.3) }), en, NOW), null);
  assert.equal(S.triggerMark(undefined, en), null);
  // A quota that only warns (80 % used, even ahead of the window) leaves it plain.
  assert.equal(S.triggerMark(provider({ attention: true, quota: quota(0.16, { warn: true }) }), en, NOW), null);
  assert.equal(S.triggerMark(provider({ attention: true, quota: quota(0.08, { warn: true }) }), en, NOW), null);
  // A temporary rate limit is a word in the menu, not a mark on the trigger.
  assert.equal(S.triggerMark(provider({ state: "rate_limited", until: at(14, 2) }), en, NOW), null);
  const spent = S.triggerMark(provider({ attention: true, quota: quota(0.02, { warn: true }) }), en, NOW);
  assert.deepEqual(spent, { tone: "danger", detail: "5h · 2% left · resets 14:30" });
  assert.deepEqual(S.triggerMark(provider({ attention: true, state: "no_credit" }), en),
    { tone: "danger", detail: "No credit" });
  assert.deepEqual(S.triggerMark(provider({ attention: true, state: "not_configured" }), en),
    { tone: "danger", detail: "Not set up" });
});

test("a fix leads to the provider's page, to Add provider when nothing is saved, or to its billing page for no credit", () => {
  assert.deepEqual(S.fixLink(provider({ state: "signed_out", fix: { action: "sign_in", url: null } })),
    { href: "/settings/providers/opencode-go", external: false });
  assert.deepEqual(S.fixLink({ id: "kimi", state: "no_credit", fix: { action: "top_up", url: "https://platform.moonshot.ai/billing" } }),
    { href: "https://platform.moonshot.ai/billing", external: true });
  assert.deepEqual(S.fixLink({ id: "kimi", state: "no_credit", fix: { action: "top_up", url: null } }),
    { href: "/settings/providers/kimi", external: false });
  assert.deepEqual(S.fixLink({ id: "deepseek", state: "not_configured", fix: { action: "connect", url: null } }),
    { href: "/settings/providers?add=deepseek", external: false });
  assert.equal(S.addProviderPage(), "/settings/providers?add=1");
});

test("balances read in their currency", () => {
  assert.equal(S.balanceLabel({ amount: 18.42, unit: "usd" }), "$18.42");
  assert.equal(S.balanceLabel({ amount: 0, unit: "CNY" }), "¥0.00");
  assert.equal(S.balanceLabel({ amount: 1200, unit: "credits" }), "1,200 credits");
  // A fractional amount is grouped too, as the Providers page writes it.
  assert.equal(S.balanceLabel({ amount: 38991.8227175, unit: "credits" }), "38,991.82 credits");
  assert.equal(S.balanceLabel({ amount: 1234.5, unit: "usd" }), "$1,234.50");
});

test("the submenu header lists windows worst first, then amounts", () => {
  const limit = (key, label, fields) => ({
    limit_key: key, label, unit: "percent", source: "headers", fetched_at: NOW, used: null, limit: null,
    remaining: null, used_fraction: null, left_fraction: null, window_minutes: 300, resets_at: null,
    status: "ok", stale: false, ...fields,
  });
  const rows = S.quotaRows([
    limit("secondary", "Week", { left_fraction: 0.71 }),
    limit("primary", "5h", { left_fraction: 0.08, resets_at: at(14, 30) }),
    limit("balance", "Balance", { unit: "usd", remaining: 18.42 }),
    limit("rpm", "Req/min", {}),
  ], en, NOW);
  assert.deepEqual(rows.map((r) => [r.label, r.pct, r.reset, r.value, r.tone]), [
    ["5h", 8, "14:30", null, "warning"], ["Week", 71, null, null, "normal"], ["Balance", null, null, "$18.42", "normal"],
  ]);
  assert.equal(rows[0].left, 0.08);
  assert.equal(rows[2].left, null);
});

test("the header lists the ring's limit and the balance before the status read arrives", () => {
  const picker = provider({
    stale: true,
    quota: quota(0.16, { label: "Weekly", resets_at: at(2, 22, 4), tone: "warning" }),
    balance: { amount: 38991.8227, unit: "credits", label: "Credits" },
  });
  // No status read yet: the ring's limit and the balance, both stale.
  assert.deepEqual(S.headerRows([], picker, en, NOW).map((r) => [r.label, r.pct, r.reset, r.value, r.stale]), [
    ["Weekly", 16, "Wed 02:22", null, true], ["Credits", null, null, "38,991.82 credits", true],
  ]);
  // Once it arrives its rows win; the ring's limit is not listed twice.
  const limit = (key, label, fields) => ({
    limit_key: key, label, unit: "percent", source: "frame", fetched_at: NOW, used: null, limit: null,
    remaining: null, used_fraction: null, left_fraction: null, window_minutes: 10080, resets_at: null,
    status: "ok", stale: false, ...fields,
  });
  const rows = S.headerRows([
    limit("primary", "Weekly", { left_fraction: 0.16, status: "warning" }),
    limit("secondary", "5h", { left_fraction: 0.5 }),
    limit("credits", "Credits", { unit: "credits", remaining: 38991.8227 }),
  ], picker, en, NOW);
  assert.deepEqual(rows.map((r) => [r.key, r.stale]), [["primary", false], ["secondary", false], ["credits", false]]);
  assert.deepEqual(S.headerRows([], null, en, NOW), []);
});

test("the header keeps the four worst windows and every amount, spent and full alike", () => {
  const limit = (key, label, fields) => ({
    limit_key: key, label, unit: "percent", source: "headers", fetched_at: NOW, used: null, limit: null,
    remaining: null, used_fraction: null, left_fraction: null, window_minutes: 300, resets_at: null,
    status: "ok", stale: false, ...fields,
  });
  const limits = [
    limit("a", "A", { left_fraction: 1 }),
    limit("b", "B", { left_fraction: 0, status: "exhausted" }),
    limit("c", "C", { left_fraction: 0.5 }),
    limit("d", "D", { left_fraction: 0.3, stale: true }),
    limit("e", "E", { left_fraction: 0.9 }),
    limit("credits", "Credits", { unit: "credits", remaining: 0, status: "exhausted" }),
    limit("unknown", "Unknown", {}),
  ];
  // The ring is drawn from the dropped best window; it is not brought back.
  const picker = provider({ quota: quota(1, { limit_key: "a", label: "A" }), balance: { amount: 5, unit: "usd", label: "Balance" } });
  for (const rows of [S.quotaRows(limits, en, NOW), S.headerRows(limits, picker, en, NOW)]) {
    assert.deepEqual(rows.map((r) => [r.key, r.pct, r.value, r.tone, r.stale]), [
      ["b", 0, null, "danger", false], ["d", 30, null, "normal", true], ["c", 50, null, "normal", false],
      ["e", 90, null, "normal", false], ["credits", null, "0 credits", "danger", false],
    ]);
  }
});

test("the spent-quota card names the provider, the limit and when it resets", () => {
  const cause = {
    cause: "quota_exhausted", http_status: 429, provider: "opencode-go", limit: "5h", resets_at: at(14, 30),
    other_limits: [{ label: "Week", left_fraction: 0.71, resets_at: null }],
  };
  assert.deepEqual(S.quotaCardText(cause, "OpenCode Go", en, { now: NOW }),
    { title: "OpenCode Go 5h limit reached", detail: "Resets 14:30 · Week 71% left" });
  assert.deepEqual(S.quotaCardText(cause, "OpenCode Go", zh, { now: NOW }),
    { title: "OpenCode Go 5h 额度已用完", detail: "14:30 重置 · Week 剩 71%" });
  const broke = { cause: "quota_exhausted", http_status: 402, provider: "kimi", limit: null, resets_at: null, other_limits: [] };
  assert.deepEqual(S.quotaCardText(broke, "Kimi", en, { picker: provider({ id: "kimi", balance: { amount: 0, unit: "cny", label: "Balance" } }) }),
    { title: "Kimi has no credit", detail: "Balance ¥0.00" });
  assert.equal(S.quotaCardText({ cause: "quota_exhausted", provider: "x" }, "X", en).title, "X quota used up");
  assert.equal(S.isQuotaCause({ cause: "invalid_key" }), false);
  assert.equal(S.isQuotaCause(null), false);
});

test("a provider_quota frame updates the picker and its quota rows without a request", () => {
  const cache = new Map();
  const client = { setQueryData: (key, fn) => cache.set(JSON.stringify(key), fn(cache.get(JSON.stringify(key)))) };
  cache.set(JSON.stringify(S.PICKER_QUERY_KEY), { models: [], providers: { "opencode-go": provider() } });
  cache.set(JSON.stringify(S.PICKER_LIMITS_QUERY_KEY), {
    generated_at: NOW, stale_after_s: 900,
    providers: [{ id: "opencode-go", quota: { kind: "none", account: null, fetched_at: null, stale: false, limits: [], read_error: null, read_at: null } },
      { id: "deepseek", quota: null }],
  });
  const picker = provider({ attention: true, quota: quota(0.08, { warn: true }) });
  const limits = [{ limit_key: "primary", label: "5h", left_fraction: 0.08 }];
  S.applyProviderQuotaFrame(client, { provider: "opencode-go", account: "", kind: "windowed", fetched_at: NOW, stale: false, limits, picker });
  assert.equal(cache.get(JSON.stringify(S.PICKER_QUERY_KEY)).providers["opencode-go"], picker);
  const status = cache.get(JSON.stringify(S.PICKER_LIMITS_QUERY_KEY));
  assert.deepEqual(status.providers[0].quota.limits, limits);
  assert.equal(status.providers[0].quota.kind, "windowed");
  assert.equal(status.providers[1].quota, null);
  // Nothing cached yet: nothing is invented.
  const empty = new Map();
  S.applyProviderQuotaFrame({ setQueryData: (key, fn) => empty.set(JSON.stringify(key), fn(undefined)) },
    { provider: "opencode-go", account: "", kind: "windowed", fetched_at: NOW, stale: false, limits, picker });
  assert.equal(empty.get(JSON.stringify(S.PICKER_QUERY_KEY)), undefined);
});
