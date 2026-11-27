/**
 * Fixtures and Radix stand-ins for the Model services page tests: status
 * payloads in each state the design names, the add-provider catalogue, a
 * provider's credential list, a fetch that answers by "METHOD /path", and
 * inline versions of the Nova parts linkedom cannot run (Switch, Tabs,
 * Dialog, RadioGroup, ToggleGroup, Collapsible, menus) and of dnd-kit.
 * No key here is real; every request is answered in-process.
 */

/** Stand-in sources for installLoader({ replace }). */
export const UI_STUBS = {
  "/components/ui/switch.tsx": "import { createElement as h } from 'react'; export const Switch = ({ checked, onCheckedChange, disabled, size, className, ...rest }) => h('button', { type: 'button', role: 'switch', 'aria-checked': String(Boolean(checked)), disabled, onClick: () => onCheckedChange?.(!checked), ...rest });",
  "/components/ui/checkbox.tsx": "import { createElement as h } from 'react'; export const Checkbox = ({ checked, onCheckedChange, ...rest }) => h('button', { type: 'button', role: 'checkbox', 'aria-checked': String(Boolean(checked)), onClick: () => onCheckedChange?.(!checked), ...rest });",
  "/components/ui/tooltip.tsx": "import { createElement as h } from 'react'; export const HoverTip = ({ label, children }) => h('span', { 'data-tip': label }, children); export const TipBody = () => null;",
  "/components/ui/tabs.tsx": [
    "import { createElement as h, createContext, useContext } from 'react';",
    "const C = createContext(null);",
    "export const Tabs = ({ value, onValueChange, children }) => h(C.Provider, { value: { value, onValueChange } }, h('div', null, children));",
    "export const TabsList = ({ children, ...rest }) => h('div', { role: 'tablist', 'aria-label': rest['aria-label'] }, children);",
    "export const TabsTrigger = ({ value, children, className, ...rest }) => { const c = useContext(C); return h('button', { type: 'button', role: 'tab', 'data-value': value, 'aria-selected': String(c.value === value), onClick: () => c.onValueChange(value), ...rest }, children); };",
    "export const TabsContent = () => null;",
  ].join("\n"),
  "/components/ui/radio-group.tsx": [
    "import { createElement as h, createContext, useContext } from 'react';",
    "const G = createContext(null);",
    "export const RadioGroup = ({ value, onValueChange, children, ...rest }) => h(G.Provider, { value: { value, onValueChange } }, h('div', { role: 'radiogroup', 'aria-label': rest['aria-label'] }, children));",
    "export const RadioGroupItem = ({ value, ...rest }) => { const g = useContext(G); return h('button', { type: 'button', role: 'radio', 'data-value': value, 'aria-checked': String(g.value === value), onClick: () => g.onValueChange(value), 'aria-label': rest['aria-label'] }); };",
  ].join("\n"),
  "/components/ui/dialog.tsx": [
    "import { createElement as h } from 'react';",
    "export const Dialog = ({ open, children }) => open ? h('div', { role: 'dialog' }, children) : null;",
    "export const DialogContent = ({ children, className, ...rest }) => h('div', rest, children);",
    "export const DialogTitle = ({ children }) => h('h2', null, children);",
    "export const DialogDescription = ({ children }) => h('p', null, children);",
    "export const DialogHeader = ({ children }) => h('div', null, children);",
    "export const DialogFooter = ({ children }) => h('div', null, children);",
  ].join("\n"),
  "/components/ui/dropdown-menu.tsx": [
    "import { createElement as h } from 'react';",
    "const pass = ({ children }) => h('div', null, children);",
    "export const DropdownMenu = pass;",
    "export const DropdownMenuTrigger = ({ children }) => children;",
    "export const DropdownMenuContent = ({ children }) => h('div', { role: 'menu' }, children);",
    "export const DropdownMenuItem = ({ children, onSelect, disabled, asChild, className, ...rest }) => asChild ? children : h('div', { role: 'menuitem', 'aria-disabled': disabled ? 'true' : undefined, onClick: () => { if (!disabled) onSelect?.(); }, ...rest }, children);",
    "export const DropdownMenuSeparator = () => h('hr');",
    "export const DropdownMenuSub = pass;",
    "export const DropdownMenuSubTrigger = ({ children, className, ...rest }) => h('div', { role: 'menuitem', 'aria-haspopup': 'menu', ...rest }, children);",
    "export const DropdownMenuSubContent = ({ children }) => h('div', { role: 'menu' }, children);",
  ].join("\n"),
  "/components/ui/menu/menu-parts.tsx": "import { createElement as h } from 'react'; export const MenuRow = ({ label, trail, checked }) => h('span', { 'data-checked': checked === undefined ? undefined : String(checked) }, label, trail != null ? h('small', { 'data-trail': '' }, trail) : null); export const MenuIcon = () => null;",
  "/components/ui/toggle-group.tsx": [
    "import { createElement as h, createContext, useContext } from 'react';",
    "const G = createContext(null);",
    "export const ToggleGroup = ({ value, onValueChange, children, ...rest }) => h(G.Provider, { value: { value, onValueChange } }, h('div', { role: 'group', 'aria-label': rest['aria-label'] }, children));",
    "export const ToggleGroupItem = ({ value, children }) => { const g = useContext(G); return h('button', { type: 'button', 'data-value': value, 'aria-pressed': String(g.value === value), onClick: () => g.onValueChange(value) }, children); };",
  ].join("\n"),
  "/components/ui/collapsible.tsx": [
    "import { createElement as h, createContext, useContext, useState, cloneElement } from 'react';",
    "const C = createContext(null);",
    "export const Collapsible = ({ children }) => { const [open, setOpen] = useState(false); return h(C.Provider, { value: { open, setOpen } }, h('div', null, children)); };",
    "export const CollapsibleTrigger = ({ children }) => { const c = useContext(C); return cloneElement(children, { 'aria-expanded': String(c.open), onClick: () => c.setOpen(!c.open) }); };",
    "export const CollapsibleContent = ({ children }) => { const c = useContext(C); return c.open ? h('div', null, children) : null; };",
  ].join("\n"),
  "/@dnd-kit/core/": [
    "const node = { setNodeRef() {}, listeners: {}, attributes: {}, transform: null, isDragging: false, isOver: false };",
    "export const DndContext = ({ children }) => children;",
    "export const useDraggable = () => node; export const useDroppable = () => node;",
    "export const useSensor = () => null; export const useSensors = () => [];",
    "export const PointerSensor = {}; export const KeyboardSensor = {};",
    "export const KeyboardCode = { Space: 'Space', Down: 'ArrowDown', Right: 'ArrowRight', Left: 'ArrowLeft', Up: 'ArrowUp', Esc: 'Escape', Enter: 'Enter', Tab: 'Tab' };",
  ].join("\n"),
  "/components/ui/confirm-dialog.tsx": "export const confirmAction = async (request) => { (globalThis.confirmRequests ??= []).push(request); return globalThis.confirmAnswer ?? true; };",
  "/components/ui/spinner.tsx": "export const Spinner = () => null;",
  "/components/settings/providers/icons/provider-icon.tsx": "export const ProviderIcon = () => null; export const providerLogo = () => null;",
  "/lib/format-utils/toast.ts": "export const showToast = (message) => (globalThis.toasts ??= []).push(message);",
};

