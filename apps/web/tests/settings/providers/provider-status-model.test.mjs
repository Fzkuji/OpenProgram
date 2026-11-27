// The provider list's view model: health labels, the four quota forms, bar
// tones at 80% and 95% used, the compact row's one line, the balance pace,
// spend for included and unpriced calls, and a quota frame merged live.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { NOW, STATES, cred, limit, payload, spend, status, way } from "../../_support/provider-fixtures.mjs";

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

test("a plan reads as its name, and what a plan includes says so", () => {
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
  const frame = { provider: "opencode-go", account: "work", kind: "windowed", fetched_at: NOW, stale: false, limits: [limit({ used_fraction: 0.9, left_fraction: 0.1 })] };
  const after = applyQuotaFrame(before, frame);
  assert.equal(after.providers[0].quota.limits.length, 1);
  assert.equal(after.providers[0].quota.limits[0].left_fraction, 0.1);
  assert.equal(after.providers[0].quota.fetched_at, NOW);
  assert.equal(after.providers[1], before.providers[1]);
  assert.equal(applyQuotaFrame(before, { ...frame, provider: "nobody" }), before);
});

test("a company reached several ways is one group where its first way is, with the best of their health", () => {
  const codex = way(status("opencode-go", { health: { state: "rate_limited", until: NOW + 60 } }), "opencode", "OpenCode", "Go · monthly subscription", "Go");
  const api = way(status("opencode"), "opencode", "OpenCode", "Zen · pay as you go", "Zen");
  const groups = m.companyGroups([codex, api, status("deepseek", { label: "DeepSeek" })]);
  assert.deepEqual(groups.map((g) => [g.family, g.label, g.ways.map((p) => p.id)]),
    [["opencode", "OpenCode", ["opencode-go", "opencode"]], ["deepseek", "DeepSeek", ["deepseek"]]]);
  assert.equal(m.bestHealth(groups[0].ways.map((p) => p.health)).state, "ready");
  assert.equal(m.bestHealth([{ state: "invalid_key" }, { state: "rate_limited" }]).state, "rate_limited");
  assert.equal(m.wayTitle(codex, text), "Go · monthly subscription");
  assert.equal(m.wayTitle(status("deepseek", { label: "DeepSeek" }), text), "DeepSeek");
});

const ring = (r) => [r.label, r.left == null ? null : Math.round(r.left * 100), r.tone, r.under];

test("a card is one row per credential: who it is, up to three rings, and one last column", () => {
  const week = limit({ limit_key: "week", label: "Weekly", used_fraction: 0.13, left_fraction: 0.87, window_minutes: 10080, resets_at: NOW + 3 * 86400 });
  const month = limit({ limit_key: "monthly", label: "Monthly", used_fraction: 1, left_fraction: 0, window_minutes: null, resets_at: NOW + 14 * 86400, status: "exhausted" });
  const go = way(status("opencode-go", { billing: "subscription", keys: [cred("g", { key_tail: "XL2d", quota: { limits: [limit(), week, month] }, health: { state: "rate_limited", until: NOW + 14 * 86400 } })] }), "opencode", "OpenCode", "Go · monthly subscription", "Go");
  const zen = way(status("opencode", { keys: [cred("z", { key_tail: "ZauU", spend_usd: 1.2 })] }), "opencode", "OpenCode", "Zen · pay as you go", "Zen");
  const [group] = m.companyGroups([go, zen]);
  const rows = m.cardRows(group, NOW, text);
  assert.deepEqual(rows.map((r) => [r.name, r.sub]), [["····XL2d", "Go"], ["····ZauU", "Zen · API key"]]);
  assert.deepEqual(rows[0].rings.map(ring), [["5h", 62, "normal", m.fmtReset(NOW + 4 * 3600, NOW)], ["Week", 87, "normal", m.fmtReset(NOW + 3 * 86400, NOW)], ["Month", 0, "danger", m.fmtReset(NOW + 14 * 86400, NOW)]]);
  assert.deepEqual(rows[0].end, { top: "Limited", bottom: m.fmtReset(NOW + 14 * 86400, NOW), tone: "warning", fix: null });
  assert.deepEqual(rows[1].rings, []);
  assert.deepEqual(rows[1].end, { top: "$1.20", bottom: "30 days", tone: "normal", fix: null });
});

