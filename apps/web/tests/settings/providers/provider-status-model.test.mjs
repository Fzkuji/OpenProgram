// The provider list's view model: health labels, the four quota forms, bar
// tones at 80% and 95% used, the compact row's one line, the balance pace,
// spend for included and unpriced calls, and a quota frame merged live.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { NOW, STATES, limit, payload, spend, status, way } from "../../_support/provider-fixtures.mjs";

installLoader({});

const m = await import("../../../components/settings/providers/status/model.ts");
const { applyQuotaFrame } = await import("../../../components/settings/providers/status/use-provider-status.ts");
const text = (en) => en;

test("health labels name the state, with the time a rate limit lifts", () => {
  const at = new Date((NOW + 1800) * 1000);
  const clock = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  assert.equal(m.healthLabel({ state: "rate_limited", until: NOW + 1800 }, text), `Rate limited · ${clock}`);
  assert.equal(m.healthLabel({ state: "signed_out", until: null }, text), "Signed out");
  assert.equal(m.healthLabel({ state: "not_configured", until: null }, text), "Not set up");
  assert.deepEqual(["ready", "rate_limited", "no_credit", "unknown"].map(m.chipTone), ["ok", "warn", "bad", "muted"]);
});

test("a bar turns warning at 80% used and danger at 95% or when nothing is left", () => {
  assert.equal(m.barTone({ used_fraction: 0.79, status: "ok", remaining: null }), "normal");
  assert.equal(m.barTone({ used_fraction: 0.8, status: "ok", remaining: null }), "warning");
  assert.equal(m.barTone({ used_fraction: 0.95, status: "ok", remaining: null }), "danger");
  assert.equal(m.barTone({ used_fraction: null, status: "ok", remaining: 0 }), "danger");
});

test("each provider lands in its quota form", () => {
  const forms = Object.fromEntries(Object.entries(STATES).map(([k, p]) => [k, m.quotaForm(p, NOW, text)]));
  assert.equal(forms.windowed.form, "windowed");
  assert.deepEqual(forms.windowed.rows.map((r) => [r.label, r.value, r.tone]), [["5h", "62%", "normal"], ["Week", "15%", "warning"]]);
  assert.equal(forms.balance.form, "balance");
  assert.equal(forms.balance.amount, "$18.42");
  assert.equal(forms.balance.runway, "≈ 26 weeks at this pace");
  assert.equal(forms.rate.form, "rate_limit");
  assert.deepEqual(forms.rate.rows.map((r) => [r.label, r.value, r.rest]), [["Req/min", "46", " / 50"]]);
  assert.equal(forms.limited.form, "windowed");
  assert.deepEqual(forms.limited.rows.map((r) => [r.value, r.rest, r.tone]), [["$0.60", " of $20", "danger"]]);
  assert.equal(forms.unread.form, "none");
});

test("a limit reported without a number reads Unlimited, never an empty dash", () => {
  const credits = m.quotaRow(limit({ limit_key: "credits", label: "Credits", unit: "credits", used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null }), NOW, text);
  assert.deepEqual([credits.value, credits.left, credits.rest], ["Unlimited", null, ""]);
  const premium = m.quotaRow(limit({ limit_key: "premium", label: "Premium", unit: "requests", used_fraction: null, left_fraction: null, window_minutes: 43200 }), NOW, text);
  assert.equal(premium.value, "Unlimited");
  const counted = m.quotaRow(limit({ limit_key: "premium", label: "Premium", unit: "requests", used: 70, limit: 300, remaining: 230, used_fraction: null, left_fraction: null, window_minutes: 43200 }), NOW, text);
  assert.deepEqual([counted.value, counted.rest], ["230", " / 300"]);
});