const json = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

/** A fetch answering `"GET /api/x"` keys (query string ignored unless the
 *  key has one); a function value gets (body, url). Records every call. */
export function fakeFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push([method, String(url), body]);
    const path = String(url);
    const key = [`${method} ${path}`, `${method} ${path.split("?")[0]}`].find((k) => k in routes);
    if (!key) return json(404, { error: `no route ${method} ${path}` });
    const answer = typeof routes[key] === "function" ? await routes[key](body, path) : routes[key];
    if (answer instanceof Error) throw answer;
    return answer && typeof answer.status === "number" && "body" in answer ? json(answer.status, answer.body) : json(200, answer);
  };
  return calls;
}

export const NOW = Date.UTC(2026, 10, 14, 10, 0, 0) / 1000;

const days = (values) => values.map((cost, i) => ({ day: `d${i}`, cost_usd: cost, tokens: cost * 1e5, calls: cost ? 3 : 0 }));

export function spend(overrides = {}) {
  return {
    days: 30, calls: 120, cost_usd: 6.3, list_price_usd: 6.3, included: false, unpriced_calls: 0,
    input_tokens: 2e6, output_tokens: 4e5, cache_read_tokens: 1e6, cache_write_tokens: 0,
    total_tokens: 3.4e6, cache_share: 0.29, series: days([0.2, 0.3, 0.1, 0.4, 0.5, 0.2, 0.3]),
    ...overrides,
  };
}

export function limit(overrides = {}) {
  return {
    limit_key: "5h", label: "5h", unit: "percent", used: null, limit: null, remaining: null,
    used_fraction: 0.38, left_fraction: 0.62, window_minutes: 300, resets_at: NOW + 4 * 3600,
    status: "ok", stale: false, ...overrides,
  };
}

const KEY_STATE = { cooling: "rate_limited", quota: "no_credit", needs_login: "signed_out", invalid_key: "invalid_key" };

/** A credential as the status route lists it, with its own health, quota,
 *  plan and spend (a partial one is filled from its pool status). */
