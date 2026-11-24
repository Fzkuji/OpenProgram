// Settings → Model services, the list (design §2, §4 empty state): every
// connected provider as a card or a compact row in each health and quota
// state, the Cards | Rows switch remembered across visits, Refresh and the
// Providers | Routing | Behavior segment, and the picker of popular providers when
// nothing is connected.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";
import { CATALOGUE, DETECTED, NOW, STATES, UI_STUBS, button, cred, credentialList, fakeFetch, payload, settle, status, way } from "../../_support/provider-fixtures.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": "export const useQueryClient = () => ({ invalidateQueries() {} });",
  },
  replace: {
    ...UI_STUBS,
    "/lib/nav/shallow-nav.ts": "export const pushPath = (path) => (globalThis.paths ??= []).push(path);",
    "/lib/net/api.ts": "export const api = { switchModel: async (...args) => (globalThis.switched ??= []).push(args) };",
    "/lib/runtime-bridge/providers.ts": "export const loadAgentSettings = () => {};",
    "/components/settings/sections/schema-settings.tsx": "import { createElement as h } from 'react'; export const SchemaSettings = ({ group }) => h('div', { 'data-schema': group });",
    "/components/settings/providers/routing-overview.tsx": "import { createElement as h } from 'react'; export const RoutingOverview = () => h('div', { 'data-routing': '' });",
    "/components/settings/providers/detail.tsx": "import { createElement as h } from 'react'; export const Detail = ({ provider }) => h('div', { 'data-detail': provider.id });",
    "/components/settings/providers/auth/provider-login.tsx": "export const ProviderLogin = () => null; export const MethodChoices = () => null;",
    "/components/settings/providers/auth/base-url.tsx": "export const BaseUrl = () => null;",
    "/components/settings/providers/auth/setup-hint.tsx": "export const SetupHint = () => null; export const CliInfo = () => null;",
    "/components/settings/providers/auth/api-key.tsx": "export const ApiKey = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { ProvidersSection } = await import("../../../components/settings/providers/index.tsx");
const m = await import("../../../components/settings/providers/status/model.ts");
const { invalidateAllSettings } = await import("../../../lib/prefs/settings-cache.ts");
console.warn = () => {};

const realNow = Date.now;
Date.now = () => NOW * 1000;

function serve(statusPayload, extra = {}) {
  return fakeFetch({
    "GET /api/providers/status": statusPayload,
    "GET /api/providers/detected": DETECTED,
    "GET /api/providers/list": { providers: CATALOGUE },
    "POST /api/providers/status/refresh": () => payload([status("deepseek", { label: "DeepSeek" })]),
    ...extra,
  });
}

async function open(props = {}) {
  const view = await render(h(ProvidersSection, props));
  await settle(view);
  return view;
}

afterEach(() => { document.body.innerHTML = ""; localStorage.removeItem("openprogram.providers-view"); globalThis.paths = []; invalidateAllSettings(); });

describe("cards", () => {
  const card = (view, id) => view.container.querySelector(`[data-slot="card"][data-provider="${id}"]`);
  const rowsOf = (el) => [...el.querySelectorAll("[data-credential], [role=button][data-provider]")];
  const end = (row) => row.querySelector("[data-end]");

  test("one card per company, one row per credential: who, up to three rings, and one last column", async () => {
    serve(payload());
    const view = await open();
    const cards = [...view.container.querySelectorAll('[data-slot="card"]')];
    assert.deepEqual(cards.map((c) => c.dataset.provider), ["openai-codex", "deepseek", "groq", "openrouter", "google", "moonshotai", "xai", "ollama"]);
    const row = (id) => card(view, id).querySelector(".credRow, [role=button]");
    // Windowed: a ring per window, shortest first, each over when it resets.
    const rings = (id) => [...row(id).querySelectorAll("[data-limit]")].map((r) => [r.dataset.limit, r.textContent]);
    assert.deepEqual(rings("openai-codex"), [["5h", `5h${m.fmtReset(NOW + 4 * 3600, NOW)}`], ["week", `Week${m.fmtReset(NOW + 3 * 86400, NOW)}`]]);
    assert.match(row("openai-codex").textContent, /Subscription/);
    assert.match(card(view, "openai-codex").textContent, /gpt-5\.5/);
    assert.equal(end(row("openai-codex")).textContent, "", "an included plan holds no amount");
    // Balance: the amount in the last column.
    assert.equal(end(row("deepseek")).textContent, "$18.42Balance");
    assert.match(row("deepseek").textContent, /API key/);
    // Rate limit: the cap under the ring.
    assert.deepEqual(rings("groq"), [["rpm", "Req/mof 50"]]);
    // Limited: amber, with when it comes back; its key limit ring in danger.
    assert.equal(end(row("openrouter")).dataset.tone, "warning");
    assert.equal(end(row("openrouter")).textContent, `Limited${m.fmtReset(NOW + 1800, NOW)}`);
    assert.deepEqual(rings("openrouter"), [["key_limit", "Limitof $20"]]);
    // Signed out: red, clickable, the row dimmed with when its numbers were read on hover.
    assert.equal(end(row("google")).dataset.tone, "danger");
    assert.match(end(row("google")).textContent, /^Signed out/);
    assert.equal(row("google").dataset.stale, "true");
    assert.ok(card(view, "google").querySelector('[data-tip="Updated 3 h ago"]'));
    assert.doesNotMatch(card(view, "google").textContent, /Updated/);
    // No credit: the balance in red.
    assert.equal(end(row("moonshotai")).dataset.tone, "danger");
    assert.match(end(row("moonshotai")).textContent, /^\$0\.00Balance/);
    // A quota that could not be read: a dashed ring with the reason on hover.
    assert.deepEqual(rings("xai").map(([k, t]) => [k, t.startsWith("Unread")]), [["unread", true]]);
    assert.ok(card(view, "xai").querySelector('[data-tip="HTTP 500 from the usage endpoint"]'));
    // A local server that is not running.
    assert.match(end(row("ollama")).textContent, /^Not running/);
    // No action words anywhere.
    assert.doesNotMatch(view.container.querySelector("[data-view]")?.parentElement?.textContent ?? view.container.textContent, /Top up|Sign in again|Replace key|How to start/);
    await view.unmount();
  });

  test("a row opens its provider; a red cell fixes what it names on the credential it is for", async () => {
    serve(payload([STATES.balance, STATES.signedOut, STATES.noCredit]));
    const opened = [];
    const realOpen = window.open;
    window.open = (...args) => { opened.push(args[0]); return null; };
    const view = await open();
    await view.act(() => card(view, "deepseek").querySelector("[role=button]").click());
    await view.act(() => card(view, "google").querySelector("[data-end]").closest("button").click());
    await view.act(() => card(view, "moonshotai").querySelector("[data-end]").closest("button").click());
    assert.deepEqual(globalThis.paths, ["/settings/providers/deepseek", "/settings/providers/google"], "the fix does not also open the row");
    assert.deepEqual(opened, ["https://platform.example.test/billing"]);
    assert.equal(view.container.querySelector('[role="dialog"]'), null, "signing in again renews the row on the page; it never adds one");
    window.open = realOpen;
    await view.unmount();
  });

  test("each credential is its own row, with its own state, name and the one in use marked", async () => {
    serve(payload([
      status("openrouter", { label: "OpenRouter", keys: [
        cred("Personal", { label: "Personal", active: true, spend_usd: 4.2 }),
        cred("Work", { label: "Work", status: "cooling", cooldown_until: NOW + 60, use_count: 2, health: { until: NOW + 60 } }),
        cred("Old", { key_tail: "a1b2", status: "invalid_key", status_at: NOW - 600 }),
        cred("Spare", { env_var: "OPENROUTER_API_KEY", enabled: false }),
      ] }),
      status("openai-codex", { label: "OpenAI Codex", billing: "subscription", keys: [cred("me", { kind: "oauth", email: "me@example.com", plan: "plus" })] }),
    ]));
    const view = await open();
    const rows = rowsOf(card(view, "openrouter"));
    assert.deepEqual(rows.map((r) => [r.querySelector("span span").textContent, end(r).textContent, end(r).dataset.tone]), [
      ["Personal", "$4.2030 days", "normal"],
      ["Work", `Cooling${m.fmtReset(NOW + 60, NOW)}`, "warning"],
      ["····a1b2", "Invalid10 m ago", "danger"],
      ["$OPENROUTER_API_KEY", "Off", "muted"],
    ]);
    assert.ok(rows[0].querySelector('[aria-label="In use"]'));
    assert.equal(rows[3].dataset.off, "true");
    assert.equal(rows[2].dataset.credential, "Old");
    const codex = rowsOf(card(view, "openai-codex"));
    assert.match(codex[0].textContent, /^me@example\.comPlus/, "the plan names the subscription");
    assert.equal(codex[0].querySelector('[aria-label="In use"]'), null, "one credential needs no mark");
    await view.unmount();
  });

  test("more than four rows fold into N more with what is wrong among them", async () => {
    serve(payload([status("openrouter", { label: "OpenRouter", keys: [
      cred("a", { label: "A", active: true }), cred("b", { label: "B" }), cred("c", { label: "C" }),
      cred("d", { label: "D", status: "invalid_key" }), cred("e", { label: "E", status: "invalid_key" }), cred("f", { label: "F", status: "invalid_key" }),
    ] })]));
    const view = await open();
    const fold = card(view, "openrouter").querySelector("[data-folded]");
    assert.match(fold.textContent, /^3 more1 invalid$/);
    assert.equal(rowsOf(card(view, "openrouter")).length, 3);
    await view.act(() => fold.click());
    assert.equal(rowsOf(card(view, "openrouter")).length, 6);
    assert.equal(card(view, "openrouter").querySelector("[data-folded]"), null);
    await view.unmount();
  });

  test("one + on the card: filled when there is no key, opening the add dialog on the list", async () => {
    serve(payload([
      status("groq", { label: "Groq", health: { state: "not_configured" }, fix: { action: "connect", url: null } }),
      status("deepseek", { label: "DeepSeek", default_model: "deepseek-chat", keys: [cred("m", { label: "Main" })] }),
      status("openai-codex", { label: "OpenAI Codex", billing: "subscription", takes_keys: false, keys: [cred("me", { kind: "oauth", email: "me@example.com" })] }),
    ]), { "GET /api/providers/groq/credentials": credentialList("groq", [], { auth: { env_vars: ["GROQ_API_KEY"] } }) });
    const view = await open();
    assert.equal(button(card(view, "groq"), "Add key").dataset.variant, "default", "the first key is the way on");
    assert.equal(button(card(view, "deepseek"), "Add key").dataset.variant, "ghost");
    assert.ok(button(card(view, "openai-codex"), "Add account"));
    assert.match(card(view, "groq").textContent, /No key yet/);
    assert.match(card(view, "deepseek").textContent, /deepseek-chat/);
    await view.act(() => button(card(view, "groq"), "Add key").click());
    await settle(view);
    const dialog = view.container.querySelector('[role="dialog"]');
    assert.match(dialog.querySelector("h2").textContent, /Add a key to Groq/);
    assert.ok(dialog.querySelector("#key-groq"));
    assert.deepEqual(globalThis.paths, [], "adding stays on the list");
    await view.unmount();
  });

  test("the add dialog opened for another provider never shows the last one's ways", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const methods = [{ id: "browser", label: "Sign in with ChatGPT", label_zh: "", description: "", description_zh: "" }];
    serve(payload([
      status("groq", { label: "Groq", health: { state: "not_configured" } }),
      status("openai-codex", { label: "OpenAI Codex", billing: "subscription", health: { state: "not_configured" } }),
    ]), {
      "GET /api/providers/groq/credentials": credentialList("groq", []),
      "GET /api/providers/openai-codex/credentials": async () => { await gate; return credentialList("openai-codex", [], { auth: { methods } }); },
    });
    const view = await open();
    await view.act(() => button(card(view, "groq"), "Add key").click());
    await settle(view);
    assert.ok(view.container.querySelector("#key-groq"));
    await view.act(() => button(view.container.querySelector('[role="dialog"]'), "Cancel").click());
    await view.act(() => button(card(view, "openai-codex"), "Add account").click());
    const step = () => view.container.querySelector('[role="dialog"] [data-step]').dataset.step;
    assert.equal(step(), "loading", "Groq's key step is not shown for Codex");
    assert.equal(view.container.querySelector('[role="dialog"] input'), null);
    await view.act(async () => { release(); await gate; });
    await settle(view);
    assert.equal(step(), "login");
    await view.unmount();
  });

  test("a change to a provider's credentials in any tab reads the cards again, once per burst", async () => {
    let reads = 0;
    serve(null, {
      "GET /api/providers/status": () => {
        reads += 1;
        return payload([status("deepseek", { label: "DeepSeek", keys: reads > 1 ? [cred("A"), cred("B")] : [cred("A")] })]);
      },
    });
    const view = await open();
    assert.equal(rowsOf(card(view, "deepseek")).length, 1);
    await view.act(async () => {
      for (let i = 0; i < 3; i += 1) window.dispatchEvent(new CustomEvent("op:provider-credentials-changed", { detail: { provider: "deepseek" } }));
      await new Promise((resolve) => setTimeout(resolve, 320));
    });
    await settle(view);
    assert.equal(reads, 2, "three changes in a burst are read once");
    assert.equal(rowsOf(card(view, "deepseek")).length, 2);
    await view.unmount();
  });
});