test("the compact row says the worst window first and money with its cap", () => {
  const w = m.compactQuota(STATES.windowed, NOW, text);
  assert.equal(w.left, 0.15);
  assert.equal(w.tone, "warning");
  assert.equal(w.parts[0].b, "62%");
  assert.match(w.parts[0].t, /^ 5h · \d\d:\d\d$/);
  assert.deepEqual(w.parts[1], { b: "15%", t: " Week" });
  const b = m.compactQuota(STATES.balance, NOW, text);
  assert.deepEqual(b.parts, [{ b: "$18.42", t: " balance" }, { b: "", t: "≈ 26 wk" }]);
  const r = m.compactQuota(STATES.rate, NOW, text);
  assert.deepEqual(r.parts, [{ b: "46", t: "/50 rpm" }, { b: "", t: "no balance data" }]);
});

test("a balance pace needs three days of spend, in dollars", () => {
  assert.equal(m.runway(10, "usd", spend({ series: [{ day: "a", cost_usd: 1, tokens: 0, calls: 1 }] }), text), "");
  assert.equal(m.runway(10, "cny", spend(), text), "");
  assert.equal(m.runway(3, "usd", spend({ series: Array.from({ length: 3 }, () => ({ day: "d", cost_usd: 1, tokens: 0, calls: 1 })) }), text), "≈ 3 days at this pace");
});

test("spend shows tokens for a plan and for unpriced calls, dollars otherwise", () => {
  assert.deepEqual(m.spendView(STATES.windowed.spend, text), { value: "3.4M", suffix: "tok", byCost: false, tip: "Included in the plan · $41.20 at API prices" });
  assert.deepEqual(m.spendView(spend({ cost_usd: 0, unpriced_calls: 7 }), text), { value: "3.4M", suffix: "tok", byCost: false, tip: "7 calls unpriced" });
  assert.deepEqual(m.spendView(spend(), text), { value: "$6.30", suffix: "30d", byCost: true, tip: "" });
});

test("a subscription's billing tag is its plan name when one is known", () => {
  assert.equal(m.billingLabel("subscription", text), "Subscription");
  assert.equal(m.billingLabel("subscription", text, null), "Subscription");
  assert.equal(m.billingLabel("subscription", text, "plus"), "Plus");
  assert.equal(m.billingLabel("subscription", text, "pro_lite"), "Pro Lite");
  assert.equal(m.billingLabel("api_key", text, "plus"), "API key");
  assert.equal(m.planLabel(""), "");
  assert.equal(m.spendView(STATES.windowed.spend, text, "plus").tip, "Included in the Plus plan · $41.20 at API prices");
});

test("plan ids and window names read as words, in either language", () => {
  assert.equal(m.planLabel("prolite"), "Pro Lite");
  assert.equal(m.planLabel("plus"), "Plus");
  const zh = (_en, cn) => cn;
  const row = (label, t = text) => m.quotaRow(limit({ limit_key: label, label }), NOW, t).label;
  assert.equal(row("5h"), "5h");
  assert.equal(row("Weekly"), "Weekly");
  assert.equal(row("Weekly", zh), "每周");
  assert.equal(row("Daily", zh), "每天");
  assert.equal(row("Premium"), "Premium", "a name the provider gave stays");
});

test("Updated counts a Refresh of the login and health, not only a quota read", () => {
  assert.equal(m.providerUpdatedAt(status("a", { quota: { fetched_at: NOW - 900 }, checked_at: NOW - 5 })), NOW - 5);
  assert.equal(m.providerUpdatedAt(status("a", { quota: { fetched_at: null } })), null);
  assert.equal(m.updatedAt([status("a", { quota: { fetched_at: null }, checked_at: NOW - 5 })], NOW - 600), NOW - 5);
});

test("the binding limit is the one with least left; Updated is the newest read", () => {
  assert.equal(m.bindingLimit(STATES.windowed).limit_key, "week");
  assert.equal(m.bindingLimit(STATES.balance).limit_key, "balance");
  assert.equal(m.bindingLimit(STATES.unread), null);
  assert.equal(m.updatedAt([status("a", { quota: { fetched_at: NOW - 30 } }), status("b", { quota: { fetched_at: null } })], NOW - 600), NOW - 30);
  assert.equal(m.updatedAt([], NOW - 600), NOW - 600);
});