export function cred(id, overrides = {}) {
  const { quota, health, ...rest } = overrides;
  const state = KEY_STATE[rest.status] ?? "ready";
  return {
    account: id, credential_id: id, kind: "api_key", label: "", email: "", key_tail: "", env_var: null, signin_name: "",
    status: "ready", enabled: true, active: false, cooldown_until: null, last_error: null, last_used_at: null, use_count: 0,
    status_at: null, plan: null, spend_usd: null, ...rest,
    health: { state, until: null, detail: null, ...(health ?? {}) },
    quota: { kind: "none", account: id, fetched_at: NOW - 120, stale: false, limits: [], read_error: null, read_at: NOW - 120, ...(quota ?? {}) },
  };
}

/** `p` with one credential in use that holds its quota and health. */
function withKey(p, overrides = {}) {
  return { ...p, keys: [cred(`${p.id}-key`, { active: true, quota: p.quota, health: p.health, kind: p.billing === "subscription" ? "oauth" : "api_key", ...overrides })] };
}

export function status(id, overrides = {}) {
  const { quota, health, spend: s, keys, ...rest } = overrides;
  return {
    keys: (keys ?? []).map((k) => ("health" in k ? k : cred(k.credential_id ?? k.account ?? "k", k))),
    id, label: id[0].toUpperCase() + id.slice(1), billing: "api_key", enabled: true,
    health: { state: "ready", until: null, detail: null, ...(health ?? {}) },
    quota: { kind: "none", account: null, fetched_at: NOW - 120, stale: false, limits: [], read_error: null, read_at: NOW - 120, ...(quota ?? {}) },
    spend: spend(s), default_model: null, fix: null, console_url: null,
    plan: null, checked_at: null, takes_keys: (rest.billing ?? "api_key") === "api_key",
    family: id, family_label: rest.label ?? id[0].toUpperCase() + id.slice(1), way_label: null,
    ...rest,
  };
}

/** One provider in each state the cards and rows draw. */
export const STATES = {
  windowed: withKey(status("opencode-go", {
    label: "OpenCode Go", billing: "subscription", default_model: "gpt-5.5",
    spend: { included: true, cost_usd: null, list_price_usd: 41.2 },
    quota: { kind: "windowed", limits: [limit(), limit({ limit_key: "week", label: "Week", used_fraction: 0.85, left_fraction: 0.15, window_minutes: 10080, resets_at: NOW + 3 * 86400 })] },
  })),
  balance: withKey(status("deepseek", {
    label: "DeepSeek",
    spend: { series: days(Array.from({ length: 30 }, () => 0.1)) },
    quota: { kind: "balance", limits: [limit({ limit_key: "balance", label: "Balance", unit: "usd", remaining: 18.42, used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null })] },
  })),
  rate: withKey(status("groq", {
    label: "Groq",
    quota: { kind: "rate_limit", limits: [limit({ limit_key: "rpm", label: "rpm", unit: "requests", limit: 50, remaining: 46, used_fraction: 0.08, left_fraction: 0.92, window_minutes: 1, resets_at: null })] },
  })),
  limited: withKey(status("openrouter", {
    label: "OpenRouter", health: { state: "rate_limited", until: NOW + 1800 },
    quota: { kind: "balance", limits: [limit({ limit_key: "key_limit", label: "Key limit", unit: "usd", limit: 20, remaining: 0.6, used_fraction: 0.97, left_fraction: 0.03, window_minutes: null, resets_at: null })] },
  })),
  signedOut: withKey(status("google", {
    label: "Google", billing: "subscription", health: { state: "signed_out", detail: "token expired" },
    fix: { action: "sign_in", url: null },
    quota: { kind: "windowed", stale: true, fetched_at: NOW - 3 * 3600, limits: [limit()] },
  })),
  noCredit: withKey(status("moonshotai", {
    label: "Moonshot", health: { state: "no_credit" }, fix: { action: "top_up", url: "https://platform.example.test/billing" },
    quota: { kind: "balance", limits: [limit({ limit_key: "balance", label: "Balance", unit: "usd", remaining: 0, used_fraction: null, left_fraction: null, window_minutes: null, resets_at: null })] },
  })),
  unread: withKey(status("xai", { label: "xAI", quota: { kind: "none", read_error: "HTTP 500 from the usage endpoint" } })),
  local: status("ollama", { label: "Ollama", billing: "local", health: { state: "not_running" }, fix: { action: "start_server", url: null }, spend: { cost_usd: 0, list_price_usd: 0, total_tokens: 0, cache_share: null, series: [] } }),
};

/** `p` as one way of a company (the company table's names). */
export function way(p, family, familyLabel, title, short) {
  return { ...p, family, family_label: familyLabel, way_label: { title, title_zh: title, short, short_zh: short } };
}

export function payload(providers = Object.values(STATES)) {
  return { generated_at: NOW - 60, stale_after_s: 900, providers };
}