describe("company cards", () => {
  test("a key every way of a company lists is one row, and a way that refuses it adds none", async () => {
    const ways = ["xiaomi", "xiaomi-token-plan-cn", "xiaomi-token-plan-sgp"];
    const bare = (id, extra = {}) => way(status(id, { label: id, keys: [cred("k1", { key_tail: "ZauU", ...extra })] }), "xiaomi", "Xiaomi", id, id);
    serve(payload(ways.map((id) => bare(id))));
    let view = await open();
    assert.deepEqual([...view.container.querySelectorAll("[data-credential]")].map((r) => r.dataset.provider), ["xiaomi"]);
    await view.unmount();
    serve(payload([bare("xiaomi"), bare("xiaomi-token-plan-cn", { status: "invalid_key", health: { state: "invalid_key" } }), bare("xiaomi-token-plan-sgp")]));
    view = await open();
    assert.deepEqual([...view.container.querySelectorAll("[data-credential]")].map((r) => r.dataset.provider), ["xiaomi"]);
    await view.unmount();
  });


  test("a company reached several ways is one card; a key two ways list is one row that names both and opens the one with something to say", async () => {
    serve(payload([
      way(status("opencode", { label: "OpenCode", keys: [cred("ZauU", { key_tail: "ZauU", spend_usd: 1.2, use_count: 4 })], default_model: "glm-5.1" }), "opencode", "OpenCode", "Zen · pay as you go", "Zen"),
      way(status("opencode-go", { label: "OpenCode Go", billing: "subscription", default_model: "deepseek-v4-flash", keys: [cred("ZauU", { key_tail: "ZauU", status: "cooling", cooldown_until: NOW + 60, use_count: 2, health: { until: NOW + 60 } })] }), "opencode", "OpenCode", "Go · monthly subscription", "Go"),
      status("deepseek", { label: "DeepSeek" }),
    ]));
    const view = await open();
    const cards = [...view.container.querySelectorAll('[data-slot="card"]')];
    assert.deepEqual(cards.map((c) => c.dataset.family), ["opencode", "deepseek"]);
    const company = cards[0];
    assert.equal(company.querySelector('[data-slot="card-title"]').textContent, "OpenCode");
    const rows = [...company.querySelectorAll("[data-credential]")];
    assert.deepEqual(rows.map((r) => [r.dataset.provider, r.querySelector("span span").textContent]), [["opencode-go", "····ZauU"]]);
    assert.match(rows[0].textContent, /····ZauUZen · Go/);
    assert.equal(rows[0].querySelector("[data-end]").dataset.tone, "warning");
    await view.act(() => rows[0].click());
    assert.deepEqual(globalThis.paths, ["/settings/providers/opencode-go"]);
    // One + with a menu of the ways.
    const add = button(company, "Add");
    assert.ok(add);
    const items = [...company.querySelectorAll("[role=menuitem]")].map((i) => i.textContent);
    assert.deepEqual(items, ["Zen · pay as you go", "Go · monthly subscription"]);
    // The cards stay in list order in the document, so Tab reads them as they are seen.
    const cells = [...view.container.querySelectorAll("[data-masonry-key]")];
    assert.deepEqual(cells.map((c) => c.dataset.masonryKey), ["opencode", "deepseek"]);
    assert.notEqual(cells[0].style.gridColumn, cells[1].style.gridColumn, "two columns side by side");
    // Rows: the company's row with its ways indented under it.
    await view.act(() => button(view.container, "Rows").click());
    const tr = [...view.container.querySelectorAll("tbody tr")];
    assert.deepEqual(tr.map((r) => r.dataset.family ? `company:${r.dataset.family}` : r.dataset.provider),
      ["company:opencode", "opencode", "opencode-go", "deepseek"]);
    assert.match(tr[0].textContent, /OpenCode/);
    assert.match(tr[1].textContent, /^Zen/);
    assert.equal(tr[2].querySelector("button").title, "Go · monthly subscription", "the whole title on hover");
    assert.ok(button(tr[2], "Go").className.includes("pl-[30px]"));
    await view.unmount();
  });
});

