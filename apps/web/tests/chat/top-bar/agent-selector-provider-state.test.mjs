// The model picker carries each provider's state
// (docs/reference/design/providers/provider-management.html#chat): a row
// that needs action shows its word and leads to the fix, a half-used window
// shows a ring, the submenu header carries the quota rows, the menu ends
// with Manage providers, and with nothing connected it opens Add provider.
// A provider with two or more credentials ends with its key row, whose
// submenu pins the conversation to one. The chip carries a mark only when
// its provider needs action.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";

const passthrough = "import { createElement as h, cloneElement } from 'react';";
installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@tanstack/react-query": [
      "export const useQuery = ({ queryKey }) => ({ data: (globalThis.__queries ?? {})[JSON.stringify(queryKey)], isError: !!(globalThis.__errors ?? {})[JSON.stringify(queryKey)] });",
      "export const useQueryClient = () => ({ setQueryData: (key, data) => { (globalThis.__queries ??= {})[JSON.stringify(key)] = data; } });",
    ].join("\n"),
  },
  replace: {
    "/components/ui/dropdown-menu.tsx": [
      passthrough,
      "export const DropdownMenuItem = ({ asChild, children, onSelect, className, disabled, ...rest }) => asChild ? cloneElement(children, { 'data-item': '' }) : h('div', { 'data-item': '', className, onClick: onSelect, 'data-disabled': disabled ? '' : undefined, ...rest }, children);",
      "export const DropdownMenuSub = ({ children }) => h('div', { 'data-sub': '' }, children);",
      "export const DropdownMenuSubTrigger = ({ children, className, ...rest }) => h('div', { 'data-sub-trigger': '', className, ...rest }, children);",
      "export const DropdownMenuSubContent = ({ children }) => h('div', { 'data-sub-content': '' }, children);",
      "export const DropdownMenuSeparator = ({ className }) => h('div', { className, role: 'separator' });",
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
    "/components/chat/top-bar/menus/harness-menu.tsx": "export const HarnessBadge = () => null; export const useHarness = () => ({ harness: null });",
    "/components/solar-icons/index.ts": `${passthrough} export const SolarIcon = ({ name }) => h('svg', { 'data-solar': name });`,
    "/components/chat/top-bar/chips/model-mark.tsx": `${passthrough} export const ModelMark = ({ provider }) => h('i', { 'data-mark': provider }); export const AgentMark = ({ harness }) => h('i', { 'data-agent': harness });`,
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
  // Each line at the menu's secondary size: the label in the primary text
  // colour, the value on the right edge, a full-width bar under a window.
  assert.match(codexSub, /class="flex flex-col gap-2 px-1\.5 pt-0\.5 pb-2" data-quota-rows/);
  assert.match(codexSub, /data-quota-row="warning"><div class="flex min-w-0 items-baseline gap-1\.5 whitespace-nowrap text-fs-sm leading-\[18px\]"><span class="min-w-0 truncate text-text-primary">5h<\/span><span class="ms-auto shrink-0 ps-3 text-\(--warning-soft\)">8%<\/span><\/div><div role="progressbar"[^>]*aria-valuenow="8" data-slot="progress" data-tone="warning" class="[^"]*bg-\(--meter-track\) mt-1\.5"><div data-slot="progress-indicator" class="[^"]*bg-\(--warning-soft\)" style="transform:translateX\(-92%\)"><\/div><\/div>/);
  // The bar is the kit's Progress, the one every usage row draws.
  assert.doesNotMatch(codexSub, /style="width:/);
  // An amount is the line alone, no bar.
  assert.match(deepseekSub, /data-quota-row="normal"><div class="[^"]*"><span class="min-w-0 truncate text-text-primary">Balance<\/span><span class="ms-auto shrink-0 ps-3 text-text-primary">\$18\.42<\/span><\/div><\/div>/);
});

test("the header shows the ring's limit even before the status read arrives", () => {
  const providers = { ...PROVIDERS, "openai-codex": state("openai-codex", {
    stale: true,
    quota: { limit_key: "primary", label: "Weekly", left_fraction: 0.16, resets_at: null, tone: "warning", ring: true, warn: true },
    balance: { amount: 38991.82, unit: "credits", label: "Credits" },
  }) };
  const html = menu({ models: MODELS, providers, status: undefined });
  const codexSub = html.slice(html.indexOf('data-mark="openai-codex"'), html.indexOf('data-mark="deepseek"'));
  assert.match(codexSub, /data-quota-row="warning" data-stale="true"[^]*Weekly[^]*text-text-muted">16%[^]*data-slot="progress-indicator" class="[^"]*opacity-50[^]*Credits[^]*38,991\.82 credits/);
});

test("a spent window keeps a red track", () => {
  const status = { ...STATUS, providers: [{ ...STATUS.providers[0], quota: { ...STATUS.providers[0].quota,
    limits: [limit("primary", "Weekly", 0, { status: "exhausted" })] } }] };
  const html = menu({ models: MODELS, providers: PROVIDERS, status });
  assert.match(html, /data-quota-row="danger"[^]*0%[^]*bg-\[color-mix\(in_oklab,var\(--danger-soft\)_24%,transparent\)\]/);
});

const cred = (id, label, fields = {}) => ({
  id, label, display_label: label, kind: "api_key", source: "web", identity: {}, key_tail: id.slice(0, 4), env_var: null,
  read_only: false, enabled: true, status: "ready", cooldown_until: null, blocked_models: {}, last_error: null,
  last_used_at: null, use_count: 0, created_at: 0, ...fields,
});
const KEYS = {
  provider: "openai-codex", strategy: "in_order", strategies: ["in_order", "evenly", "least_used"], active: "a1b2",
  auth: { methods: [], env_vars: [], check: null, keys_url: null, billing_url: null },
  credentials: [cred("a1b2", "Personal"), cred("work", "Work", { status: "cooling", cooldown_until: 4_000_000_000 }), cred("old0", "Old team key", { status: "invalid_key" })],
};
function keyMenu(keys, session) {
  globalThis.__queries = {
    [JSON.stringify(S.PICKER_QUERY_KEY)]: { models: MODELS, providers: PROVIDERS },
    [JSON.stringify(S.PICKER_LIMITS_QUERY_KEY)]: STATUS,
    [JSON.stringify(["provider-credentials", "openai-codex"])]: keys,
    [JSON.stringify(["session-credentials", "s1"])]: session,
  };
  const html = renderToStaticMarkup(h(AgentSelector, { onClose() {} }));
  return html.slice(html.indexOf('data-mark="openai-codex"'), html.indexOf('data-mark="deepseek"'));
}

test("a provider with several keys ends its models with its keys inline, not a third panel", () => {
  const codex = keyMenu(KEYS, { binding: {}, pin: {} });
  const row = codex.slice(codex.indexOf('data-picker-keys="openai-codex"'));
  assert.match(row, /This conversation uses Personal<\/div>/);
  assert.doesNotMatch(row, /data-key-mode/, "the check on Automatic says it; the label carries no hint");
  assert.doesNotMatch(row, /data-slot="dropdown-menu-sub-trigger"/, "the keys open no submenu of their own");
  assert.ok(codex.indexOf("GPT-6.1-SOL") < codex.indexOf("data-picker-keys"), "the key row follows the models");
  assert.match(row, /This conversation uses[^]*Automatic \(in order\)/);
  const choice = (id) => row.slice(row.indexOf(`data-credential-choice="${id}"`), row.indexOf("</div>", row.indexOf(`data-credential-choice="${id}"`)));
  assert.match(choice("auto"), /data-menu-check|svg/, "Automatic is checked while nothing is pinned");
  assert.match(choice("a1b2"), /Personal[^]*font-mono">····a1b2/);
  assert.match(choice("work"), /Work[^]*text-text-muted">back \d\d:\d\d/, "the word is muted: the row is already greyed out");
  assert.match(row, /data-disabled=""[^>]*data-credential-choice="old0"/, "a credential that cannot answer cannot be picked");
  assert.doesNotMatch(row, /data-disabled=""[^>]*data-credential-choice="a1b2"/);
  assert.match(choice("old0"), /text-text-muted">invalid/);
  assert.doesNotMatch(row, /--(warning|danger)-soft/, "no coloured words in the key submenu");
  assert.match(row, /<a href="\/settings\/providers\/openai-codex"[^>]*data-picker-manage-keys[^]*Manage keys…/);
});

test("a pinned conversation names its key and says it is pinned; one key has no row", () => {
  const pinned = keyMenu(KEYS, { binding: { "openai-codex": "a1b2" }, pin: { "openai-codex": "work" } });
  assert.match(pinned, /This conversation uses Work<\/div>/);
  const single = keyMenu({ ...KEYS, credentials: KEYS.credentials.slice(0, 1) }, { binding: {}, pin: {} });
  assert.doesNotMatch(single, /data-picker-keys/);
});

test("Manage keys changes the route on a plain click and leaves a modified one to the browser", async () => {
  const { openInPage } = await import("../../../components/chat/top-bar/menus/provider-keys.tsx");
  const went = [];
  const click = (mods = {}) => ({ metaKey: false, ctrlKey: false, shiftKey: false, ...mods, prevented: false,
    preventDefault() { this.prevented = true; } });
  const plain = click();
  openInPage(plain, "/settings/providers/x", (h) => went.push(h));
  assert.deepEqual(went, ["/settings/providers/x"]);
  assert.equal(plain.prevented, true);
  for (const mods of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }]) {
    const e = click(mods);
    openInPage(e, "/settings/providers/x", (h) => went.push(h));
    assert.equal(e.prevented, false, "the browser opens the new tab");
  }
  assert.equal(went.length, 1, "the page itself does not move");
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

test("a failed read of the models shows no notice and no false empty state", () => {
  globalThis.__errors = { [JSON.stringify(S.PICKER_QUERY_KEY)]: true };
  try {
    const html = menu({ models: undefined, providers: {}, status: undefined });
    assert.doesNotMatch(html, /Could not load models|data-picker-error/);
    assert.doesNotMatch(html, /agent\.no_enabled_models|No model provider connected/);
  } finally {
    globalThis.__errors = {};
  }
});

test("providers with no state keep today's rows", () => {
  const html = menu({ models: MODELS.slice(0, 2), providers: {}, status: undefined });
  assert.doesNotMatch(html, /data-quota-ring|data-state-word|data-picker-fix|data-quota-rows/);
  // The count; its unit shows only where the state sits under the name (a phone sheet).
  assert.match(html, />1<span class="picker-count-unit hidden"> model<\/span><\/span>/);
});

test("the chip's mark is a dot in the state's tone", () => {
  const html = renderToStaticMarkup(h(TriggerMark, { tone: "danger" }));
  assert.match(html, /data-trigger-mark="danger"/);
  assert.match(html, /bg-\(--danger-soft\)/);
  assert.doesNotMatch(html, /style=/);
});

test("the model chip is marked, in red, only when its provider cannot answer or its quota is nearly spent", () => {
  const chip = (provider) => {
    globalThis.__queries = { [JSON.stringify(S.PICKER_QUERY_KEY)]: { models: MODELS, providers: PROVIDERS } };
    return renderToStaticMarkup(h(AgentBadge, { id: "agent", locked: false, provider, model: MODELS.find((m) => m.provider === provider).id }));
  };
  // 92 % used warns in the menu's ring and bars; the chip stays plain.
  const codex = chip("openai-codex");
  assert.doesNotMatch(codex, /data-trigger-mark/);
  assert.match(codex, /Talks with you and plans the work/);
  const nearlySpent = (() => {
    globalThis.__queries = { [JSON.stringify(S.PICKER_QUERY_KEY)]: { models: MODELS, providers: {
      ...PROVIDERS, "openai-codex": state("openai-codex", { attention: true, quota: { limit_key: "primary", label: "5h", left_fraction: 0.03, resets_at: null, tone: "danger", ring: true, warn: true } }),
    } } };
    return renderToStaticMarkup(h(AgentBadge, { id: "agent", locked: false, provider: "openai-codex", model: "gpt-6.1-sol" }));
  })();
  assert.match(nearlySpent, /data-trigger-mark="danger"/);
  assert.match(nearlySpent, /OpenAI Codex · 5h · 3% left/);
  // Signed out: the reply cannot go out, so the chip says so in red too.
  assert.match(chip("xai-grok"), /data-trigger-mark="danger"/);
  const kimi = chip("kimi");
  assert.match(kimi, /data-trigger-mark="danger"/);
  assert.match(kimi, /Kimi · No credit/);
  // The dot is read out to a screen reader, not only drawn.
  assert.match(kimi, /<span class="sr-only">No credit<\/span>/);
  const deepseek = chip("deepseek");
  assert.doesNotMatch(deepseek, /data-trigger-mark/);
  assert.match(deepseek, /Talks with you and plans the work/);
});

test("one way of a company reached several ways is named \"Company · Way\" as soon as the model list arrives; other providers keep their name", () => {
  const models = [model("openai-codex", "gpt-6.1-sol", "OpenAI Codex"), model("openai", "gpt-6.1", "OpenAI"), model("deepseek", "deepseek-v4", "DeepSeek")];
  const named = (id, name) => state(id, { name, name_zh: name });
  const providers = { "openai-codex": named("openai-codex", "OpenAI · ChatGPT"), openai: named("openai", "OpenAI · API"), deepseek: named("deepseek", null) };
  // No status read yet: the names come with the models.
  const html = menu({ models, providers, status: undefined });
  assert.match(html, />OpenAI · ChatGPT</);
  assert.match(html, />OpenAI · API</);
  assert.match(html, />DeepSeek</);
  assert.doesNotMatch(html, />OpenAI Codex</);
});

test("in Codex the menu lists Codex's models, then OpenProgram's providers run through it", () => {
  globalThis.__queries = {
    [JSON.stringify(S.PICKER_QUERY_KEY)]: { models: MODELS, providers: PROVIDERS },
    [JSON.stringify(S.PICKER_LIMITS_QUERY_KEY)]: STATUS,
  };
  const entry = {
    id: "codex", provider: "codex-harness", label: "Codex", installed: true, version: "0.153.4", signedIn: true,
    models: [{ id: "gpt-6-astra", label: "GPT-6 Astra", default: true, efforts: [] }, { id: "gpt-5.6-sol", label: "GPT-5.6 Sol", default: false, efforts: [] }],
    agents: [],
  };
  const html = renderToStaticMarkup(h(AgentSelector, {
    currentProvider: "codex-harness", currentModel: "openprogram:deepseek/deepseek-v4", onClose() {},
    harness: { id: "codex", provider: "codex-harness", entry },
  }));
  const order = ["Codex", "GPT-6 Astra", "GPT-5.6 Sol", "Through OpenProgram", "DeepSeek"].map((label) => html.indexOf(label));
  assert.ok(order.every((at, i) => at >= 0 && (i === 0 || at > order[i - 1])), html);
  // The provider whose model runs in Codex names it, as the current model.
  assert.match(rowOf(html, "deepseek"), /DEEPSEEK-V4/);
  assert.doesNotMatch(html, />Models</);
});