export const CATALOGUE = [
  { id: "openai", label: "OpenAI", enabled: false, kind: "api", api_key_env: "OPENAI_API_KEY", base_url: "https://api.openai.com/v1" },
  { id: "opencode-go", label: "OpenCode Go", enabled: true, kind: "api", login_methods: ["browser"] },
  { id: "anthropic", label: "Anthropic", enabled: false, kind: "api", api_key_env: "ANTHROPIC_API_KEY", base_url: "https://api.anthropic.com" },
  { id: "deepseek", label: "DeepSeek", enabled: false, kind: "api", api_key_env: "DEEPSEEK_API_KEY", base_url: "https://api.deepseek.com" },
  { id: "groq", label: "Groq", enabled: false, kind: "api", api_key_env: "GROQ_API_KEY" },
  { id: "ollama", label: "Ollama", enabled: false, kind: "api", local: true, base_url: "http://127.0.0.1:11434/v1" },
];

export const DETECTED = {
  env: [
    { env_var: "DEEPSEEK_API_KEY", provider: "deepseek", label: "DeepSeek", providers: ["deepseek"], masked: "sk-…9f2c", connected: false },
    { env_var: "OPENAI_API_KEY", provider: "openai", label: "OpenAI", providers: ["openai"], masked: "sk-…aa11", connected: true },
  ],
  local: [{ id: "ollama", label: "Ollama", base_url: "http://127.0.0.1:11434", running: true, enabled: false }],
  popular: [
    { id: "opencode-go", label: "OpenCode", auth: "sign_in", console_url: null, connected: false, env_var: null, variants: [{ id: "opencode", label: "OpenCode Zen", auth: "api_key", console_url: "https://platform.example.test/keys", connected: false, env_var: "OPENCODE_API_KEY" }] },
    { id: "anthropic", label: "Anthropic", auth: "api_key", console_url: "https://console.example.test/keys", connected: false, env_var: "ANTHROPIC_API_KEY", variants: [] },
    { id: "deepseek", label: "DeepSeek", auth: "api_key", console_url: "https://platform.deepseek.example.test/api_keys", connected: false, env_var: "DEEPSEEK_API_KEY", variants: [] },
  ],
  catalogue_count: 6,
};

export function check(result, overrides = {}) {
  return {
    result, http_status: null, detail: null, fix_page: null, fix_url: null, balance: null, model_count: null,
    models: [], enabled_models: [], provider_enabled: false, auto_enabled: false, ...overrides,
  };
}

export const SUGGESTED = [
  { id: "deepseek-chat", name: "DeepSeek Chat", context_window: 128000, input_cost: 0.27, output_cost: 1.1, reasoning: false, vision: false, tools: true, enabled: false },
  { id: "deepseek-reasoner", name: "DeepSeek Reasoner", context_window: 128000, input_cost: 0.55, output_cost: 2.19, reasoning: true, vision: false, tools: true, enabled: false },
];

/** Settle React and pending promises. */
export const settle = async (view, rounds = 3) => {
  for (let i = 0; i < rounds; i++) await view.act(() => new Promise((resolve) => setTimeout(resolve, 0)));
};

export const button = (root, label) =>
  [...root.querySelectorAll("button, [role=menuitem], a")].find((b) => b.textContent.trim() === label || b.getAttribute("aria-label") === label);

/** Type into an input the way React notices it. */
export async function typeInto(view, input, value) {
  input.type ||= "text";
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, value);
  await view.act(() => input.dispatchEvent(new Event("input", { bubbles: true })));
}

/** A credential row as GET /api/providers/{p}/credentials lists it. */
export function credential(id, overrides = {}) {
  return {
    id, label: "", display_label: id, kind: "api_key", source: "web", identity: {}, key_tail: "a1b2",
    env_var: null, read_only: false, enabled: true, status: "ready", cooldown_until: null,
    blocked_models: {}, last_error: null, last_used_at: null, use_count: 0, created_at: NOW - 86400,
    today: { calls: 0, cost_usd: 0 }, limits: [], ...overrides,
  };
}

/** The login-method registry's "Paste an API key" entry, as the worker sends it. */
export const KEY_ENTRY = {
  id: "api_key", label: "Paste an API key", label_zh: "粘贴 API 密钥",
  description: "A key from the provider's console.", description_zh: "使用在服务商控制台创建的密钥。",
};

/** A provider's credential list; its ways in default to pasting a key, as a
 *  key provider's do. */
export function credentialList(provider, credentials, overrides = {}) {
  const { auth, ...rest } = overrides;
  return {
    provider, strategy: "in_order", strategies: ["in_order", "evenly", "least_used"],
    active: credentials.find((c) => c.enabled && c.status === "ready")?.id ?? null,
    auth: { methods: [KEY_ENTRY], env_vars: [], check: "models", keys_url: null, billing_url: null, ...(auth ?? {}) },
    credentials, ...rest,
  };
}
