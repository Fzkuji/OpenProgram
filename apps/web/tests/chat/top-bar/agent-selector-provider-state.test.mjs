// The model picker carries each provider's state
// (docs/reference/design/providers/provider-management.html#chat): a row
// that needs action shows its word and leads to the fix, a half-used window
// shows a ring, the submenu header carries the quota rows, the menu ends
// with Manage providers, and with nothing connected it opens Add provider.
// The chip carries a mark only when its provider needs action.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";

const passthrough = "import { createElement as h, cloneElement } from 'react';";
installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": [
      "export const useQuery = ({ queryKey }) => ({ data: (globalThis.__queries ?? {})[JSON.stringify(queryKey)], isError: !!(globalThis.__errors ?? {})[JSON.stringify(queryKey)] });",
    ].join("\n"),
  },
  replace: {
    "/components/ui/dropdown-menu.tsx": [
      passthrough,
      "export const DropdownMenuItem = ({ asChild, children, onSelect, className }) => asChild ? cloneElement(children, { 'data-item': '' }) : h('div', { 'data-item': '', className, onClick: onSelect }, children);",
      "export const DropdownMenuSub = ({ children }) => h('div', { 'data-sub': '' }, children);",
      "export const DropdownMenuSubTrigger = ({ children, className, ...rest }) => h('div', { 'data-sub-trigger': '', className, ...rest }, children);",
      "export const DropdownMenuSubContent = ({ children }) => h('div', { 'data-sub-content': '' }, children);",
      "export const DropdownMenu = ({ children }) => h('div', { 'data-menu': '' }, children);",
      "export const DropdownMenuTrigger = ({ children }) => children;",
      "export const DropdownMenuContent = ({ children }) => h('div', { 'data-menu-content': '' }, children);",
    ].join("\n"),
    "/components/ui/tooltip.tsx": `${passthrough} export const HoverTip = ({ label, children }) => h('div', { 'data-hover-tip': '' }, label, children); export const TipBody = ({ title, detail }) => h('span', { 'data-tip-body': '' }, title, '|', detail);`,
    "/lib/session-store/index.ts": "export const useSessionStore = (select) => select({ composerSettingsBySession: {} });",
    "/lib/runtime-bridge/ui.ts": "export const closeAllPopovers = () => {};",
    "/lib/tabs/top-bar-sync.ts": "export const pushAgentSettings = () => {}; export const pushBranchInfo = () => {}; export const pushStatusBadge = () => {};",
    "/components/chat/top-bar/menus/project-menu.tsx": "export const ProjectBadge = () => null;",
    "/components/chat/top-bar/chips/working-dir-chips.tsx": "export const WorkingDirChips = () => null;",
    "/components/chat/top-bar/menus/permission-menu.tsx": "export const PermissionBadge = () => null;",
    "/components/chat/top-bar/menus/agent-config-menu.tsx": "export const AgentConfigBadge = () => null;",
    "/components/solar-icons/index.ts": `${passthrough} export const SolarIcon = ({ name }) => h('svg', { 'data-solar': name });`,
    "/components/chat/top-bar/chips/model-mark.tsx": `${passthrough} export const ModelMark = ({ provider }) => h('i', { 'data-mark': provider });`,
    "/components/chat/top-bar/bound-chat.tsx": "export const useBoundChat = () => ({ sessionId: 's1', chatKey: 's1' });",
    "/lib/agents/model-pick.ts": "export const pickBranchModel = async (...args) => { (globalThis.__picks ??= []).push(args); };",
    "/lib/agents/start-conversation.ts": "export const updatePendingAgentModel = async () => false;",
    "/lib/net/api.ts": "export const api = { listPickerModels: async () => ({}), listEnabledModels: async () => [] };",
    "/lib/net/fetch-client.ts": "export const jsonFetch = async () => ({});",
    "/lib/format-utils/toast.ts": "export const showToast = () => {};",
  },
});

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { AgentSelector } = await import("../../../components/chat/top-bar/menus/agent-selector.tsx");
const { TriggerMark } = await import("../../../components/chat/top-bar/menus/provider-quota.tsx");
const { AgentBadge } = await import("../../../components/chat/top-bar/index.tsx");
const S = await import("../../../lib/models/provider-state.ts");

const model = (provider, id, label) => ({ id, name: id.toUpperCase(), provider, providerLabel: label, enabled: true, capabilities: [], context: 400000 });
const state = (id, fields = {}) => ({
  id, configured: true, state: "ready", until: null, fix: null, quota: null, balance: null,
  stale: false, attention: false, ...fields,
});
const limit = (key, label, left, fields = {}) => ({
  limit_key: key, label, unit: "percent", source: "headers", fetched_at: 0, used: null, limit: null,
  remaining: null, used_fraction: 1 - left, left_fraction: left, window_minutes: 300, resets_at: null,
  status: "ok", stale: false, ...fields,
});

