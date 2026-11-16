// Add provider (design §4): pick → a key checked the moment it is pasted,
// with one line for each result → the default model → Start chatting. Also
// an environment key's Use, a local server's Connect, and the custom form.
// Keys here are placeholders answered in-process; nothing leaves the test.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";
import { CATALOGUE, DETECTED, SUGGESTED, UI_STUBS, button, check, fakeFetch, settle, typeInto } from "../../_support/provider-fixtures.mjs";

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
    "/lib/net/api.ts": "export const api = { switchModel: async (...args) => { (globalThis.switched ??= []).push(args); return {}; } };",
    "/lib/runtime-bridge/providers.ts": "export const loadAgentSettings = () => {};",
    "/components/settings/providers/auth/provider-login.tsx": "import { createElement as h } from 'react'; export const ProviderLogin = ({ provider, onChanged }) => h('button', { type: 'button', 'data-login': provider.id, onClick: () => onChanged?.() }, 'Sign in with browser'); export const MethodChoices = () => null;",
    "/components/settings/providers/auth/base-url.tsx": "import { createElement as h } from 'react'; export const BaseUrl = () => h('div', { 'data-base-url': '' });",
    "/components/settings/providers/auth/setup-hint.tsx": "import { createElement as h } from 'react'; export const SetupHint = () => h('div', { 'data-setup-hint': '' }); export const CliInfo = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { AddProviderDialog } = await import("../../../components/settings/providers/add/add-provider-dialog.tsx");

const KEY = "sk-test-placeholder-0000";
const connected = [];
const opened = [];

async function openDialog(start = { step: "pick" }, detected = DETECTED) {
  let isOpen = true;
  const props = () => ({
    open: isOpen, start, detected, catalogue: CATALOGUE,
    onOpenChange: (next) => { isOpen = next; },
    onConnected: () => connected.push(1),
    onOpenProvider: (id) => opened.push(id),
  });
  const view = await render(h(AddProviderDialog, props()));
  await settle(view);
  return { view, isOpen: () => isOpen };
}

const step = (view) => view.container.querySelector("[data-step]")?.dataset.step;
const keyInput = (view) => view.container.querySelector('input[id^="key-"]');
const line = (view) => view.container.querySelector("[data-result]");

async function pasteKey(view, value = KEY) {
  const input = keyInput(view);
  await typeInto(view, input, value);
  await view.act(() => input.dispatchEvent(Object.assign(new Event("keydown", { bubbles: true }), { key: "Enter" })));
  await settle(view);
}

afterEach(() => { document.body.innerHTML = ""; connected.length = 0; opened.length = 0; globalThis.switched = []; globalThis.__routerCalls?.splice(0); });

describe("pick", () => {
  test("search narrows every provider; picking one goes to its key step", async () => {
    fakeFetch({});
    const { view } = await openDialog();
    assert.equal(step(view), "pick");
    await typeInto(view, view.container.querySelector('input[aria-label="Search providers"]'), "gro");
    assert.deepEqual([...view.container.querySelectorAll('[role="listitem"]')].map((r) => r.textContent), ["Groq"]);
    await view.act(() => view.container.querySelector('[role="listitem"]').click());
    assert.equal(step(view), "connect");
    assert.ok(keyInput(view));
    await view.act(() => button(view.container, "Back").click());
    assert.equal(step(view), "pick");
    await view.unmount();
  });
});

