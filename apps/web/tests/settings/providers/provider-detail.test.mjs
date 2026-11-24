// A provider's page (design §5): the usage panel (period, cost or tokens,
// the four figures, the pace of a key limit), the Credentials section (rows,
// the row menu, reorder, strategy, the add and replace dialog, the empty
// state, a card's fix), the models table (switch, prices,
// capabilities, 30-day spend, default star, bulk actions) and Remove
// provider behind the one destructive confirm.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../../_support/dom.mjs";
import { NOW, STATES, UI_STUBS, button, check, credential, credentialList, fakeFetch, settle, typeInto } from "../../_support/provider-fixtures.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": "export const useQueryClient = () => ({ invalidateQueries() {} });",
  },
  replace: {
    ...UI_STUBS,
    "/lib/net/api.ts": "export const api = { switchModel: async (...args) => { (globalThis.switched ??= []).push(args); return {}; } };",
    "/lib/runtime-bridge/providers.ts": "export const loadAgentSettings = () => {};",
    "/components/settings/providers/auth/provider-login.tsx": "import { createElement as h } from 'react'; export const ProviderLogin = ({ method }) => h('div', { 'data-login': method ?? '' }); export const MethodChoices = ({ methods, value, onValueChange }) => h('div', { role: 'radiogroup' }, methods.map((m) => h('button', { key: m.id, type: 'button', role: 'radio', 'data-method': m.id, 'aria-checked': String(value === m.id), onClick: () => onValueChange(m.id) }, m.label)));",
    "/components/settings/providers/auth/base-url.tsx": "import { createElement as h } from 'react'; export const BaseUrl = () => h('div', { 'data-base-url': '' });",
    "/components/settings/providers/auth/setup-hint.tsx": "export const SetupHint = () => null; export const CliInfo = () => null;",
    "/components/settings/providers/auth/connectivity.tsx": "export const Connectivity = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { Detail } = await import("../../../components/settings/providers/detail.tsx");
const { invalidateAllSettings } = await import("../../../lib/prefs/settings-cache.ts");
console.warn = () => {};

const realNow = Date.now;
Date.now = () => NOW * 1000;
process.on("exit", () => { Date.now = realNow; });

const PROVIDER = { id: "openrouter", label: "OpenRouter", enabled: true, kind: "api", api_key_env: "OPENROUTER_API_KEY", supports_fetch: true, base_url: "https://openrouter.ai/api/v1" };
const STATUS = {
  ...STATES.limited,
  default_model: "openrouter/anthropic/claude-sonnet-4.5",
  keys: [
    { account: "Work", credential_id: "work", kind: "api_key", status: "ready", active: true, cooldown_until: null, last_error: null, last_used_at: NOW - 60, use_count: 3, status_at: NOW - 60 },
    { account: "Spare", credential_id: "spare", kind: "api_key", status: "invalid_key", active: false, cooldown_until: null, last_error: "401 Unauthorized", last_used_at: null, use_count: 0, status_at: NOW - 600 },
  ],
};
const MODELS = [
  { id: "openai/gpt-5.5", enabled: false, context_window: 400000, input_cost: 1.25, output_cost: 10, cache_read_cost: 0.125, tools: true, vision: true, family: "gpt" },
  { id: "anthropic/claude-sonnet-4.5", enabled: true, context_window: 1000000, input_cost: 3, output_cost: 15, cache_read_cost: 0.3, tools: true, reasoning: true },
  { id: "meta/llama-free", enabled: true, context_window: 8192 },
];
const USAGE = (days) => ({
  provider: "openrouter", days, since: NOW - days * 86400, until: NOW, group: "day,model",
  totals: { calls: 40, cost_usd: 12.5, list_price_usd: 12.5, included: false, unpriced_calls: 2, input_tokens: 3e6, output_tokens: 5e5, cache_read_tokens: 1e6, cache_write_tokens: 0, total_tokens: 4.5e6, cache_share: 0.22 },
  series: [{ day: "a", ts: NOW - 86400, list_price_usd: 2, tokens: 9e5, calls: 8, models: { "anthropic/claude-sonnet-4.5": { list_price_usd: 1.5, tokens: 5e5 } } }],
  models: [
    { model_id: "anthropic/claude-sonnet-4.5", calls: 30, list_price_usd: 10.5, unpriced_calls: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 3e6 },
    { model_id: "meta/llama-free", calls: 10, list_price_usd: 0, unpriced_calls: 2, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 1.5e6 },
  ],
  quota_samples: [],
});
const WORK = credential("work", { label: "Work", display_label: "Work", key_tail: "a1b2", today: { calls: 12, cost_usd: 0.42 } });
const SPARE = credential("spare", { label: "Spare", display_label: "Spare", key_tail: "c3d4", status: "invalid_key", last_error: { at: NOW - 600, code: "401", message: "401 Unauthorized" } });
const LIST = (credentials = [WORK, SPARE], over = {}) => credentialList("openrouter", credentials, { auth: { env_vars: ["OPENROUTER_API_KEY"], keys_url: "https://openrouter.example.test/keys", billing_url: null }, ...over });
const BASE = "/api/providers/openrouter/credentials";

function serve(extra = {}) {
  return fakeFetch({
    "GET /api/providers/openrouter/models": { models: MODELS },
    "GET /api/providers/openrouter/usage": (_, url) => USAGE(Number(new URL(url, "http://x").searchParams.get("days"))),
    [`GET ${BASE}`]: LIST(),
    [`PATCH ${BASE}/spare`]: { ok: true },
    [`DELETE ${BASE}/work`]: { ok: true },
    [`DELETE ${BASE}/spare`]: { ok: true },
    [`POST ${BASE}/spare/check`]: { credential: SPARE, check: check("rejected", { http_status: 401 }) },
    [`POST ${BASE}/spare/replace`]: { ok: true, credential: SPARE, check: check("works") },
    [`POST ${BASE}/check-key`]: { ok: true, check: check("works", { model_count: 42 }) },
    [`POST ${BASE}/reorder`]: LIST([SPARE, WORK]),
    [`POST ${BASE}/strategy`]: LIST(),
    "POST /api/providers/openrouter/toggle": { ok: true },
    ...extra,
  });
}

async function open(props = {}) {
  const changes = [];
  const view = await render(h(Detail, { provider: PROVIDER, status: STATUS, onToggle: (on) => changes.push(["toggle", on]), onChanged: () => changes.push(["changed"]), onDeleted: () => changes.push(["deleted"]), onBack: () => changes.push(["back"]), onRefresh: () => changes.push(["refresh"]), ...props }));
  await settle(view, 4);
  return { view, changes };
}
const posts = (calls) => calls.filter(([m]) => m === "POST").map(([, u, b]) => [u, b]);
const panel = (view, name) => view.container.querySelector(`section[aria-label="${name}"]`);

afterEach(() => { document.body.innerHTML = ""; invalidateAllSettings(); globalThis.confirmRequests = []; globalThis.confirmAnswer = true; globalThis.switched = []; });

describe("header", () => {
  test("back, the health chip, when it was read, Refresh and the on switch", async () => {
    serve();
    const { view, changes } = await open();
    assert.match(view.container.querySelector("h2").textContent, /OpenRouter/);
    assert.match(view.container.querySelector("[data-state]").textContent, /^Rate limited · \d\d:\d\d$/);
    assert.match(view.container.textContent, /Updated 2 min ago/);
    await view.act(() => button(view.container, "Model services").click());
    await view.act(() => button(view.container, "Refresh").click());
    const toggle = view.container.querySelector('[aria-label="Enable this provider"]');
    assert.equal(toggle.getAttribute("aria-checked"), "true");
    await view.act(() => toggle.click());
    assert.deepEqual(changes.filter(([k]) => k !== "changed"), [["back"], ["refresh"], ["toggle", false]]);
    await view.unmount();
  });
});

describe("usage panel", () => {
  test("30 days by cost: spent with unpriced calls on hover, tokens, cache, and the key limit that binds", async () => {
    const calls = serve();
    const { view } = await open();
    const usage = panel(view, "Usage");
    const figures = [...usage.querySelectorAll("b")].map((b) => b.textContent);
    assert.deepEqual(figures.slice(0, 4), ["$12.50", "4.5M", "22%", "$0.60"]);
    assert.ok(usage.querySelector('[data-tip="2 calls unpriced"]'));
    assert.ok(usage.querySelector('[data-limit="key_limit"] b').className.includes("danger"));
    assert.match(usage.textContent, /Key limit left/);
    assert.match(usage.textContent, /anthropic\/claude-sonnet-4\.5/);
    assert.match(usage.textContent, /Daily pace for the limit/);
    assert.ok(calls.some(([, u]) => u === "/api/providers/openrouter/usage?days=30&group=day,model"));
    await view.act(() => button(usage, "90d").click());
    await settle(view);
    assert.ok(calls.some(([, u]) => u === "/api/providers/openrouter/usage?days=90&group=day,model"));
    await view.act(() => button(usage, "Tokens").click());
    assert.doesNotMatch(usage.textContent, /Daily pace/, "the pace is a cost line");
    await view.unmount();
  });

  test("a subscription opens on tokens and calls its spend Included", async () => {
    fakeFetch({
      "GET /api/providers/openrouter/models": { models: [] },
      [`GET ${BASE}`]: LIST(),
      "GET /api/providers/openrouter/usage": () => ({ ...USAGE(30), totals: { ...USAGE(30).totals, included: true, cost_usd: null, list_price_usd: 41.2 } }),
    });
    const { view } = await open({ status: { ...STATES.windowed, keys: [] } });
    const usage = panel(view, "Usage");
    assert.equal(button(usage, "Tokens").getAttribute("aria-selected"), "true");
    assert.equal(usage.querySelector("b").textContent, "Included");
    assert.ok(usage.querySelector('[data-tip="$41.20 at API prices"]'));
    await view.unmount();
  });

  test("a subscription's Cost view prints no list-price dollars: the axis is blank and a day's list price shows only on hover", async () => {
    fakeFetch({
      "GET /api/providers/openrouter/models": { models: [] },
      [`GET ${BASE}`]: LIST(),
      "GET /api/providers/openrouter/usage": () => ({ ...USAGE(30), totals: { ...USAGE(30).totals, included: true, cost_usd: null, list_price_usd: 41.2 } }),
    });
    const { view } = await open({ status: { ...STATES.windowed, keys: [] } });
    const usage = panel(view, "Usage");
    await view.act(() => button(usage, "Cost").click());
    const wrap = usage.querySelector(".chartWrap");
    setRect(wrap, { width: 480, height: 150 });
    await view.act(() => resizeObservers.trigger(wrap));
    const axis = [...usage.querySelectorAll("svg text")].map((t) => t.textContent);
    assert.ok(axis.length > 0, "the chart drew");
    assert.ok(axis.every((label) => !label.includes("$")), `no dollars on the axis: ${axis.join(", ")}`);
    assert.equal(usage.querySelector("b").textContent, "Included");
    await view.act(() => usage.querySelector("svg rect").dispatchEvent(new Event("mouseover", { bubbles: true })));
    assert.match(usage.querySelector(".chartTip").textContent, /Included · \$2\.00 at API prices/);
    await view.unmount();
  });

  test("a pay-per-token provider's Cost view keeps dollars on the axis and in the hover", async () => {
    serve();
    const { view } = await open();
    const usage = panel(view, "Usage");
    const wrap = usage.querySelector(".chartWrap");
    setRect(wrap, { width: 480, height: 150 });
    await view.act(() => resizeObservers.trigger(wrap));
    assert.ok([...usage.querySelectorAll("svg text")].some((t) => t.textContent.startsWith("$")));
    await view.act(() => usage.querySelector("svg rect").dispatchEvent(new Event("mouseover", { bubbles: true })));
    assert.doesNotMatch(usage.querySelector(".chartTip").textContent, /Included/);
    await view.unmount();
  });

  test("a failed read says nothing; the panel stays and fills in when a quiet retry answers", async () => {
    let fail = true;
    serve({ "GET /api/providers/openrouter/usage": (_, url) => (fail ? { status: 500, body: {} } : USAGE(Number(new URL(url, "http://x").searchParams.get("days")))) });
    const { view } = await open();
    assert.equal(panel(view, "Usage").querySelector('[role="alert"]'), null);
    assert.doesNotMatch(panel(view, "Usage").textContent, /Couldn't/);
    fail = false;
    await view.act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
    await settle(view);
    assert.equal(panel(view, "Usage").querySelector("b").textContent, "$12.50");
    await view.unmount();
  });

  test("reopening the provider draws its usage and models in the first render", async () => {
    serve();
    let { view } = await open();
    await view.unmount();
    globalThis.fetch = () => new Promise(() => {});
    view = await render(h(Detail, { provider: PROVIDER, status: STATUS, onToggle() {}, onChanged() {} }));
    assert.equal(panel(view, "Usage").querySelector("b").textContent, "$12.50");
    assert.ok(view.container.textContent.includes("anthropic/claude-sonnet-4.5"));
    await view.unmount();
  });
});

describe("credentials", () => {
  const section = (view) => view.container.querySelector("[data-credentials-section]");
  const row = (view, id) => section(view).querySelector(`[data-credential="${id}"]`);
  const dialog = () => document.querySelector('[role="dialog"]');
  const enter = (view, input) => view.act(() => input.dispatchEvent(Object.assign(new Event("keydown", { bubbles: true }), { key: "Enter" })));
  const sent = (calls, method) => calls.filter(([m, u]) => m === method && u.startsWith(BASE)).map(([, u, b]) => [u.slice(BASE.length), b]);

  test("one row per credential in order: in use, masked tail, today's use or why it stopped, status", async () => {
    serve();
    const { view } = await open();
    const rows = [...section(view).querySelectorAll("[data-credential]")];
    assert.deepEqual(rows.map((r) => r.dataset.credential), ["work", "spare"]);
    assert.match(rows[0].textContent, /Work/);
    assert.match(rows[0].textContent, /In use/);
    assert.doesNotMatch(rows[1].textContent, /In use/);
    assert.match(rows[0].textContent, /····a1b2/);
    assert.match(rows[0].textContent, /\$0\.42 today/);
    assert.match(rows[0].textContent, /12 requests/);
    assert.equal(rows[0].querySelector("[data-state]").textContent, "Ready");
    assert.equal(rows[1].querySelector("[data-state]").textContent, "Invalid key");
    assert.match(rows[1].textContent, /Rejected by OpenRouter \(401\)/);
    assert.ok(button(rows[1], "Replace"), "the failing key offers its fix inline");
    assert.doesNotMatch(section(view).textContent, /sk-or-/, "no key text reaches the page");
    assert.match(section(view).textContent, /Drag a row, or use Move up \/ Move down, to change the order\./);
    await view.unmount();
  });

  test("the row menu renames, checks, sets aside, moves and removes; only Remove asks", async () => {
    const calls = serve();
    const { view } = await open();
    await view.act(() => button(row(view, "spare"), "Rename").click());
    const name = row(view, "spare").querySelector('input[aria-label="Name"]');
    await typeInto(view, name, "Backup");
    await enter(view, name);
    await settle(view);
    await view.act(() => button(row(view, "spare"), "Check now").click());
    await settle(view);
    await view.act(() => button(row(view, "spare"), "Don't use for now").click());
    await settle(view);
    const up = [...row(view, "work").querySelectorAll('[role="menuitem"]')].find((i) => i.textContent === "Move up");
    assert.equal(up.getAttribute("aria-disabled"), "true", "the first row cannot move up");
    await view.act(() => button(row(view, "spare"), "Move up").click());
    await settle(view);
    assert.equal(globalThis.confirmRequests?.length ?? 0, 0, "nothing so far asked");
    await view.act(() => button(row(view, "spare"), "Remove…").click());
    await settle(view);
    assert.deepEqual(globalThis.confirmRequests.map((r) => [r.title, r.destructive]), [['Remove "Spare"?', true]]);
    assert.deepEqual(sent(calls, "PATCH"), [["/spare", { label: "Backup" }], ["/spare", { enabled: false }]]);
    assert.deepEqual(sent(calls, "POST"), [["/spare/check", null], ["/reorder", { order: ["spare", "work"] }]]);
    assert.deepEqual(sent(calls, "DELETE"), [["/spare", null]]);
    await view.unmount();
  });

  test("an environment key that cannot be removed here says why", async () => {
    const env = credential("env:OPENROUTER_API_KEY", { display_label: "OPENROUTER_API_KEY", source: "env", env_var: "OPENROUTER_API_KEY", read_only: true });
    serve({
      [`GET ${BASE}`]: LIST([WORK, env]),
      [`DELETE ${BASE}/env%3AOPENROUTER_API_KEY`]: { status: 409, body: { error: "Unset OPENROUTER_API_KEY to remove it", env_var: "OPENROUTER_API_KEY" } },
    });
    globalThis.toasts = [];
    const { view } = await open();
    const envRow = row(view, "env:OPENROUTER_API_KEY");
    assert.equal(button(envRow, "Rename"), undefined, "a key read from the environment keeps its name");
    assert.match(envRow.textContent, /\$OPENROUTER_API_KEY/);
    await view.act(() => button(envRow, "Remove…").click());
    await settle(view);
    assert.deepEqual(globalThis.toasts, ["Unset OPENROUTER_API_KEY to remove it"]);
    await view.unmount();
  });

  test("how requests are spread shows with two keys and posts the choice", async () => {
    const calls = serve();
    const { view } = await open();
    const group = section(view).querySelector('[role="group"]');
    assert.deepEqual([...group.querySelectorAll("button")].map((b) => [b.textContent, b.getAttribute("aria-pressed")]), [["In order", "true"], ["Evenly", "false"], ["Least used", "false"]]);
    await view.act(() => button(group, "Evenly").click());
    await settle(view);
    assert.deepEqual(sent(calls, "POST").at(-1), ["/strategy", { strategy: "evenly" }]);
    await view.unmount();
    serve({ [`GET ${BASE}`]: LIST([WORK]) });
    const single = await open();
    assert.equal(section(single.view).querySelector('[role="group"]'), null, "no choice with one key");
    await single.view.unmount();
  });

  test("Replace opens the key step for that row; the new key is checked and takes its place", async () => {
    const calls = serve();
    const { view } = await open();
    await view.act(() => button(row(view, "spare"), "Replace").click());
    await settle(view);
    assert.match(dialog().querySelector("h2").textContent, /Replace Spare/);
    assert.equal(dialog().querySelector('input[aria-label="Name"], #credential-name-openrouter'), null, "a replacement keeps the name");
    const key = dialog().querySelector("#key-openrouter");
    await typeInto(view, key, "sk-or-test-placeholder");
    await enter(view, key);
    await settle(view);
    assert.match(dialog().textContent, /Key works/);
    assert.deepEqual(sent(calls, "POST"), [["/check-key", { api_key: "sk-or-test-placeholder", replace: "spare" }]], "a check replaces nothing");
    await view.act(() => button(dialog(), "Replace key").click());
    await settle(view);
    assert.equal(dialog(), null);
    assert.deepEqual(sent(calls, "POST").slice(1), [["/spare/replace", { api_key: "sk-or-test-placeholder" }]]);
    await view.unmount();
  });

  test("arriving from a card's Replace key opens the failing key's replacement", async () => {
    serve();
    const { view } = await open({ fix: { action: "replace_key", credential: null } });
    await settle(view);
    assert.match(dialog().querySelector("h2").textContent, /Replace Spare/);
    await view.unmount();
  });

  test("with no key: the empty state adds one or links to where keys are made", async () => {
    serve({ [`GET ${BASE}`]: LIST([]) });
    const { view } = await open();
    assert.match(section(view).textContent, /No key yet/);
    assert.match(section(view).textContent, /OpenRouter needs an API key to run its models\./);
    const get = button(section(view), "Get a key");
    assert.equal(get.getAttribute("href"), "https://openrouter.example.test/keys");
    assert.equal(get.getAttribute("target"), "_blank");
    assert.equal(section(view).querySelector("[data-credential]"), null);
    await view.unmount();
  });

  test("Add key checks the key before it is saved, shows the result, and keeps the name", async () => {
    const calls = serve({
      [`GET ${BASE}`]: LIST([]),
      [`POST ${BASE}`]: { ok: true, credential: credential("team", { label: "Team" }), check: check("works", { model_count: 42 }) },
    });
    const { view } = await open();
    const empty = section(view).querySelector("[data-credentials-section] > div") ?? section(view);
    const add = [...empty.querySelectorAll("button")].filter((b) => b.textContent === "Add key").at(-1);
    await view.act(() => add.click());
    await settle(view);
    assert.match(dialog().querySelector("h2").textContent, /Add a key to OpenRouter/);
    assert.match(dialog().textContent, /Checked with OpenRouter before it is saved\./);
    await typeInto(view, dialog().querySelector("#credential-name-openrouter"), "Team");
    const key = dialog().querySelector("#key-openrouter");
    assert.equal(key.getAttribute("type"), "password", "the key is masked until revealed");
    await typeInto(view, key, "sk-or-test-placeholder");
    await enter(view, key);
    await settle(view);
    assert.match(dialog().querySelector('[data-result="works"]').textContent, /Key works · 42 models/);
    assert.deepEqual(sent(calls, "POST"), [["/check-key", { api_key: "sk-or-test-placeholder" }]], "checked, not saved");
    await view.act(() => button(dialog(), "Add key").click());
    await settle(view);
    assert.equal(dialog(), null);
    assert.deepEqual(sent(calls, "POST").slice(1), [["", { api_key: "sk-or-test-placeholder", validate: true, label: "Team" }]], "saved once, with its name");
    assert.deepEqual(sent(calls, "PATCH"), [], "no follow-up rename");
    await view.unmount();
  });

  test("Add waits for the check, and a typed key is checked once typing rests", async () => {
    const calls = serve({
      [`GET ${BASE}`]: LIST([]),
      [`POST ${BASE}/check-key`]: { ok: true, check: check("works", { model_count: 3 }) },
    });
    const { view } = await open();
    const empty = section(view).querySelector("[data-credentials-section] > div") ?? section(view);
    const add = [...empty.querySelectorAll("button")].filter((b) => b.textContent === "Add key").at(-1);
    await view.act(() => add.click());
    await settle(view);
    await typeInto(view, dialog().querySelector("#key-openrouter"), "sk-or-typed-placeholder");
    assert.equal(button(dialog(), "Add key").disabled, true, "nothing is added before the key is checked");
    assert.deepEqual(sent(calls, "POST"), [], "no check while typing goes on");
    await new Promise((resolve) => setTimeout(resolve, 650));
    await settle(view);
    assert.deepEqual(sent(calls, "POST"), [["/check-key", { api_key: "sk-or-typed-placeholder" }]]);
    assert.equal(button(dialog(), "Add key").disabled, false);
    await view.unmount();
  });

  async function openAddKey(extra = {}) {
    const calls = serve({ [`GET ${BASE}`]: LIST([]), ...extra });
    const { view } = await open();
    const empty = section(view).querySelector("[data-credentials-section] > div") ?? section(view);
    await view.act(() => [...empty.querySelectorAll("button")].filter((b) => b.textContent === "Add key").at(-1).click());
    await settle(view);
    return { calls, view, key: dialog().querySelector("#key-openrouter") };
  }

  test("going back to a key already checked shows its answer and Add again", async () => {
    const { calls, view, key } = await openAddKey();
    await typeInto(view, key, "sk-or-test-placeholder");
    await enter(view, key);
    await settle(view);
    assert.equal(button(dialog(), "Add key").disabled, false);
    await typeInto(view, key, "sk-or-test-placeholderx");
    assert.equal(button(dialog(), "Add key").disabled, true);
    await typeInto(view, key, "sk-or-test-placeholder");
    assert.equal(button(dialog(), "Add key").disabled, false, "the earlier check holds for the same key");
    assert.match(dialog().querySelector('[data-result="works"]').textContent, /Key works/);
    assert.equal(sent(calls, "POST").length, 1, "not asked again");
    await view.unmount();
  });

  test("a key typed during a check is checked when that check ends", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let n = 0;
    const { calls, view, key } = await openAddKey({
      [`POST ${BASE}/check-key`]: async () => { n += 1; if (n === 1) await gate; return { ok: true, check: check("works", { model_count: 3 }) }; },
    });
    await typeInto(view, key, "sk-or-first-placeholder");
    await enter(view, key);
    await typeInto(view, key, "sk-or-second-placeholder");
    await enter(view, key);
    await view.act(async () => { release(); await gate; });
    await settle(view);
    await settle(view);
    assert.deepEqual(sent(calls, "POST").map(([, b]) => b.api_key), ["sk-or-first-placeholder", "sk-or-second-placeholder"]);
    assert.equal(button(dialog(), "Add key").disabled, false);
    assert.doesNotMatch(dialog().textContent, /Checking the key/, "no spinner left behind");
    await view.unmount();
  });

  test("a key that could not be checked here can still be added", async () => {
    const { view, key } = await openAddKey({ [`POST ${BASE}/check-key`]: { ok: true, check: check("not_configured") } });
    await typeInto(view, key, "sk-or-test-placeholder");
    await enter(view, key);
    await settle(view);
    assert.equal(button(dialog(), "Add key").disabled, false);
    assert.equal(key.getAttribute("aria-invalid"), null, "not marked as a wrong key");
    await view.unmount();
  });

  test("a short value typed by hand is not sent while typing rests", async () => {
    const { calls, view, key } = await openAddKey();
    await typeInto(view, key, "sk-or-");
    await new Promise((resolve) => setTimeout(resolve, 650));
    await settle(view);
    assert.deepEqual(sent(calls, "POST"), [], "a prefix never reaches the provider");
    assert.doesNotMatch(dialog().textContent, /Checking the key/);
    await view.unmount();
  });

  test("a failed check of an older key is not shown over the newer one", async () => {
    let fail;
    const gate = new Promise((resolve) => { fail = resolve; });
    let n = 0;
    const { view, key } = await openAddKey({
      [`POST ${BASE}/check-key`]: async () => { n += 1; if (n === 1) { await gate; throw new Error("down"); } return { ok: true, check: check("works") }; },
    });
    await typeInto(view, key, "sk-or-first-placeholder");
    await enter(view, key);
    await typeInto(view, key, "sk-or-second-placeholder");
    await view.act(async () => { fail(); await gate; });
    await settle(view);
    assert.doesNotMatch(dialog().textContent, /Couldn't check the key/);
    await view.unmount();
  });

  test("a row over its own place is not announced over itself", async () => {
    const { moveAnnouncements } = await import("../../../components/settings/providers/detail/credentials-section.tsx");
    const en = (e) => e;
    const say = moveAnnouncements([credential("a", { label: "Personal" }), credential("b", { label: "Work" })], en);
    assert.equal(say.onDragOver({ active: { id: "a" }, over: { id: "a" } }), undefined);
    assert.match(say.onDragOver({ active: { id: "a" }, over: { id: "b" } }), /Personal is over Work \(2\/2\)/);
  });

  test("the keyboard moves a picked-up row one row per arrow", async () => {
    const { rowByRow } = await import("../../../components/settings/providers/detail/credentials-section.tsx");
    const rects = new Map([["a", { top: 0 }], ["b", { top: 60 }], ["c", { top: 120 }]]);
    const move = (code, y) => rowByRow({ code, preventDefault() {} }, { currentCoordinates: { x: 5, y }, context: { droppableRects: rects } });
    assert.deepEqual(move("ArrowDown", 2), { x: 5, y: 60 });
    assert.deepEqual(move("ArrowUp", 61), { x: 5, y: 0 });
    assert.deepEqual(move("ArrowDown", 120), { x: 5, y: 120 }, "the last row stays last");
    assert.equal(move("ArrowLeft", 0), undefined);
  });

  test("a rejected key is not saved and the dialog says so", async () => {
    const calls = serve({
      [`GET ${BASE}`]: LIST([]),
      [`POST ${BASE}/check-key`]: { ok: true, check: check("rejected", { http_status: 401 }) },
    });
    const { view } = await open();
    await view.act(() => [...section(view).querySelectorAll("button")].filter((b) => b.textContent === "Add key").at(-1).click());
    await settle(view);
    const key = dialog().querySelector("#key-openrouter");
    await typeInto(view, key, "sk-or-bad-placeholder");
    await enter(view, key);
    await settle(view);
    assert.match(dialog().querySelector('[data-result="rejected"]').textContent, /Key rejected \(401\)/);
    assert.equal(button(dialog(), "Add key").disabled, true, "a refused key cannot be added");
    assert.deepEqual(sent(calls, "POST"), [["/check-key", { api_key: "sk-or-bad-placeholder" }]]);
    await view.unmount();
  });

  test("Cancel after a checked key leaves nothing behind", async () => {
    const calls = serve({ [`GET ${BASE}`]: LIST([]) });
    const { view } = await open();
    await view.act(() => [...section(view).querySelectorAll("button")].filter((b) => b.textContent === "Add key").at(-1).click());
    await settle(view);
    const key = dialog().querySelector("#key-openrouter");
    await typeInto(view, key, "sk-or-test-placeholder");
    await enter(view, key);
    await settle(view);
    assert.match(dialog().textContent, /Key works/);
    await view.act(() => button(dialog(), "Cancel").click());
    await settle(view);
    assert.equal(dialog(), null);
    assert.deepEqual(sent(calls, "POST"), [["/check-key", { api_key: "sk-or-test-placeholder" }]], "only the check was sent");
    await view.unmount();
  });

  test("a key provider offers Add key only, straight to the key step", async () => {
    let reads = 0;
    serve({ [`GET ${BASE}`]: async () => { reads += 1; if (reads > 1) await new Promise(() => {}); return LIST([WORK]); } });
    const { view } = await open();
    const labels = [...section(view).querySelectorAll("button")].map((b) => b.textContent);
    assert.ok(labels.includes("Add key"));
    assert.ok(!labels.includes("Sign in"), "no sign-in for a provider whose only way is a key");
    await view.act(() => button(section(view), "Add key").click());
    assert.equal(dialog().querySelector("[data-step]").dataset.step, "key", "the kept ways open the dialog at once, while they are read again");
    assert.equal(reads, 2);
    assert.equal(dialog().querySelector('[role="radiogroup"]'), null, "one way: no list of one");
    await view.unmount();
  });

  test("a row's grip is a button the keyboard reaches", async () => {
    serve();
    const { view } = await open();
    const grip = row(view, "work").querySelector("[data-grip]");
    assert.equal(grip.tagName, "BUTTON");
    assert.equal(grip.getAttribute("aria-label"), "Reorder Work");
    assert.notEqual(grip.getAttribute("aria-hidden"), "true");
    await view.unmount();
  });

  test("Base URL has its own section", async () => {
    serve();
    const { view } = await open();
    assert.equal(section(view).querySelector("[data-base-url]"), null, "not inside the credentials");
    assert.ok(view.container.querySelector('section[aria-label="Endpoint"] [data-base-url]'));
    await view.unmount();
  });

  test("a sign-in provider signs in with another account; its one way starts at once", async () => {
    const methods = [{ id: "browser", label: "Sign in with OpenRouter", label_zh: "", description: "Opens the browser.", description_zh: "" }];
    serve({ [`GET ${BASE}`]: LIST([], { auth: { methods, env_vars: [], check: null } }) });
    const { view } = await open({ provider: { id: "openrouter", label: "OpenRouter", enabled: true, kind: "api", login_methods: methods } });
    assert.match(section(view).textContent, /Not signed in/);
    assert.ok(![...section(view).querySelectorAll("button")].some((b) => b.textContent === "Add key"), "a sign-in-only provider has no Add key");
    await view.act(() => [...section(view).querySelectorAll("button")].filter((b) => b.textContent === "Sign in").at(-1).click());
    await settle(view);
    assert.match(dialog().querySelector("h2").textContent, /Add a OpenRouter account/);
    assert.ok(dialog().querySelector("[data-login]"));
    await view.unmount();
  });
});

describe("models table", () => {
  test("enabled models first, with context, prices, capabilities, 30-day spend and the default starred", async () => {
    serve();
    const { view } = await open();
    const models = panel(view, "Models");
    assert.match(models.textContent, /2 of 3 enabled/);
    const rows = [...models.querySelectorAll("tr[data-model]")];
    assert.deepEqual(rows.map((r) => r.dataset.model), ["anthropic/claude-sonnet-4.5", "meta/llama-free", "openai/gpt-5.5"]);
    const cells = (r) => [...r.querySelectorAll("td")].map((td) => td.textContent);
    assert.deepEqual(cells(rows[0]).slice(1, 4), ["anthropic/claude-sonnet-4.5", "1M", "3.00 / 15.00 / 0.30"]);
    assert.equal(cells(rows[0])[5], "$10.50");
    assert.deepEqual([...rows[0].querySelectorAll('[role="img"]')].map((i) => i.getAttribute("aria-label")), ["Tool use", "Reasoning"]);
    assert.deepEqual([...rows[2].querySelectorAll('[role="img"]')].map((i) => i.getAttribute("aria-label")), ["Tool use", "Vision"]);
    assert.equal(cells(rows[1])[3], "—");
    assert.ok(rows[2].className.includes("off"));
    assert.ok(rows[2].querySelector('[data-tip="Family gpt"]'));
    assert.deepEqual(rows.map((r) => r.querySelector("[aria-pressed]").getAttribute("aria-pressed")), ["true", "false", "false"]);
    await view.unmount();
  });

  test("a subscription's 30-day spend reads Included, its list price only on hover", async () => {
    fakeFetch({
      "GET /api/providers/openrouter/models": { models: MODELS },
      [`GET ${BASE}`]: LIST(),
      "GET /api/providers/openrouter/usage": () => ({ ...USAGE(30), totals: { ...USAGE(30).totals, included: true, cost_usd: null, list_price_usd: 41.2 } }),
    });
    const { view } = await open({ status: { ...STATES.windowed, keys: [] } });
    const models = panel(view, "Models");
    const spend = models.querySelector('tr[data-model="anthropic/claude-sonnet-4.5"] [data-spend]');
    assert.equal(spend.textContent, "Included");
    assert.ok(spend.querySelector('[data-tip="$10.50 at API prices"]'));
    assert.doesNotMatch(models.textContent, /\$10\.50/, "no list price printed in the table");
    assert.equal(models.querySelector('tr[data-model="meta/llama-free"] [data-spend]').textContent, "—");
    await view.unmount();
  });

  test("search narrows; the switch, the star and the bulk actions post", async () => {
    const calls = serve({
      "POST /api/providers/openrouter/models/openai%2Fgpt-5.5/toggle": { ok: true },
      "POST /api/providers/openrouter/models/anthropic%2Fclaude-sonnet-4.5/toggle": { ok: true },
      "POST /api/providers/openrouter/models/meta%2Fllama-free/toggle": { ok: true },
      "POST /api/providers/openrouter/fetch-models": { fetched: 4, added: 1 },
    });
    const { view } = await open();
    const models = () => panel(view, "Models");
    await typeInto(view, models().querySelector('input[aria-label="Search models"]'), "llama");
    assert.deepEqual([...models().querySelectorAll("tr[data-model]")].map((r) => r.dataset.model), ["meta/llama-free"]);
    await typeInto(view, models().querySelector('input[aria-label="Search models"]'), "");
    await view.act(() => models().querySelector('[aria-label="Make openai/gpt-5.5 the default"]').click());
    await settle(view);
    assert.deepEqual(posts(calls).at(-1), ["/api/providers/openrouter/models/openai%2Fgpt-5.5/toggle", { enabled: true }], "a model made default is switched on first");
    assert.deepEqual(globalThis.switched, [["openrouter", "openai/gpt-5.5"]]);
    assert.equal(models().querySelector('[aria-label="openai/gpt-5.5 is the default"]').getAttribute("aria-pressed"), "true");
    const before = posts(calls).length;
    await view.act(() => button(models(), "Disable all").click());
    await settle(view);
    assert.deepEqual(posts(calls).slice(before).map(([u]) => u).sort(), [
      "/api/providers/openrouter/models/anthropic%2Fclaude-sonnet-4.5/toggle",
      "/api/providers/openrouter/models/meta%2Fllama-free/toggle",
    ]);
    await view.act(() => button(models(), "Fetch models").click());
    await settle(view);
    assert.match(models().querySelector('[role="status"]').textContent, /Fetched 4, added 1 new/);
    await view.unmount();
  });
});

describe("remove provider", () => {
  test("a built-in provider loses its keys and is switched off, after one destructive confirm", async () => {
    const calls = serve();
    const { view, changes } = await open();
    await view.act(() => button(view.container, "Remove provider").click());
    await settle(view);
    assert.equal(globalThis.confirmRequests.length, 1);
    assert.equal(globalThis.confirmRequests[0].destructive, true);
    assert.deepEqual(calls.filter(([m]) => m === "DELETE").map(([, u]) => u), [`${BASE}/work`, `${BASE}/spare`]);
    assert.deepEqual(posts(calls), [["/api/providers/openrouter/toggle", { enabled: false }]]);
    assert.deepEqual(changes.at(-1), ["deleted"]);
    await view.unmount();
  });

  test("a custom provider is deleted; declining the confirm changes nothing", async () => {
    const calls = serve({ "DELETE /api/providers/custom/openrouter": { ok: true } });
    globalThis.confirmAnswer = false;
    const { view, changes } = await open({ provider: { ...PROVIDER, custom: true } });
    await view.act(() => button(view.container, "Remove provider").click());
    await settle(view);
    assert.equal(calls.filter(([m]) => m === "DELETE").length, 0);
    globalThis.confirmAnswer = true;
    await view.act(() => button(view.container, "Remove provider").click());
    await settle(view);
    assert.deepEqual(calls.filter(([m]) => m === "DELETE").map(([, u]) => u), ["/api/providers/custom/openrouter"]);
    assert.deepEqual(changes.at(-1), ["deleted"]);
    await view.unmount();
  });
});