test("the last column holds an amount while a credential works, else why it does not", () => {
  const p = status("x", { billing: "api_key" });
  const end = (k) => m.rowEnd(p, cred("k", k), NOW, text);
  const credits = limit({ limit_key: "credits", label: "Credits", unit: "credits", remaining: 26871.6, used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null });
  const balance = (n) => limit({ limit_key: "balance", label: "Balance", unit: "cny", remaining: n, used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null });
  assert.deepEqual(end({ quota: { limits: [credits] } }), { top: "26.9K", bottom: "Credits", tone: "normal", fix: null });
  assert.deepEqual(end({ quota: { limits: [balance(18.42)] } }), { top: "¥18.42", bottom: "Balance", tone: "normal", fix: null });
  assert.deepEqual(end({ health: { state: "no_credit" }, quota: { limits: [balance(-0.26)] } }), { top: "-¥0.26", bottom: "Balance", tone: "danger", fix: "top_up" });
  assert.deepEqual(end({ spend_usd: 0 }), { top: "", bottom: "", tone: "normal", fix: null }, "nothing spent, nothing said");
  assert.deepEqual(end({ health: { state: "rate_limited", until: NOW + 1800 }, cooldown_until: NOW + 1800 }), { top: "Cooling", bottom: m.fmtReset(NOW + 1800, NOW), tone: "warning", fix: null });
  assert.deepEqual(end({ health: { state: "invalid_key" }, status_at: NOW - 18 * 3600 }), { top: "Invalid", bottom: "18 h ago", tone: "danger", fix: "replace_key" });
  assert.deepEqual(end({ health: { state: "signed_out" }, status_at: NOW - 2 * 86400 }), { top: "Signed out", bottom: "2 d ago", tone: "danger", fix: "sign_in" });
  assert.deepEqual(end({ enabled: false, health: { state: "invalid_key" } }), { top: "Off", bottom: "", tone: "muted", fix: null }, "a key set aside says only that");
});

test("rings: rates and key limits by their cap, unlimited full, a failed read dashed, the tightest three of more", () => {
  const rpm = limit({ limit_key: "rpm", label: "rpm", unit: "requests", limit: 8300, remaining: 8300, used_fraction: 0, left_fraction: 1, window_minutes: 1, resets_at: null });
  const cap = limit({ limit_key: "key_limit", label: "Key limit", unit: "usd", limit: 10, remaining: 6.2, used_fraction: 0.38, left_fraction: 0.62, window_minutes: null, resets_at: null });
  const unlimited = limit({ limit_key: "premium", label: "Premium", unit: "requests", used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null });
  assert.deepEqual(m.ringsOf({ limits: [rpm, cap] }, NOW, text).map(ring), [["Req/m", 100, "normal", "of 8.3K"], ["Limit", 62, "normal", "of $10"]]);
  assert.deepEqual(m.ringsOf({ limits: [unlimited] }, NOW, text).map(ring), [["Premium", 100, "normal", "Unlimited"]]);
  assert.deepEqual(m.ringsOf({ limits: [], read_error: "HTTP 500", read_at: NOW - 18 * 60 }, NOW, text).map(ring), [["Unread", null, "normal", "18 m ago"]]);
  const w = (key, mins, left) => limit({ limit_key: key, label: key, window_minutes: mins, used_fraction: 1 - left, left_fraction: left });
  assert.deepEqual(m.ringsOf({ limits: [w("a", 60, 0.9), w("b", 300, 0.2), w("c", 1440, 0.5), w("d", 10080, 0.1)] }, NOW, text).map((r) => r.key), ["b", "c", "d"]);
});

test("a card folds past four rows, keeping the ones in use and in trouble", () => {
  const keys = ["a", "b", "c", "d", "e", "f"].map((id) => cred(id, { label: id.toUpperCase(), active: id === "f", ...(id === "e" ? { health: { state: "invalid_key" } } : {}) }));
  const [group] = m.companyGroups([status("openrouter", { keys })]);
  const rows = m.cardRows(group, NOW, text);
  assert.equal(rows.find((r) => r.name === "F").inUse, true);
  const { shown, hidden } = m.shownRows(rows);
  assert.deepEqual(shown.map((r) => r.name), ["A", "E", "F"], "three rows and the fold make four");
  assert.deepEqual(hidden.map((r) => r.name), ["B", "C", "D"]);
  assert.equal(m.foldedSummary(m.cardRows(group, NOW, text).filter((r) => r.name === "E")), "1 invalid");
});

test("the masonry puts each card into the shortest column, never above the card before it", () => {
  const places = m.placeMasonry([300, 200, 100, 150, 80], 2, 12);
  assert.deepEqual(places, [
    { column: 0, top: 0 }, { column: 1, top: 0 }, { column: 1, top: 212 }, { column: 0, top: 312 }, { column: 1, top: 324 },
  ]);
  for (let i = 1; i < places.length; i++) assert.ok(places[i].top >= places[i - 1].top, "list order is reading order");
  assert.deepEqual(m.placeMasonry([10.2, 5], 1, 12), [{ column: 0, top: 0 }, { column: 0, top: 23 }]);
});

