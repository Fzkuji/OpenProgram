// The reply card of a spent quota (docs/reference/design/providers/
// provider-management.html#chat): it names the provider and the limit, says
// when it resets, offers Continue with the Worker's suggestion, Other model…
// and Wait, and a spent balance offers Top up. Nothing switches by itself:
// only a button re-sends, with the model it names, on this branch.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader, STUBS } from "../../../_support/load.mjs";
import { installDom, render } from "../../../_support/dom.mjs";

const passthrough = "import { createElement as h, cloneElement } from 'react';";
installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": [
      "export const useQuery = ({ queryKey, enabled = true }) => {",
      "  const key = JSON.stringify(queryKey);",
      "  (globalThis.__asked ??= []).push({ key, enabled });",
      "  if (!enabled) return { data: undefined };",
      "  if (queryKey[0] === 'provider-fallback') return { data: globalThis.__offer };",
      "  return { data: (globalThis.__queries ?? {})[key] };",
      "};",
    ].join("\n"),
  },
  replace: {
    "/components/ui/dropdown-menu.tsx": [
      passthrough,
      "export const DropdownMenu = ({ children }) => h('div', { 'data-menu': '' }, children);",
      "export const DropdownMenuTrigger = ({ children }) => children;",
      "export const DropdownMenuContent = ({ children }) => h('div', { 'data-menu-content': '' }, children);",
      "export const DropdownMenuItem = ({ children, onSelect, className }) => h('button', { 'data-item': '', className, onClick: onSelect }, children);",
    ].join("\n"),
    "/components/chat/top-bar/chips/model-mark.tsx": `${passthrough} export const ModelMark = ({ provider }) => h('i', { 'data-mark': provider });`,
    "/components/chat/top-bar/menus/agent-selector.tsx": `${passthrough} export const AgentSelector = ({ onPick }) => h('button', { 'data-full-picker': '', onClick: () => onPick('openrouter', 'or-9') }, 'all models');`,
    "/lib/session-store/index.ts": [
      "const state = { agentSettingsBySession: { s1: { chat: { provider: 'openai-codex', model: 'openai-codex:gpt-6.1-sol' } } }, tokens: { s1: { total_used: 90000 } } };",
      "export const useSessionStore = (select) => select(state);",
    ].join("\n"),
    "/lib/models/fallback.ts": [
      "export const bareModel = (p, m) => m && p && m.startsWith(p + ':') ? m.slice(p.length + 1) : (m || '');",
      "export const fetchFallback = async () => globalThis.__offer;",
      "export const continueWith = async (...args) => { (globalThis.__resent ??= []).push(args); };",
    ].join("\n"),
    "/lib/net/api.ts": "export const api = { listPickerModels: async () => ({}) };",
    "/lib/format-utils/toast.ts": "export const showToast = () => {};",
  },
});
installDom();

const { createElement: h, Fragment, act } = await import("react");
const click = (el) => act(async () => { el.click(); await new Promise((r) => setTimeout(r, 0)); });
const { renderToStaticMarkup } = await import("react-dom/server");
const card = await import("../../../../components/chat/messages/bubbles/quota-reply-card.tsx");
const { QuotaActions, QuotaCardHead, QuotaIcon } = card;
globalThis.__card = card;
const S = await import("../../../../lib/models/provider-state.ts");

const NOW = Date.now() / 1000;
const reset = new Date(); reset.setHours(23, 59, 0, 0);
const RESET = reset.getTime() / 1000 > NOW ? reset.getTime() / 1000 : NOW + 60;
const codexCause = {
  cause: "quota_exhausted", http_status: 429, provider: "openai-codex", limit: "5h", resets_at: RESET, account: null,
  other_limits: [{ label: "Week", left_fraction: 0.71, resets_at: null }],
};
const kimiCause = { cause: "quota_exhausted", http_status: 402, provider: "kimi", limit: null, resets_at: null, account: null, other_limits: [] };
const picker = {
  models: [
    { id: "gpt-6.1-sol", name: "GPT-6.1-Sol", provider: "openai-codex", providerLabel: "OpenAI Codex" },
    { id: "kimi-k2", name: "Kimi K2", provider: "kimi", providerLabel: "Kimi" },
  ],
  providers: {
    kimi: { id: "kimi", configured: true, state: "no_credit", until: null, fix: { action: "top_up", url: "https://platform.moonshot.ai/console/pay" },
      quota: null, balance: { amount: 0, unit: "cny", label: "Balance" }, stale: false, attention: true },
  },
};
const OFFER = {
  suggestion: { provider: "deepseek", provider_label: "DeepSeek", model: "deepseek-v4", name: "deepseek-v4", context_window: 128000, reason: "most_used" },
  alternatives: [{ provider: "openrouter", provider_label: "OpenRouter", model: "openai/gpt-6.1-sol", name: "GPT-6.1-Sol", context_window: 400000, reason: "same_model" }],
};
globalThis.__queries = { [JSON.stringify(S.PICKER_QUERY_KEY)]: picker };

