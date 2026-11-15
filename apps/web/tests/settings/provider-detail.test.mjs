// A provider's page (design §5): the usage panel (period, cost or tokens,
// the four figures, the pace of a key limit), the keys panel (use, rotate,
// rename, replace, check, remove, add), the models table (switch, prices,
// capabilities, 30-day spend, default star, bulk actions) and Remove
// provider behind the one destructive confirm.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../_support/dom.mjs";
import { NOW, STATES, UI_STUBS, button, fakeFetch, settle, typeInto } from "../_support/provider-fixtures.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": "export const useQueryClient = () => ({ invalidateQueries() {} });",
  },
  replace: {
    ...UI_STUBS,
    "/lib/net/api.ts": "export const api = { switchModel: async (...args) => { (globalThis.switched ??= []).push(args); return {}; } };",
    "/lib/runtime-bridge/providers.ts": "export const loadAgentSettings = () => {};",
    "/components/settings/providers/auth/provider-login.tsx": "import { createElement as h } from 'react'; export const ProviderLogin = () => h('div', { 'data-login': '' });",
    "/components/settings/providers/auth/base-url.tsx": "import { createElement as h } from 'react'; export const BaseUrl = () => h('div', { 'data-base-url': '' });",
    "/components/settings/providers/auth/setup-hint.tsx": "export const SetupHint = () => null; export const CliInfo = () => null;",
    "/components/settings/providers/auth/connectivity.tsx": "export const Connectivity = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { Detail } = await import("../../components/settings/providers/detail.tsx");

const realNow = Date.now;
Date.now = () => NOW * 1000;
process.on("exit", () => { Date.now = realNow; });

const PROVIDER = { id: "openrouter", label: "OpenRouter", enabled: true, kind: "api", api_key_env: "OPENROUTER_API_KEY", supports_fetch: true, base_url: "https://openrouter.ai/api/v1" };
const STATUS = {
  ...STATES.limited,
  default_model: "openrouter/anthropic/claude-sonnet-4.5",
  keys: [
    { account: "work", credential_id: "work", kind: "api_key", status: "valid", active: true, cooldown_until: NOW + 900, last_error: null, last_used_at: NOW - 60, use_count: 3, status_at: NOW - 60 },
    { account: "spare", credential_id: "spare", kind: "api_key", status: "revoked", active: false, cooldown_until: null, last_error: "401 Unauthorized", last_used_at: null, use_count: 0, status_at: NOW - 600 },
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
const ACCOUNTS = (over = {}) => ({
  accounts: [
    { id: "work", label: "Work", name: "work", kind: "api_key", is_active: true, enabled: true, has_value: true, masked_key: "sk-or-…a1b2" },
    { id: "spare", label: "Spare", name: "spare", kind: "api_key", is_active: false, enabled: true, has_value: true, masked_key: "sk-or-…c3d4" },
  ],
  active: "work", rotation: false, strategy: "fill_first", strategies: ["fill_first", "round_robin", "least_used"], add_mode: "api_key",
  ...over,
});

function serve(extra = {}) {
  return fakeFetch({
    "GET /api/providers/openrouter/models": { models: MODELS },
    "GET /api/providers/openrouter/usage": (_, url) => USAGE(Number(new URL(url, "http://x").searchParams.get("days"))),
    "GET /api/providers/openrouter/accounts": ACCOUNTS(),
    "POST /api/providers/openrouter/accounts/use": { ok: true },
    "POST /api/providers/openrouter/accounts/rename": { ok: true },
    "POST /api/providers/openrouter/accounts/remove": { ok: true },
    "POST /api/providers/openrouter/accounts/rotation": { ok: true },
    "POST /api/providers/openrouter/accounts/spare/validate": { ok: true, check: { result: "rejected" } },
    "POST /api/providers/openrouter/accounts/spare/update": { ok: true },
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

afterEach(() => { document.body.innerHTML = ""; globalThis.confirmRequests = []; globalThis.confirmAnswer = true; globalThis.switched = []; });

describe("header", () => {
  test("back, the health chip, when it was read, Refresh and the on switch", async () => {
    serve();
    const { view, changes } = await open();
    assert.match(view.container.querySelector("h2").textContent, /OpenRouter/);
    assert.match(view.container.querySelector("[data-state]").textContent, /^Rate limited · \d\d:\d\d$/);
    assert.match(view.container.textContent, /Updated 2 min ago/);
    await view.act(() => button(view.container, "LLM Providers").click());
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
      "GET /api/providers/openrouter/accounts": ACCOUNTS(),
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
      "GET /api/providers/openrouter/accounts": ACCOUNTS(),
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

  test("a failed read says so", async () => {
    serve({ "GET /api/providers/openrouter/usage": { status: 500, body: {} } });
    const { view } = await open();
    assert.match(panel(view, "Usage").querySelector('[role="alert"]').textContent, /Couldn't read the usage/);
    await view.unmount();
  });
});

describe("keys panel", () => {
  test("one row per key: in use, masked tail, health from the key pool", async () => {
    serve();
    const { view } = await open();
    const keys = panel(view, "Keys");
    const rows = [...keys.querySelectorAll("[data-account]")];
    assert.deepEqual(rows.map((r) => r.dataset.account), ["work", "spare"]);
    assert.deepEqual(rows.map((r) => r.querySelector('[role="radio"]').getAttribute("aria-checked")), ["true", "false"]);
    assert.match(rows[0].textContent, /sk-or-…a1b2/);
    assert.match(rows[0].querySelector("[data-state]").textContent, /^Rate limited · /);
    assert.equal(rows[1].querySelector("[data-state]").textContent, "Invalid key");
    assert.ok(rows[1].querySelector('[data-tip="401 Unauthorized"]'));
    assert.doesNotMatch(keys.textContent, /sk-or-[^…]/, "only the masked tail is on the page");
    // The same limit rows as the card.
    assert.ok(keys.querySelector('[data-limit="key_limit"]'));
    await view.unmount();
  });

  test("use, check, rename, replace and remove post for that key", async () => {
    const calls = serve();
    const { view } = await open();
    const row = () => panel(view, "Keys").querySelector('[data-account="spare"]');
    await view.act(() => row().querySelector('[role="radio"]').click());
    await settle(view);
    await view.act(() => button(row(), "Check").click());
    await settle(view);
    await view.act(() => button(row(), "Rename").click());
    const name = row().querySelector('input[aria-label="Key name"]');
    await typeInto(view, name, "Backup");
    await view.act(() => name.dispatchEvent(Object.assign(new Event("keydown", { bubbles: true }), { key: "Enter" })));
    await settle(view);
    await view.act(() => button(row(), "Replace key").click());
    const replacement = panel(view, "Keys").querySelector('input[aria-label="New API key"]');
    await typeInto(view, replacement, "sk-or-test-placeholder");
    await view.act(() => button(panel(view, "Keys"), "Save").click());
    await settle(view);
    await view.act(() => button(row(), "Remove").click());
    await settle(view);
    assert.deepEqual(globalThis.confirmRequests.map((r) => [r.title, r.destructive]), [['Remove "Spare"?', true]]);
    assert.deepEqual(posts(calls), [
      ["/api/providers/openrouter/accounts/use", { id: "spare" }],
      ["/api/providers/openrouter/accounts/spare/validate", {}],
      ["/api/providers/openrouter/accounts/rename", { id: "spare", name: "Backup" }],
      ["/api/providers/openrouter/accounts/spare/update", { api_key: "sk-or-test-placeholder", validate: true }],
      ["/api/providers/openrouter/accounts/remove", { id: "spare" }],
    ]);
    await view.unmount();
  });

  test("arriving from a card's Replace key, the key in use opens on its replacement field", async () => {
    serve();
    const { view } = await open({ status: { ...STATUS, health: { state: "invalid_key", until: null, detail: null }, fix: { action: "replace_key", url: null } } });
    const keys = panel(view, "Keys");
    assert.ok(keys.querySelector('input[aria-label="New API key"]'));
    await view.unmount();
    serve();
    const plain = await open();
    assert.equal(panel(plain.view, "Keys").querySelector('input[aria-label="New API key"]'), null, "no field without a rejected key");
    await plain.view.unmount();
  });

  test("a refused replacement says why and keeps the field", async () => {
    serve({ "POST /api/providers/openrouter/accounts/spare/update": { status: 400, body: { error: "Key rejected (401)" } } });
    const { view } = await open();
    const keys = panel(view, "Keys");
    await view.act(() => button(keys.querySelector('[data-account="spare"]'), "Replace key").click());
    await typeInto(view, keys.querySelector('input[aria-label="New API key"]'), "sk-or-bad-placeholder");
    await view.act(() => button(keys, "Save").click());
    await settle(view);
    assert.equal(keys.querySelector('[role="alert"]').textContent, "Key rejected (401)");
    assert.ok(keys.querySelector('input[aria-label="New API key"]'));
    await view.unmount();
  });

  test("rotation appears with two keys and switches each key to a checkbox", async () => {
    const calls = serve();
    const { view } = await open();
    const keys = panel(view, "Keys");
    assert.deepEqual([...keys.querySelectorAll('[role="tab"]')].map((t) => t.textContent), ["Off", "In order", "Spread", "Least used"]);
    await view.act(() => button(keys, "Spread").click());
    await settle(view);
    assert.deepEqual(posts(calls).at(-1), ["/api/providers/openrouter/accounts/rotation", { enabled: true, strategy: "round_robin" }]);
    await view.unmount();

    serve({ "GET /api/providers/openrouter/accounts": ACCOUNTS({ rotation: true, strategy: "round_robin" }) });
    const second = await open();
    const rotating = panel(second.view, "Keys");
    assert.equal(rotating.querySelectorAll('[role="checkbox"]').length, 2);
    assert.equal(rotating.querySelector('[role="radio"]'), null);
    await second.view.unmount();

    serve({ "GET /api/providers/openrouter/accounts": ACCOUNTS({ accounts: ACCOUNTS().accounts.slice(0, 1) }) });
    const single = await open();
    assert.equal(panel(single.view, "Keys").querySelector('[role="tab"]'), null, "no rotation with one key");
    await single.view.unmount();
  });

  test("with no key the key field is open; Add key opens it beside existing keys; Base URL is under Advanced", async () => {
    serve({ "GET /api/providers/openrouter/accounts": ACCOUNTS({ accounts: [] }) });
    let { view } = await open();
    let keys = panel(view, "Keys");
    assert.ok(keys.querySelector('input[aria-label="API key"]'));
    assert.equal(button(keys, "Add key"), undefined);
    await view.unmount();

    serve();
    ({ view } = await open());
    keys = panel(view, "Keys");
    assert.equal(keys.querySelector('input[aria-label="API key"]'), null);
    await view.act(() => button(keys, "Add key").click());
    assert.ok(keys.querySelector('input[aria-label="API key"]'));
    assert.equal(keys.querySelector("[data-base-url]"), null);
    await view.act(() => button(keys, "Advanced · Base URL").click());
    assert.ok(keys.querySelector("[data-base-url]"));
    await view.unmount();
  });

  test("a sign-in provider adds an account by signing in", async () => {
    serve({ "GET /api/providers/openrouter/accounts": ACCOUNTS({ accounts: [], add_mode: "login" }) });
    const { view } = await open();
    const keys = panel(view, "Keys");
    assert.ok(keys.querySelector("[data-login]"));
    assert.equal(keys.querySelector('input[aria-label="API key"]'), null);
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
      "GET /api/providers/openrouter/accounts": ACCOUNTS(),
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
    assert.deepEqual(posts(calls), [
      ["/api/providers/openrouter/accounts/remove", { id: "work" }],
      ["/api/providers/openrouter/accounts/remove", { id: "spare" }],
      ["/api/providers/openrouter/toggle", { enabled: false }],
    ]);
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
