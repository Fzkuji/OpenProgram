// Settings → LLM Providers, the list (design §2, §4 empty state): every
// connected provider as a card or a compact row in each health and quota
// state, the Cards | Rows switch remembered across visits, Refresh and the
// Providers | Routing segment, and the picker of popular providers when
// nothing is connected.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";
import { CATALOGUE, DETECTED, NOW, STATES, UI_STUBS, button, fakeFetch, payload, settle, status } from "../_support/provider-fixtures.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": "export const useQueryClient = () => ({ invalidateQueries() {} });",
  },
  replace: {
    ...UI_STUBS,
    "/lib/prefs/settings-cache.ts": [
      "export const readCachedSettings = () => undefined;",
      "export const invalidate = () => {};",
      "export const cachedFetch = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); };",
    ].join("\n"),
    "/lib/nav/shallow-nav.ts": "export const pushPath = (path) => (globalThis.paths ??= []).push(path);",
    "/lib/net/api.ts": "export const api = { switchModel: async (...args) => (globalThis.switched ??= []).push(args) };",
    "/lib/runtime-bridge/providers.ts": "export const loadAgentSettings = () => {};",
    "/components/settings/providers/routing-overview.tsx": "import { createElement as h } from 'react'; export const RoutingOverview = () => h('div', { 'data-routing': '' });",
    "/components/settings/providers/detail.tsx": "import { createElement as h } from 'react'; export const Detail = ({ provider }) => h('div', { 'data-detail': provider.id });",
    "/components/settings/providers/auth/provider-login.tsx": "export const ProviderLogin = () => null;",
    "/components/settings/providers/auth/base-url.tsx": "export const BaseUrl = () => null;",
    "/components/settings/providers/auth/setup-hint.tsx": "export const SetupHint = () => null; export const CliInfo = () => null;",
    "/components/settings/providers/auth/api-key.tsx": "export const ApiKey = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { ProvidersSection } = await import("../../components/settings/providers/index.tsx");

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

afterEach(() => { document.body.innerHTML = ""; localStorage.removeItem("openprogram.providers-view"); globalThis.paths = []; });

describe("cards", () => {
  test("one card per connected provider, each in its quota form and health", async () => {
    serve(payload());
    const view = await open();
    const cards = [...view.container.querySelectorAll("article[data-provider]")];
    assert.deepEqual(cards.map((c) => [c.dataset.provider, c.dataset.health, c.querySelector("[data-form]").dataset.form]), [
      ["openai-codex", "ready", "windowed"],
      ["deepseek", "ready", "balance"],
      ["groq", "ready", "rate_limit"],
      ["openrouter", "rate_limited", "windowed"],
      ["google", "signed_out", "windowed"],
      ["moonshotai", "no_credit", "balance"],
      ["xai", "ready", "none"],
      ["ollama", "not_running", "none"],
    ]);
    const card = (id) => view.container.querySelector(`article[data-provider="${id}"]`);
    // Windowed: what is left, with the week row in warning.
    assert.deepEqual([...card("openai-codex").querySelectorAll("[data-limit]")].map((r) => [r.dataset.limit, r.querySelector("b").textContent, r.className.includes("warning")]),
      [["5h", "62%", false], ["week", "15%", true]]);
    assert.match(card("openai-codex").textContent, /Subscription/);
    assert.match(card("openai-codex").textContent, /3\.4M tok/);
    assert.match(card("openai-codex").textContent, /gpt-5\.5/);
    // Balance: the big figure and its pace.
    assert.match(card("deepseek").textContent, /\$18\.42balance/);
    assert.match(card("deepseek").textContent, /≈ 26 weeks at this pace/);
    // Rate limit: headroom and "No balance data".
    assert.match(card("groq").textContent, /Req\/min46 \/ 50/);
    assert.match(card("groq").textContent, /No balance data/);
    // Rate limited: the chip says until when, the key limit bar is danger.
    assert.match(card("openrouter").querySelector("[data-state]").textContent, /^Rate limited · \d\d:\d\d$/);
    assert.ok(card("openrouter").querySelector('[data-limit="key_limit"]').className.includes("danger"));
    // Signed out: dimmed and stale numbers, the fix inline, the token error on the chip.
    const google = card("google");
    assert.ok(google.querySelector("[data-form]").className.includes("dim"));
    assert.ok(google.querySelector("[data-form]").className.includes("stale"));
    assert.match(google.textContent, /Updated 3 h ago/);
    assert.ok(button(google, "Sign in again"));
    assert.equal(google.querySelector('[data-tip="token expired"] [data-state]').textContent, "Signed out");
    // No credit: Top up goes to the billing page.
    assert.equal(button(card("moonshotai"), "Top up").getAttribute("href"), "https://platform.example.test/billing");
    // A quota endpoint that failed says so, with the reason on hover.
    assert.match(card("xai").textContent, /No quota data/);
    assert.ok(card("xai").querySelector('[data-tip="HTTP 500 from the usage endpoint"]'));
    // A local server that is not running: How to start, no "No quota data".
    assert.ok(button(card("ollama"), "How to start"));
    assert.doesNotMatch(card("ollama").textContent, /No quota data/);
    await view.unmount();
  });

  test("a card opens its provider; Sign in again opens step 2 of Add provider", async () => {
    serve(payload([STATES.balance, status("openai-codex", { label: "OpenAI Codex", billing: "subscription", health: { state: "signed_out" }, fix: { action: "sign_in", url: null } }), STATES.signedOut]));
    const view = await open();
    await view.act(() => button(view.container.querySelector('article[data-provider="deepseek"]'), "DeepSeek").click());
    assert.deepEqual(globalThis.paths, ["/settings/providers/deepseek"]);
    await view.act(() => button(view.container.querySelector('article[data-provider="openai-codex"]'), "Sign in again").click());
    assert.equal(view.container.querySelector('[role="dialog"] [data-step]').dataset.step, "connect");
    // The sign-in way is chosen; the API key way of the same vendor is one tab over.
    assert.deepEqual([...view.container.querySelectorAll('[role="dialog"] [role="tab"]')].map((t) => [t.textContent, t.getAttribute("aria-selected")]),
      [["OpenAI · Sign in", "true"], ["OpenAI API · API key", "false"]]);
    // A provider outside the catalogue has no dialog step; its page opens.
    await view.act(() => button(view.container.querySelector('article[data-provider="google"]'), "Sign in again").click());
    assert.deepEqual(globalThis.paths, ["/settings/providers/deepseek", "/settings/providers/google"]);
    await view.unmount();
  });
});

describe("fix buttons", () => {
  test("Replace key opens the provider's page, where the rejected key is replaced in place", async () => {
    serve(payload([status("deepseek", { label: "DeepSeek", health: { state: "invalid_key" }, fix: { action: "replace_key", url: null } })]));
    const view = await open();
    await view.act(() => button(view.container.querySelector('article[data-provider="deepseek"]'), "Replace key").click());
    assert.deepEqual(globalThis.paths, ["/settings/providers/deepseek"]);
    assert.equal(view.container.querySelector('[role="dialog"]'), null, "Add provider would store a second key beside the rejected one");
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
    assert.equal(view.container.querySelectorAll("article[data-provider]").length, 8);
    await view.unmount();
  });
});

describe("rows fit the pane", () => {
  test("a long name ends in an ellipsis and tokens and cache give way first in a narrow pane", async () => {
    const long = "A Very Long Self-Hosted OpenAI-Compatible Provider Name";
    serve(payload([status("custom-long", { label: long })]));
    const view = await open();
    await view.act(() => button(view.container, "Rows").click());
    const label = view.container.querySelector('tr[data-provider="custom-long"] .rowLabel');
    assert.equal(label.textContent, long);
    assert.equal(label.closest("button").getAttribute("title"), long, "the whole name stays on hover");
    const wide = [...view.container.querySelectorAll("thead th")].filter((th) => th.className.includes("wide")).map((th) => th.textContent);
    assert.deepEqual(wide, ["Tokens", "Cache"]);
    await view.unmount();
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(new URL("../../components/settings/providers/status/status.module.css", import.meta.url), "utf8");
    assert.match(css, /\.rowLabel \{[^}]*text-overflow: ellipsis/);
    assert.match(css, /@container \(max-width: 880px\) \{ \.rows \.wide \{ display: none; \} \}/);
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
    assert.equal(row.querySelector(".modelChip").closest("[data-tip]").dataset.tip, model);
    const widths = [...view.container.querySelectorAll("thead th")].map((th) => th.className.match(/col\w+/)?.[0] ?? "");
    assert.deepEqual(widths, ["", "colStatus", "colQuota", "colSpend", "colTrend", "colTokens", "colCache", ""], "Provider and Default take what the fixed columns leave");
    await view.unmount();
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(new URL("../../components/settings/providers/status/status.module.css", import.meta.url), "utf8");
    assert.match(css, /\.rows \{[^}]*table-layout: fixed/);
    assert.match(css, /\.rows td \{[^}]*overflow: hidden; text-overflow: ellipsis/);
    for (const inner of [/\.rows \.chip > span:last-child \{[^}]*text-overflow: ellipsis/, /\.rows \.qvalue \{[^}]*text-overflow: ellipsis/, /\.rows \.modelChip \{ max-width: 100%/, /\.rowName \{[^}]*min-width: 0/]) {
      assert.match(css, inner);
    }
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
    assert.deepEqual([...view.container.querySelectorAll("article[data-provider]")].map((c) => c.dataset.provider), ["deepseek"]);
    await view.unmount();
  });

  test("Routing shows the routing overview in place of the list", async () => {
    serve(payload([STATES.balance]));
    const view = await open();
    await view.act(() => button(view.container, "Routing").click());
    assert.ok(view.container.querySelector("[data-routing]"));
    assert.equal(view.container.querySelector("article[data-provider]"), null);
    await view.act(() => button(view.container, "Providers").click());
    assert.ok(view.container.querySelector("article[data-provider]"));
    await view.unmount();
  });

  test("a failed read says why and retries", async () => {
    let fail = true;
    serve(() => (fail ? { status: 500, body: {} } : payload([STATES.balance])));
    const view = await open();
    assert.match(view.container.querySelector('[role="alert"]').textContent, /Couldn't load the providers: HTTP 500/);
    fail = false;
    await view.act(() => button(view.container, "Retry").click());
    await settle(view);
    assert.ok(view.container.querySelector('article[data-provider="deepseek"]'));
    await view.unmount();
  });

  test("a provider in the URL opens its page", async () => {
    serve(payload([STATES.balance]));
    const view = await open({ initialProviderId: "deepseek" });
    assert.ok(view.container.querySelector('[data-detail="deepseek"]'));
    assert.equal(view.container.querySelector("article[data-provider]"), null);
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

  test("the popular providers that could not be read say so and are read again", async () => {
    let fail = true;
    serve(payload([]), { "GET /api/providers/detected": () => (fail ? { status: 500, body: {} } : DETECTED) });
    const view = await open();
    const empty = view.container.querySelector("[data-empty]");
    assert.match(empty.querySelector('[role="alert"]').textContent, /Couldn't read the popular providers/);
    fail = false;
    await view.act(() => button(empty, "Retry").click());
    await settle(view);
    assert.equal(empty.querySelector('[role="alert"]'), null);
    assert.ok(empty.querySelector('button[data-provider="anthropic"]'));
    await view.unmount();
  });

  test("a popular tile opens Add provider at its key step; Anthropic is always an API key", async () => {
    serve(payload([]));
    const view = await open();
    await view.act(() => view.container.querySelector('[data-empty] button[data-provider="anthropic"]').click());
    const dialog = view.container.querySelector('[role="dialog"]');
    assert.equal(dialog.querySelector("[data-step]").dataset.step, "connect");
    assert.ok(dialog.querySelector('input[type="password"][aria-label="API key"]'));
    assert.equal(dialog.querySelector('[role="tab"]'), null, "Anthropic has no sign-in variant");
    await view.unmount();
  });
});

describe("plan name", () => {
  test("a subscription whose plan is known shows it as the billing tag, on the card and in the row", async () => {
    serve(payload([{ ...STATES.windowed, plan: "plus" }]));
    const view = await open();
    const card = view.container.querySelector('article[data-provider="openai-codex"]');
    assert.equal(card.querySelector("[data-plan]").textContent, "Plus");
    assert.doesNotMatch(card.textContent, /Subscription/);
    await view.act(() => button(view.container, "Rows").click());
    const row = view.container.querySelector('tr[data-provider="openai-codex"]');
    assert.equal(row.querySelector("[data-plan]").textContent, "Plus");
    assert.ok(row.querySelector('[data-tip="Included in the Plus plan · $41.20 at API prices"]'));
    await view.unmount();
  });

  test("an unknown plan adds nothing: Subscription on the card, Included in the row", async () => {
    serve(payload([STATES.windowed]));
    const view = await open();
    assert.equal(view.container.querySelector("[data-plan]"), null);
    assert.match(view.container.querySelector('article[data-provider="openai-codex"]').textContent, /Subscription/);
    await view.act(() => button(view.container, "Rows").click());
    assert.match(view.container.querySelector('tr[data-provider="openai-codex"]').textContent, /Included/);
    assert.equal(view.container.querySelector("[data-plan]"), null);
    await view.unmount();
  });
});

process.on("exit", () => { Date.now = realNow; });