describe("rows", () => {
  test("Rows draws one compact row per provider and is remembered", async () => {
    serve(payload());
    let view = await open();
    assert.equal(view.container.querySelector("[data-view]").dataset.view, "cards");
    await view.act(() => button(view.container, "Rows").click());
    assert.equal(localStorage.getItem("openprogram.providers-view"), "rows");
    const rows = [...view.container.querySelectorAll("tr[data-provider]")];
    assert.equal(rows.length, 8);
    const row = (id) => view.container.querySelector(`tr[data-provider="${id}"]`);
    assert.match(row("openai-codex").textContent, /62% 5h · \d\d:\d\d · 15% Week/);
    assert.match(row("openai-codex").textContent, /Included/);
    assert.match(row("deepseek").textContent, /\$18\.42 balance · ≈ 26 wk/);
    assert.match(row("deepseek").textContent, /\$6\.30/);
    assert.match(row("groq").textContent, /46\/50 rpm · no balance data/);
    assert.ok(row("openrouter").querySelector('[data-form]').className.includes("danger"));
    assert.ok(button(row("google"), "Sign in again"));
    assert.ok(row("google").className.includes("dimRow"));
    assert.ok(row("google").querySelector("[data-form]").className.includes("stale"), "a stale snapshot is dimmed in rows too");
    assert.ok(!row("deepseek").querySelector("[data-form]").className.includes("stale"));
    assert.match(row("xai").textContent, /No quota data/);
    await view.unmount();

    view = await open();
    assert.equal(view.container.querySelector("[data-view]").dataset.view, "rows", "the next visit opens on rows");
    await view.act(() => button(view.container, "Cards").click());
    assert.equal(localStorage.getItem("openprogram.providers-view"), "cards");
    assert.equal(view.container.querySelectorAll('[data-slot="card"][data-provider]').length, 8);
    await view.unmount();
  });
});