function menu({ models, providers, status, current = {} }) {
  globalThis.__queries = {
    [JSON.stringify(S.PICKER_QUERY_KEY)]: models ? { models, providers } : undefined,
    [JSON.stringify(S.PICKER_LIMITS_QUERY_KEY)]: status,
  };
  return renderToStaticMarkup(h(AgentSelector, { currentProvider: current.provider, currentModel: current.model, onClose() {} }));
}

const MODELS = [
  model("openai-codex", "gpt-6.1-sol", "OpenAI Codex"),
  model("deepseek", "deepseek-v4", "DeepSeek"),
  model("kimi", "kimi-k2", "Kimi"),
  model("xai-grok", "grok-5", "Grok Subscription"),
  model("anthropic", "claude-opus", "Anthropic"),
  model("openrouter", "or-1", "OpenRouter"),
];
const STATUS = {
  generated_at: 0, stale_after_s: 900,
  providers: [
    { id: "openai-codex", label: "OpenAI Codex", quota: { kind: "windowed", account: null, fetched_at: 0, stale: false, read_error: null, read_at: null,
      limits: [limit("primary", "5h", 0.08), limit("secondary", "Week", 0.71)] } },
    { id: "deepseek", label: "DeepSeek", quota: { kind: "balance", account: null, fetched_at: 0, stale: false, read_error: null, read_at: null,
      limits: [limit("balance", "Balance", null, { unit: "usd", remaining: 18.42, left_fraction: null, used_fraction: null })] } },
  ],
};
const PROVIDERS = {
  "openai-codex": state("openai-codex", { attention: true, quota: { limit_key: "primary", label: "5h", left_fraction: 0.08, resets_at: null, tone: "warning", ring: true, warn: true } }),
  deepseek: state("deepseek", { balance: { amount: 18.42, unit: "usd", label: "Balance" } }),
  kimi: state("kimi", { state: "no_credit", attention: true, fix: { action: "top_up", url: "https://platform.moonshot.ai/console/pay" } }),
  "xai-grok": state("xai-grok", { state: "signed_out", attention: true, fix: { action: "sign_in", url: null } }),
  anthropic: state("anthropic", { configured: false, state: "not_configured", attention: true, fix: { action: "connect", url: null } }),
  openrouter: state("openrouter", { state: "rate_limited", until: 1_900_000_000 }),
};

const rowOf = (html, provider) => {
  const at = html.indexOf(`data-mark="${provider}"`);
  assert.ok(at >= 0, `no row for ${provider}`);
  const start = Math.max(html.lastIndexOf("<a ", at), html.lastIndexOf("data-sub-trigger", at));
  return html.slice(start, html.indexOf(`data-mark=`, at + 10) === -1 ? undefined : html.indexOf(`data-mark=`, at + 10));
};

test("a half-used window shows its ring with the reset in its hint; the current model stays named", () => {
  const html = menu({ models: MODELS, providers: PROVIDERS, status: STATUS, current: { provider: "openai-codex", model: "gpt-6.1-sol" } });
  const codex = rowOf(html, "openai-codex");
  assert.match(codex, /data-quota-ring="warning"/);
  assert.match(codex, /data-tip="5h · 8% left"/);
  assert.match(codex, /GPT-6\.1-SOL/);
  // A quiet provider shows only its model count, no ring.
  const deepseek = html.slice(html.indexOf('data-mark="deepseek"'), html.indexOf('data-mark="kimi"'));
  assert.doesNotMatch(deepseek.split("data-sub-content")[0], /data-quota-ring/);
});

test("the submenu header carries the quota rows, worst first, and the balance", () => {
  const html = menu({ models: MODELS, providers: PROVIDERS, status: STATUS });
  const codexSub = html.slice(html.indexOf('data-mark="openai-codex"'), html.indexOf('data-mark="deepseek"'));
  assert.match(codexSub, /data-quota-row="warning"[^]*5h[^]*8%[^]*data-quota-row="normal"[^]*Week[^]*71%/);
  const deepseekSub = html.slice(html.indexOf('data-mark="deepseek"'), html.indexOf('data-mark="kimi"'));
  assert.match(deepseekSub, /Balance[^]*\$18\.42/);
});