test("the title names the provider and the limit; the line says when it resets and what else is left", () => {
  const html = renderToStaticMarkup(h(Fragment, null, h(QuotaIcon, { cause: codexCause }), h(QuotaCardHead, { cause: codexCause })));
  assert.match(html, /data-quota-title="true">OpenAI Codex 5h limit reached</);
  assert.match(html, /data-quota-detail="true">Resets \d\d:\d\d · Week 71% left</);
  assert.match(html, /color:var\(--warning-soft\)/);
});

test("a spent balance reads as no credit, with the balance", () => {
  const html = renderToStaticMarkup(h(QuotaCardHead, { cause: kimiCause }));
  assert.match(html, />Kimi has no credit</);
  assert.match(html, />Balance ¥0\.00</);
});

test("a spent balance that names its limit still reads as no credit", () => {
  const named = { ...kimiCause, limit: "Balance" };
  const html = renderToStaticMarkup(h(QuotaCardHead, { cause: named }));
  assert.match(html, />Kimi has no credit</);
  assert.doesNotMatch(html, /limit reached/);
});

test("Continue with the suggestion re-sends this turn with that model, and nothing else does", async () => {
  globalThis.__offer = OFFER;
  globalThis.__resent = [];
  globalThis.__asked = [];
  const view = await render(h(QuotaActions, { msg: { id: "reply-1" }, cause: codexCause, sessionId: "s1" }));
  // The offer is asked for the model that failed and the conversation's size.
  const asked = globalThis.__asked.find((q) => q.key.startsWith('["provider-fallback"'));
  assert.equal(asked.key, '["provider-fallback","openai-codex","gpt-6.1-sol",90]');
  assert.deepEqual(globalThis.__resent, []);
  const buttons = [...view.container.querySelectorAll("button")].map((b) => b.textContent);
  assert.deepEqual(buttons.filter((t) => /Continue|Other|Wait/.test(t)), ["Continue with deepseek-v4", "Other model…", "Wait"]);
  await click(view.container.querySelector("[data-quota-continue]"));
  assert.deepEqual(globalThis.__resent, [["s1", "reply-1", "deepseek", "deepseek-v4"]]);
  await view.unmount();
});

test("Other model… offers the models with room first, then every model", async () => {
  globalThis.__offer = OFFER;
  globalThis.__resent = [];
  const view = await render(h(QuotaActions, { msg: { id: "reply-1" }, cause: codexCause, sessionId: "s1" }));
  const menu = view.container.querySelector("[data-menu-content]");
  assert.match(menu.textContent, /Has room[^]*GPT-6\.1-Sol[^]*OpenRouter[^]*all models/);
  await click(menu.querySelector("[data-item]"));
  await click(menu.querySelector("[data-full-picker]"));
  assert.deepEqual(globalThis.__resent[0], ["s1", "reply-1", "openrouter", "openai/gpt-6.1-sol"]);
  await view.unmount();
});

test("Wait keeps the model and folds the buttons away", async () => {
  globalThis.__offer = OFFER;
  globalThis.__resent = [];
  const view = await render(h(QuotaActions, { msg: { id: "reply-1" }, cause: codexCause, sessionId: "s1" }));
  await click(view.container.querySelector("[data-quota-wait]"));
  assert.equal(view.container.querySelector("[data-quota-actions]"), null);
  assert.deepEqual(globalThis.__resent, []);
  await view.unmount();
});

test("no credit offers Top up on the billing page and no Wait; no offer shows no Continue", async () => {
  globalThis.__offer = { suggestion: null, alternatives: [] };
  const view = await render(h(QuotaActions, { msg: { id: "reply-2" }, cause: kimiCause, sessionId: "s1" }));
  const top = view.container.querySelector("[data-quota-top-up]");
  assert.equal(top.getAttribute("href"), "https://platform.moonshot.ai/console/pay");
  assert.equal(top.getAttribute("target"), "_blank");
  assert.equal(view.container.querySelector("[data-quota-wait]"), null);
  assert.equal(view.container.querySelector("[data-quota-continue]"), null);
  assert.ok(view.container.querySelector("[data-quota-other]"));
  await view.unmount();
});

test("the card keeps the raw error under its details, between the reset line and the buttons", () => {
  globalThis.__offer = OFFER;
  const { QuotaErrorCard } = globalThis.__card;
  const html = renderToStaticMarkup(h(QuotaErrorCard, { msg: { id: "reply-1" }, cause: codexCause, sessionId: "s1", after: h("i", { "data-after": "" }) },
    h("details", { className: "turn-error-details" }, "raw")));
  assert.match(html, /data-quota-title[^]*data-quota-detail[^]*<details class="turn-error-details">raw<\/details>[^]*data-quota-actions[^]*data-after/);
  const embedded = renderToStaticMarkup(h(QuotaErrorCard, { msg: { id: "reply-1" }, cause: codexCause, sessionId: "s1", embedded: true }));
  assert.doesNotMatch(embedded, /data-quota-actions/);
});