describe("rows fit the pane", () => {
  test("a long name ends in an ellipsis and tokens and cache give way first in a narrow pane", async () => {
    const long = "A Very Long Self-Hosted OpenAI-Compatible Provider Name";
    serve(payload([status("custom-long", { label: long })]));
    const view = await open();
    await view.act(() => button(view.container, "Rows").click());
    const label = view.container.querySelector('tr[data-provider="custom-long"] td button .truncate');
    assert.equal(label.textContent, long);
    assert.equal(label.closest("button").getAttribute("title"), long, "the whole name stays on hover");
    const wide = [...view.container.querySelectorAll("thead th")].filter((th) => th.className.includes("wide")).map((th) => th.textContent);
    assert.deepEqual(wide, ["Tokens", "Cache"]);
    await view.unmount();
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(new URL("../../../components/settings/providers/status/status.module.css", import.meta.url), "utf8");
    assert.match(css, /@container \(max-width: 1040px\) \{ \.rows \.wide \{ display: none; \} \}/);
  });

  test("every cell ends inside its column with the whole text on hover: a long status, quota and model never widen the table", async () => {
    const model = "openrouter/some-vendor/an-extremely-long-model-identifier-preview-2026-11-01";
    serve(payload([{ ...STATES.windowed, id: "long-one", default_model: model, health: { state: "rate_limited", until: NOW + 3600, detail: null } }]));
    const view = await open();
    await view.act(() => button(view.container, "Rows").click());
    const row = view.container.querySelector('tr[data-provider="long-one"]');
    const cells = [...row.querySelectorAll("td")];
    assert.match(cells[1].querySelector("[data-tip]").dataset.tip, /^Rate limited · \d\d:\d\d$/, "the status chip's whole text on hover");
    const quota = row.querySelector("[data-form] .qvalue");
    assert.equal(quota.closest("[data-tip]").dataset.tip, quota.textContent, "the whole quota line on hover");
    assert.ok(!row.querySelector("[data-form]").className.includes("min-w-[2"), "the quota cell has no minimum width to push the table");
    const chip = row.querySelector('[data-slot="badge"].font-mono');
    assert.equal(chip.closest("[data-tip]").dataset.tip, model);
    assert.ok(chip.className.includes("max-w-full") && chip.querySelector(".truncate"), "the model badge ends in an ellipsis");
    const widths = [...view.container.querySelectorAll("thead th")].map((th) => th.className.match(/col\w+/)?.[0] ?? "");
    assert.deepEqual(widths, ["", "colStatus", "colQuota", "colSpend", "colTrend", "colTokens", "colCache", "colDefault"], "Provider takes what the other columns leave");
    await view.unmount();
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(new URL("../../../components/settings/providers/status/status.module.css", import.meta.url), "utf8");
    assert.match(css, /\.rows \{[^}]*table-layout: fixed/);
    assert.match(css, /\.rows td \{[^}]*overflow: hidden; text-overflow: ellipsis/);
    assert.match(css, /\.rows \.qvalue \{[^}]*text-overflow: ellipsis/);
  });
});