describe("key step", () => {
  test("a working key: Key works · balance · models, then the default model and Start chatting", async () => {
    const calls = fakeFetch({
      "POST /api/providers/deepseek/credentials": { ok: true, credential: { id: "default" }, check: check("works", { balance: { amount: 18.42, unit: "usd", label: "Balance" }, model_count: 5, models: SUGGESTED, enabled_models: [] }) },
      "POST /api/providers/deepseek/toggle": { ok: true },
      "POST /api/providers/deepseek/models/deepseek-chat/toggle": { ok: true },
    });
    const { view, isOpen } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    assert.equal(button(view.container, "Continue").disabled, true, "Continue waits for a usable key");
    await pasteKey(view);
    assert.equal(line(view).dataset.result, "works");
    assert.equal(line(view).textContent, "Key works · $18.42 · 5 models");
    const [, url, body] = calls.find(([m, u]) => m === "POST" && u.endsWith("/credentials"));
    assert.equal(url, "/api/providers/deepseek/credentials");
    assert.deepEqual(body, { api_key: KEY, validate: true });
    assert.equal(connected.length, 1);

    await view.act(() => button(view.container, "Continue").click());
    assert.equal(step(view), "model");
    const radios = [...view.container.querySelectorAll('[role="radio"]')];
    assert.deepEqual(radios.map((r) => [r.dataset.value, r.getAttribute("aria-checked")]), [["deepseek-chat", "true"], ["deepseek-reasoner", "false"]]);
    assert.match(view.container.textContent, /\+3 more · 0 enabled/);
    assert.match(view.container.querySelector('[data-model="deepseek-chat"]').textContent, /128K · \$0\.27 \/ \$1\.10/);
    await view.act(() => button(view.container, "Start chatting").click());
    await settle(view);
    assert.ok(calls.some(([m, u]) => m === "POST" && u === "/api/providers/deepseek/toggle"), "the provider is switched on");
    assert.ok(calls.some(([m, u]) => m === "POST" && u === "/api/providers/deepseek/models/deepseek-chat/toggle"), "the model is switched on");
    assert.deepEqual(globalThis.switched, [["deepseek", "deepseek-chat"]]);
    assert.equal(isOpen(), false);
    assert.deepEqual(globalThis.__routerCalls?.at(-1), ["push", "/chat"]);
    await view.unmount();
  });

  const failures = [
    ["rejected", { status: 400, body: { error: "invalid", check: check("rejected", { http_status: 401, fix_page: "keys", fix_url: "https://platform.deepseek.example.test/api_keys" }) } }, "Key rejected (401)Get a key", false],
    ["no_credit", { ok: true, credential: { id: "default" }, check: check("no_credit", { http_status: 402, fix_page: "billing", fix_url: "https://platform.deepseek.example.test/top_up" }) }, "Key works · no creditTop up", true],
    ["not_available", { ok: true, credential: { id: "default" }, check: check("not_available", { http_status: 403 }) }, "Not available in your region or plan (403)", false],
    ["unreachable", { ok: true, credential: { id: "default" }, check: check("unreachable") }, "Can't reach api.deepseek.comRetry", false],
    ["unverified", { ok: true, credential: { id: "default" }, check: check("unverified") }, "Key saved · not verified", true],
  ];
  for (const [result, answer, words, usable] of failures) {
    test(`${result}: one line says so${usable ? ", and Continue is open" : ""}`, async () => {
      fakeFetch({ "POST /api/providers/deepseek/credentials": answer });
      const { view } = await openDialog();
      await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
      await pasteKey(view);
      assert.equal(line(view).dataset.result, result);
      assert.equal(line(view).textContent.trim(), words);
      assert.equal(button(view.container, "Continue").disabled, !usable);
      if (result === "rejected") {
        assert.equal(keyInput(view).getAttribute("aria-invalid"), "true");
        assert.equal(line(view).querySelector("a").getAttribute("href"), "https://platform.deepseek.example.test/api_keys");
      }
      if (result === "no_credit") assert.equal(line(view).querySelector("a").getAttribute("href"), "https://platform.deepseek.example.test/top_up");
      await view.unmount();
    });
  }

  test("unreachable retries the stored key's check", async () => {
    const calls = fakeFetch({
      "POST /api/providers/deepseek/credentials": { ok: true, credential: { id: "default" }, check: check("unreachable") },
      "POST /api/providers/deepseek/credentials/default/check": { credential: { id: "default" }, check: check("works", { model_count: 5 }) },
    });
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    await pasteKey(view);
    await view.act(() => button(line(view), "Retry").click());
    await settle(view);
    assert.ok(calls.some(([m, u]) => m === "POST" && u === "/api/providers/deepseek/credentials/default/check"));
    assert.equal(line(view).dataset.result, "works");
    assert.equal(button(view.container, "Continue").disabled, false);
    await view.unmount();
  });

  test("a refused save gives the reason, and a lost connection says so", async () => {
    let lost = false;
    fakeFetch({ "POST /api/providers/deepseek/credentials": () => (lost ? new Error("offline") : { status: 400, body: { error: "This key is already in the list" } }) });
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    await pasteKey(view);
    assert.match(view.container.querySelector('[role="dialog"]').textContent, /This key is already in the list/);
    lost = true;
    await pasteKey(view, `${KEY}-2`);
    assert.match(view.container.querySelector('[role="dialog"]').textContent, /Couldn't save the key/);
    await view.unmount();
  });

  test("Advanced folds the base URL away", async () => {
    fakeFetch({});
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    assert.equal(view.container.querySelector("[data-base-url]"), null);
    await view.act(() => button(view.container, "Advanced").click());
    assert.ok(view.container.querySelector("[data-base-url]"));
    await view.unmount();
  });
});

describe("other ways in", () => {
  test("an environment key's Use checks it and goes straight to the model step", async () => {
    const calls = fakeFetch({
      "POST /api/providers/deepseek/credentials/env%3ADEEPSEEK_API_KEY/check": { credential: { id: "env:DEEPSEEK_API_KEY" }, check: check("works", { models: SUGGESTED, model_count: 2, enabled_models: ["deepseek-chat"], provider_enabled: true }) },
    });
    const { view } = await openDialog();
    await view.act(() => button(view.container.querySelector('[data-env="DEEPSEEK_API_KEY"]'), "Use").click());
    await settle(view);
    assert.deepEqual(calls.find(([m]) => m === "POST").slice(1), ["/api/providers/deepseek/credentials/env%3ADEEPSEEK_API_KEY/check", null]);
    assert.equal(step(view), "model");
    assert.doesNotMatch(view.container.textContent, /more ·/, "no '+N more' when every model is listed");
    await view.unmount();
  });

  test("picking a provider whose key is in the environment offers that key first, with paste as the alternative", async () => {
    const calls = fakeFetch({
      "POST /api/providers/deepseek/credentials/env%3ADEEPSEEK_API_KEY/check": { credential: { id: "env:DEEPSEEK_API_KEY" }, check: check("works", { models: SUGGESTED, model_count: 2, enabled_models: [], provider_enabled: true }) },
    });
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    assert.equal(step(view), "connect");
    const offered = view.container.querySelector('[data-env="DEEPSEEK_API_KEY"]');
    assert.ok(offered, "the environment key is offered on the key step");
    assert.match(offered.textContent, /DEEPSEEK_API_KEY/);
    assert.match(offered.textContent, /sk-…9f2c/);
    const input = keyInput(view);
    assert.ok(input, "pasting stays possible");
    assert.ok(offered.compareDocumentPosition(input) & 4, "the environment key comes before the paste field");
    assert.match(view.container.textContent, /Or paste a key/);
    await view.act(() => button(offered, "Use").click());
    await settle(view);
    assert.deepEqual(calls.filter(([m]) => m === "POST").map(([, u, b]) => [u, b]), [["/api/providers/deepseek/credentials/env%3ADEEPSEEK_API_KEY/check", null]]);
    assert.ok(!JSON.stringify(calls).includes("api_key"), "the page sends the variable's name, never a key");
    assert.equal(step(view), "model");
    await view.unmount();
  });

  test("arriving at one provider's key step (?add=<id>) offers its environment key too", async () => {
    fakeFetch({});
    const choice = { id: "deepseek", label: "DeepSeek", auth: "api_key", console_url: null, env_var: null, variants: [] };
    const { view } = await openDialog({ step: "connect", choice });
    assert.ok(view.container.querySelector('[data-env="DEEPSEEK_API_KEY"]'));
    await view.unmount();
  });

  test("a key another provider reads first is stored for the provider on screen; a key already stored for it is not offered", async () => {
    const calls = fakeFetch({ "POST /api/providers/openrouter/credentials/env%3ASHARED_API_KEY/check": { credential: { id: "env:SHARED_API_KEY" }, check: check("unverified") } });
    const detected = {
      ...DETECTED,
      env: [...DETECTED.env, { env_var: "SHARED_API_KEY", provider: "deepseek", label: "DeepSeek", providers: ["deepseek", "openrouter"], masked: "sk-…77aa", connected: true }],
    };
    const openrouter = { id: "openrouter", label: "OpenRouter", auth: "api_key", console_url: null, env_var: null, variants: [] };
    let { view } = await openDialog({ step: "connect", choice: openrouter }, detected);
    await view.act(() => button(view.container.querySelector('[data-env="SHARED_API_KEY"]'), "Use").click());
    await settle(view);
    assert.deepEqual(calls.filter(([m]) => m === "POST").map(([, u]) => u), ["/api/providers/openrouter/credentials/env%3ASHARED_API_KEY/check"]);
    assert.equal(step(view), "model");
    assert.match(view.container.querySelector('[role="dialog"]').textContent, /OpenRouter/);
    await view.unmount();

    ({ view } = await openDialog({ step: "connect", choice: { id: "openai", label: "OpenAI API", auth: "api_key", console_url: null, env_var: "OPENAI_API_KEY", variants: [] } }));
    assert.equal(view.container.querySelector("[data-env]"), null, "OPENAI_API_KEY is already stored for OpenAI");
    assert.ok(keyInput(view));
    await view.unmount();
  });

  test("an environment key that is rejected shows the line, not the key, and pasting one is the way on", async () => {
    fakeFetch({
      "POST /api/providers/deepseek/credentials/env%3ADEEPSEEK_API_KEY/check": { credential: { id: "env:DEEPSEEK_API_KEY" }, check: check("rejected", { http_status: 401 }) },
      "POST /api/providers/deepseek/credentials": { ok: true, credential: { id: "default" }, check: check("works", { models: SUGGESTED, model_count: 2, enabled_models: [] }) },
    });
    const { view } = await openDialog({ step: "env", row: DETECTED.env[0] });
    await settle(view);
    assert.equal(step(view), "connect");
    assert.equal(line(view).dataset.result, "rejected");
    assert.equal(view.container.querySelector("[data-env]"), null, "the rejected variable is not offered again");
    assert.match(view.container.textContent, /Or paste a key/);
    const input = keyInput(view);
    assert.ok(input, "a key can be pasted after the environment's is rejected");
    assert.equal(input.value, "", "the environment key never reaches the page");
    assert.ok(line(view).compareDocumentPosition(input) & 4, "the rejection line comes first");
    await pasteKey(view);
    const lines = [...view.container.querySelectorAll("[data-result]")].map((l) => l.dataset.result);
    assert.deepEqual(lines, ["works"], "the pasted key keeps its own line once stored");
    assert.equal(keyInput(view), input, "the paste field stays the same field");
    assert.equal(button(view.container, "Continue").disabled, false);
    await view.unmount();
  });

  test("sign-in providers sign in, then pick a model", async () => {
    fakeFetch({
      "GET /api/providers/openai-codex/models": { models: [{ id: "gpt-5.5", enabled: true, tools: true }, { id: "gpt-5.5-mini", enabled: false }] },
      "GET /api/providers/list": { providers: CATALOGUE },
    });
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="openai-codex"]').click());
    await view.act(() => view.container.querySelector('[data-login="openai-codex"]').click());
    await settle(view);
    assert.equal(step(view), "model");
    assert.deepEqual([...view.container.querySelectorAll('[role="radio"]')].map((r) => r.dataset.value), ["gpt-5.5", "gpt-5.5-mini"]);
    await view.unmount();
  });

  test("a running local server connects with one button", async () => {
    const calls = fakeFetch({
      "POST /api/providers/ollama/toggle": { ok: true },
      "POST /api/providers/ollama/fetch-models": { fetched: 1, added: 1 },
      "GET /api/providers/ollama/models": { models: [{ id: "qwen3:8b", enabled: true }] },
      "GET /api/providers/list": { providers: CATALOGUE.map((p) => (p.id === "ollama" ? { ...p, enabled: true } : p)) },
    });
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="ollama"]').click());
    assert.match(view.container.textContent, /http:\/\/127\.0\.0\.1:11434/);
    await view.act(() => button(view.container, "Connect").click());
    await settle(view);
    assert.deepEqual(calls.filter(([m]) => m === "POST").map(([, u]) => u), ["/api/providers/ollama/toggle", "/api/providers/ollama/fetch-models"]);
    assert.equal(step(view), "model");
    await view.unmount();
  });

  test("a local server that is not running says so with how to start it", async () => {
    fakeFetch({});
    const detected = { ...DETECTED, local: [{ ...DETECTED.local[0], running: false }] };
    const { view } = await openDialog({ step: "connect", choice: { id: "ollama", label: "Ollama", auth: "local", console_url: null, env_var: null, variants: [] } }, detected);
    assert.equal(view.container.querySelector("[data-state]").dataset.state, "not_running");
    await view.unmount();
  });

  test("a model step with no models offers the provider's page", async () => {
    fakeFetch({ "POST /api/providers/deepseek/credentials": { ok: true, credential: { id: "default" }, check: check("unverified") } });
    const { view, isOpen } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="deepseek"]').click());
    await pasteKey(view);
    await view.act(() => button(view.container, "Continue").click());
    assert.match(view.container.textContent, /No models listed yet/);
    await view.act(() => button(view.container, "Open provider").click());
    assert.deepEqual(opened, ["deepseek"]);
    assert.equal(isOpen(), false);
    await view.unmount();
  });

  test("Custom opens the custom provider form", async () => {
    fakeFetch({});
    const { view } = await openDialog();
    await view.act(() => view.container.querySelector('button[data-provider="custom"]').click());
    assert.equal(step(view), "custom");
    assert.ok(view.container.querySelector("input"));
    await view.unmount();
  });
});