test("a negative amount puts its sign before the currency, and a way named API adds no API key", () => {
  assert.equal(m.fmtAmount(-0.26, "cny"), "-¥0.26");
  const api = { ...status("minimax", { keys: [cred("k", { key_tail: "Bg5k" })] }), way_label: { title: "API (International)", title_zh: "", short: "API", short_zh: "" } };
  const plan = { ...status("minimax-coding-plan", { keys: [cred("p", { key_tail: "Bg5k" })] }), way_label: { title: "Token Plan", title_zh: "", short: "Token Plan", short_zh: "" } };
  const rows = m.cardRows({ family: "minimax", label: "MiniMax", ways: [api, plan] }, NOW, (en) => en);
  assert.deepEqual(rows.map((r) => r.sub), ["API", "Token Plan · API key"]);
});

test("a company's key is one row that shows everything its ways report, and never the ways that refuse it", () => {
  const lab = (short) => ({ title: short, title_zh: "", short, short_zh: "" });
  const on = (id, short, keys, extra = {}) => ({ ...status(id, { keys, ...extra }), way_label: lab(short) });
  const w5 = (left) => limit({ limit_key: "5h", label: "5h", window_minutes: 300, used_fraction: 1 - left, left_fraction: left, resets_at: NOW + 3600 });
  const week = (left) => limit({ limit_key: "week", label: "Weekly", window_minutes: 10080, used_fraction: 1 - left, left_fraction: left, resets_at: NOW + 3 * 86400 });
  const limited = { health: { state: "rate_limited", until: NOW + 3600 } };
  const refused = { health: { state: "invalid_key" }, use_count: 1 };
  const unread = { quota: { limits: [], read_error: "HTTP 404", read_at: NOW - 60 } };
  // As stable reports MiniMax: Bg5k answered on API China and on Token Plan China,
  // K980 never sent a request but its quota reads on Token Plan China.
  const group = { family: "minimax", label: "MiniMax", ways: [
    on("minimax", "API", [cred("a", { key_tail: "Bg5k", ...refused }), cred("b", { key_tail: "K980", active: true })]),
    on("minimax-cn", "API China", [cred("a", { key_tail: "Bg5k", active: true, use_count: 2 }), cred("b", { key_tail: "K980" })]),
    on("minimax-coding-plan", "Token Plan", [cred("a", { key_tail: "Bg5k", ...refused, ...unread }), cred("b", { key_tail: "K980", active: true, ...unread })]),
    on("minimax-cn-coding-plan", "Token Plan China", [
      cred("a", { key_tail: "Bg5k", use_count: 1, ...limited, quota: { limits: [w5(0), week(0.79)] } }),
      cred("b", { key_tail: "K980", active: true, ...limited, quota: { limits: [w5(0), week(0.63)] } }),
    ], { default_model: "MiniMax-M3" }),
  ] };
  const rows = m.cardRows(group, NOW, text);
  assert.deepEqual(rows.map((r) => [r.name, r.sub, r.inUse, r.end.top, r.rings.map((g) => [g.label, Math.round(g.left * 100)])]), [
    ["····Bg5k", "API China · Token Plan China", false, "Limited", [["5h", 0], ["Week", 79]]],
    ["····K980", "Token Plan China", true, "Limited", [["5h", 0], ["Week", 63]]],
  ]);
  assert.equal(rows[0].p.id, "minimax-cn-coding-plan", "the row opens the way it reports for");
  assert.match(rows[0].rings[1].tip, /^Token Plan China · /, "a ring's hint names its way");
  assert.equal(rows[0].listsWays, true);
  assert.equal(m.foldedSummary(rows), "2 limited", "a way that refuses a key is no trouble");
  const quiet = m.cardRows({ family: "minimax", label: "MiniMax", ways: group.ways.slice(0, 3).map((w) => ({ ...w, keys: w.keys.filter((k) => k.key_tail === "K980") })) }, NOW, text);
  assert.deepEqual([quiet[0].sub, quiet[0].rings.map((g) => g.label)], ["Not used yet", ["Unread"]], "a quota that could not be read is still shown");
  const dead = m.cardRows({ family: "minimax", label: "MiniMax", ways: group.ways.map((w) => ({ ...w, keys: [cred("a", { key_tail: "Bg5k", ...refused })] })) }, NOW, text);
  assert.deepEqual([dead[0].sub, dead[0].end.top, dead[0].end.fix], ["API key", "Invalid", "replace_key"], "refused everywhere is an invalid key");
  assert.equal(m.estimateCardHeight(group), 16 + 32 + 8 + 2 * 60 + 8 + 12);
});