test("a live quota frame replaces that provider's limits and nothing else", () => {
  const before = payload([STATES.windowed, STATES.balance]);
  const frame = { provider: "openai-codex", account: "work", kind: "windowed", fetched_at: NOW, stale: false, limits: [limit({ used_fraction: 0.9, left_fraction: 0.1 })] };
  const after = applyQuotaFrame(before, frame);
  assert.equal(after.providers[0].quota.limits.length, 1);
  assert.equal(after.providers[0].quota.limits[0].left_fraction, 0.1);
  assert.equal(after.providers[0].quota.fetched_at, NOW);
  assert.equal(after.providers[1], before.providers[1]);
  assert.equal(applyQuotaFrame(before, { ...frame, provider: "nobody" }), before);
});

test("a company reached several ways is one group where its first way is, with the best of their health", () => {
  const codex = way(status("openai-codex", { health: { state: "rate_limited", until: NOW + 60 } }), "openai", "OpenAI", "ChatGPT subscription", "ChatGPT");
  const api = way(status("openai"), "openai", "OpenAI", "API · pay as you go", "API");
  const groups = m.companyGroups([codex, api, status("deepseek", { label: "DeepSeek" })]);
  assert.deepEqual(groups.map((g) => [g.family, g.label, g.ways.map((p) => p.id)]),
    [["openai", "OpenAI", ["openai-codex", "openai"]], ["deepseek", "DeepSeek", ["deepseek"]]]);
  assert.equal(m.bestHealth(groups[0].ways.map((p) => p.health)).state, "ready");
  assert.equal(m.bestHealth([{ state: "invalid_key" }, { state: "rate_limited" }]).state, "rate_limited");
  assert.equal(m.wayTitle(codex, text), "ChatGPT subscription");
  assert.equal(m.wayTitle(status("deepseek", { label: "DeepSeek" }), text), "DeepSeek");
});

test("a way's line names its plan, how it connects (the same key as a way above) and its model", () => {
  const key = (tail, env) => ({ credential_id: tail, kind: "api_key", key_tail: tail, env_var: env ?? null, status: "ready" });
  const zen = status("opencode", { keys: [key("ZauU")], default_model: "glm-5.1" });
  const go = status("opencode-go", { billing: "subscription", keys: [key("ZauU")], default_model: "deepseek-v4-flash" });
  assert.equal(m.sharesKey(go, [zen]), true);
  assert.equal(m.sharesKey(go, []), false);
  assert.equal(m.sharesKey(status("x", { keys: [key("abcd")] }), [zen]), false);
  assert.equal(m.wayMeta(zen, { keys: true, shared: false }, text), "API key · glm-5.1");
  assert.equal(m.wayMeta(go, { keys: true, shared: true }, text), "Same key · deepseek-v4-flash");
  const codex = status("openai-codex", { billing: "subscription", plan: "prolite", keys: [{ kind: "oauth" }], default_model: "gpt-6.1-sol" });
  assert.equal(m.wayMeta(codex, { keys: false, shared: false }, text), "Pro Lite · sign-in · gpt-6.1-sol");
  assert.equal(m.wayMeta(status("perplexity"), { keys: true, shared: false }, text), "No key yet");
});

test("the masonry puts each card into the shortest column, never above the card before it", () => {
  const places = m.placeMasonry([300, 200, 100, 150, 80], 2, 12);
  assert.deepEqual(places, [
    { column: 0, top: 0 }, { column: 1, top: 0 }, { column: 1, top: 212 }, { column: 0, top: 312 }, { column: 1, top: 324 },
  ]);
  for (let i = 1; i < places.length; i++) assert.ok(places[i].top >= places[i - 1].top, "list order is reading order");
  assert.deepEqual(m.placeMasonry([10.2, 5], 1, 12), [{ column: 0, top: 0 }, { column: 0, top: 23 }]);
});