test("a provider that needs action shows its word and leads to its fix", () => {
  const html = menu({ models: MODELS, providers: PROVIDERS, status: STATUS });
  // Not set up: straight to the provider's setup step.
  assert.match(html, /<a href="\/settings\/providers\?add=anthropic"[^>]*data-tip="Add a key or sign in"[^>]*data-picker-fix="not_configured"[^]*?Not set up/);
  // Signed out: straight to its sign-in.
  assert.match(html, /<a href="\/settings\/providers\/xai-grok"[^>]*data-picker-fix="signed_out"[^]*?data-state-word="warning"[^>]*>Sign in</);
  // The empty chevron slot of a fix row is centred like the chevron, so the
  // row keeps the height of the rows that open a submenu.
  const fixRow = html.slice(html.indexOf('data-picker-fix="signed_out"'));
  assert.match(fixRow.slice(0, fixRow.indexOf("</a>")), /<span class="ml-1\.5 size-4 self-center" aria-hidden="true"><\/span>/);
  // No credit with a billing page: Top up there, or open the provider.
  const kimi = html.slice(html.indexOf('data-mark="kimi"'), html.indexOf('data-mark="xai-grok"'));
  assert.match(kimi, /data-state-word="danger"[^>]*>No credit</);
  assert.match(kimi, /href="https:\/\/platform\.moonshot\.ai\/console\/pay" target="_blank"[^]*Top up/);
  assert.match(kimi, /href="\/settings\/providers\/kimi"[^]*Open provider/);
  assert.doesNotMatch(kimi, /KIMI-K2/);
  // Rate limited is temporary: a word with the time, the models still open.
  const router = html.slice(html.indexOf('data-mark="openrouter"'));
  assert.match(router, /data-state-word="warning"[^>]*>Rate limited · /);
  assert.match(router, /OR-1/);
});

test("the menu ends with Manage providers", () => {
  const html = menu({ models: MODELS, providers: PROVIDERS, status: STATUS });
  assert.match(html, /<a href="\/settings\/providers"[^>]*data-picker-manage[^]*Manage providers<\/span><\/a>$/);
});

test("one provider shows its state above its models, with its fix", () => {
  const html = menu({ models: [MODELS[2]], providers: { kimi: PROVIDERS.kimi }, status: { ...STATUS, providers: [] } });
  assert.match(html, /Kimi[^]*No credit[^]*Top up[^]*Open provider[^]*KIMI-K2/);
});

test("with no provider connected the menu says so and opens Add provider", () => {
  const html = menu({ models: [], providers: {}, status: { generated_at: 0, stale_after_s: 900, providers: [] } });
  assert.match(html, /No model provider connected\./);
  assert.match(html, /<a href="\/settings\/providers\?add=1"[^>]*data-picker-add-provider[^]*Add provider/);
  assert.doesNotMatch(html, /Manage providers/);
  // Connected but nothing enabled keeps the enable-models note.
  const some = menu({ models: [], providers: {}, status: STATUS });
  assert.match(some, /agent\.no_enabled_models/);
  assert.match(some, /Manage providers/);
});

test("a failed read of the models says so instead of an empty menu", () => {
  globalThis.__errors = { [JSON.stringify(S.PICKER_QUERY_KEY)]: true };
  try {
    const html = menu({ models: undefined, providers: {}, status: undefined });
    assert.match(html, /data-picker-error[^>]*>Could not load models\./);
  } finally {
    globalThis.__errors = {};
  }
});

test("providers with no state keep today's rows", () => {
  const html = menu({ models: MODELS.slice(0, 2), providers: {}, status: undefined });
  assert.doesNotMatch(html, /data-quota-ring|data-state-word|data-picker-fix/);
  assert.match(html, />1<\/span>/);
});

test("the chip's mark is a dot in the state's tone", () => {
  const html = renderToStaticMarkup(h(TriggerMark, { tone: "danger" }));
  assert.match(html, /data-trigger-mark="danger"/);
  assert.match(html, /background:var\(--danger-soft\)/);
});

test("the model chip is marked only when its provider needs action, with the detail in its tooltip", () => {
  const chip = (provider) => {
    globalThis.__queries = { [JSON.stringify(S.PICKER_QUERY_KEY)]: { models: MODELS, providers: PROVIDERS } };
    return renderToStaticMarkup(h(AgentBadge, { id: "agent", locked: false, provider, model: MODELS.find((m) => m.provider === provider).id }));
  };
  const codex = chip("openai-codex");
  assert.match(codex, /data-trigger-mark="warning"/);
  assert.match(codex, /OpenAI Codex · 5h · 8% left/);
  const kimi = chip("kimi");
  assert.match(kimi, /data-trigger-mark="danger"/);
  assert.match(kimi, /Kimi · No credit/);
  // The dot is read out to a screen reader, not only drawn.
  assert.match(kimi, /<span class="sr-only">No credit<\/span>/);
  const deepseek = chip("deepseek");
  assert.doesNotMatch(deepseek, /data-trigger-mark/);
  assert.match(deepseek, /Talks with you and plans the work/);
});