describe("header", () => {
  test("Updated is the newest read, Refresh reads every quota again", async () => {
    const calls = serve(payload([STATES.balance, STATES.windowed]));
    const view = await open();
    assert.match(view.container.textContent, /Updated 2 min ago/);
    await view.act(() => button(view.container, "Refresh").click());
    await settle(view);
    assert.ok(calls.some(([method, url]) => method === "POST" && url === "/api/providers/status/refresh"));
    assert.deepEqual([...view.container.querySelectorAll('[data-slot="card"][data-provider]')].map((c) => c.dataset.provider), ["deepseek"]);
    await view.unmount();
  });

  test("Routing shows the routing overview in place of the list", async () => {
    serve(payload([STATES.balance]));
    const view = await open();
    await view.act(() => button(view.container, "Routing").click());
    assert.ok(view.container.querySelector("[data-routing]"));
    assert.equal(view.container.querySelector('[data-slot="card"][data-provider]'), null);
    await view.act(() => button(view.container, "Providers").click());
    assert.ok(view.container.querySelector('[data-slot="card"][data-provider]'));
    await view.unmount();
  });

  test("Behavior shows the Models settings group in place of the list", async () => {
    serve(payload([STATES.balance]));
    const view = await open();
    await view.act(() => button(view.container, "Behavior").click());
    assert.ok(view.container.querySelector('[data-schema="Models"]'));
    assert.equal(view.container.querySelector('[data-slot="card"][data-provider]'), null);
    await view.unmount();
  });

  test("a failed read says nothing and fills in when a quiet retry answers", async () => {
    let fail = true;
    serve(() => (fail ? { status: 500, body: {} } : payload([STATES.balance])));
    const view = await open();
    assert.equal(view.container.querySelector('[role="alert"]'), null);
    assert.doesNotMatch(view.container.textContent, /Couldn't|Retry|Loading/);
    assert.equal(view.container.querySelector("[data-empty]"), null, "a failed read is not an empty list");
    fail = false;
    await view.act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
    await settle(view);
    assert.ok(view.container.querySelector('[data-slot="card"][data-provider="deepseek"]'));
    await view.unmount();
  });

  test("coming back shows the kept cards in the first render", async () => {
    serve(payload([STATES.balance]));
    let view = await open();
    await view.unmount();
    globalThis.fetch = () => new Promise(() => {});
    view = await render(h(ProvidersSection));
    assert.ok(view.container.querySelector('[data-slot="card"][data-provider="deepseek"]'));
    await view.unmount();
  });

  test("a provider in the URL opens its page", async () => {
    serve(payload([STATES.balance]));
    const view = await open({ initialProviderId: "deepseek" });
    assert.ok(view.container.querySelector('[data-detail="deepseek"]'));
    assert.equal(view.container.querySelector('[data-slot="card"][data-provider]'), null);
    await view.unmount();
  });
});

describe("empty state", () => {
  test("nothing connected: popular providers, environment keys first with Use, local servers, All providers", async () => {
    serve(payload([]));
    const view = await open();
    const empty = view.container.querySelector("[data-empty]");
    assert.ok(empty);
    assert.match(empty.querySelector("h3").textContent, /Connect a model provider/);
    assert.equal(empty.querySelectorAll("p").length, 0, "no prose");
    // Only the unconnected environment key is offered.
    assert.deepEqual([...empty.querySelectorAll("[data-env]")].map((r) => r.dataset.env), ["DEEPSEEK_API_KEY"]);
    assert.deepEqual([...empty.querySelectorAll("button[data-provider]")].map((t) => t.dataset.provider), ["openai-codex", "anthropic", "deepseek", "ollama", "custom"]);
    assert.ok(empty.textContent.indexOf("In your environment") < empty.textContent.indexOf("Popular"));
    assert.equal(button(view.container, "Rows"), undefined, "no view switch without providers");
    await view.act(() => button(empty, "All providers (6)").click());
    assert.deepEqual([...empty.querySelectorAll('[role="listitem"]')].map((r) => r.textContent.replace("Connected", "")),
      ["Anthropic", "DeepSeek", "Groq", "Ollama", "OpenAI", "OpenAI Codex"]);
    await view.act(() => button(empty, "Back to popular").click());
    assert.ok(empty.querySelector("[data-env]"));
    await view.unmount();
  });

  test("popular providers that could not be read leave their place empty and are read again quietly", async () => {
    let fail = true;
    serve(payload([]), { "GET /api/providers/detected": () => (fail ? { status: 500, body: {} } : DETECTED) });
    const view = await open();
    const empty = view.container.querySelector("[data-empty]");
    assert.equal(empty.querySelector('[role="alert"]'), null);
    assert.equal(empty.querySelector('[role="status"]'), null, "no spinner");
    assert.doesNotMatch(empty.textContent, /Couldn't|Retry/);
    fail = false;
    await view.act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
    await settle(view);
    assert.ok(empty.querySelector('button[data-provider="anthropic"]'));
    await view.unmount();
  });

  test("a popular tile opens Add provider at its key step; Anthropic is always an API key", async () => {
    serve(payload([]));
    const view = await open();
    await view.act(() => view.container.querySelector('[data-empty] button[data-provider="anthropic"]').click());
    const dialog = view.container.querySelector('[role="dialog"]');
    assert.equal(dialog.querySelector("[data-step]").dataset.step, "connect");
    assert.ok(dialog.querySelector('input[type="password"]#key-anthropic'));
    assert.equal(dialog.querySelector('[role="tab"]'), null, "Anthropic has no sign-in variant");
    await view.unmount();
  });
});

describe("plan name", () => {
  test("a subscription whose plan is known names it, on the credential's row and in the table", async () => {
    serve(payload([{ ...STATES.windowed, plan: "plus", keys: [{ ...STATES.windowed.keys[0], plan: "plus" }] }]));
    const view = await open();
    const card = view.container.querySelector('[data-slot="card"][data-provider="openai-codex"]');
    assert.match(card.querySelector("[data-credential]").textContent, /Plus/);
    assert.doesNotMatch(card.textContent, /Subscription/);
    await view.act(() => button(view.container, "Rows").click());
    const row = view.container.querySelector('tr[data-provider="openai-codex"]');
    assert.equal(row.querySelector("[data-plan]").textContent, "Plus");
    assert.ok(row.querySelector('[data-tip="Included in the Plus plan · $41.20 at API prices"]'));
    await view.unmount();
  });

  test("an unknown plan adds nothing: Subscription on the credential, Included in the table", async () => {
    serve(payload([STATES.windowed]));
    const view = await open();
    assert.match(view.container.querySelector('[data-slot="card"][data-provider="openai-codex"] [data-credential]').textContent, /Subscription/);
    await view.act(() => button(view.container, "Rows").click());
    assert.match(view.container.querySelector('tr[data-provider="openai-codex"]').textContent, /Included/);
    assert.equal(view.container.querySelector("[data-plan]"), null);
    await view.unmount();
  });
});

process.on("exit", () => { Date.now = realNow; });
