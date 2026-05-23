/**
 * Provider / agent-settings / token-badge data layer.
 *
 * TS port of the legacy `public/js/shared/providers.js`. Functions are
 * bridged onto `window.*` for the still-legacy scripts (ui.js) and
 * React components; `useWS` / `conversations.ts` / `chat-handlers.ts`
 * call the exports directly.
 *
 * Several functions write to legacy topbar DOM ids (`#providerBadge`,
 * `#tokenBadge`, `#chatAgentBadge`, …). Where the React topbar already
 * owns that chip the element is absent and the write is a no-op — the
 * data side (`_agentSettings`, cache timers) is what still matters.
 *
 * Imported for side effects by `useWS`.
 */

interface AgentSide {
  provider?: string;
  model?: string;
  session_id?: string;
  locked?: boolean;
  thinking?: unknown;
}

interface AgentSettings {
  chat?: AgentSide;
  exec?: AgentSide;
  available?: Record<string, unknown>;
}

interface ProvWindow {
  currentSessionId?: string | null;
  _hasActiveSession?: boolean;
  _agentSettings?: AgentSettings;
  _lastChatProvider?: string | null;
  _lastChatModel?: string | null;
  _lastExecProvider?: string | null;
  _lastExecModel?: string | null;
  _thinkingEffort?: string | null;
  _execThinkingEffort?: string | null;
  _thinkingConfig?: unknown;
  buildThinkingMenu?: () => void;
  escAttr?: (s: unknown) => string;
  escHtml?: (s: unknown) => string;
  _refreshBranchTokens?: () => void;
  [k: string]: unknown;
}

const W = window as unknown as ProvWindow;

/* ===== Provider badge ============================================ */

interface ProviderInfo {
  provider?: string;
  type?: string;
  session_id?: string;
}

export function updateProviderBadge(info: ProviderInfo | null | undefined): void {
  const provBadge = document.getElementById("providerBadge");
  const sessBadge = document.getElementById("sessionBadge");
  if (!provBadge) return;
  if (!info || !info.provider) {
    provBadge.style.display = "none";
    if (sessBadge) sessBadge.style.display = "none";
    return;
  }
  const hadSession = W._hasActiveSession;
  W._hasActiveSession = !!info.session_id;
  provBadge.textContent =
    info.provider +
    (info.type ? " · " + info.type : "") +
    (W._hasActiveSession ? " \u{1F512}" : "");
  provBadge.style.display = "";
  if (hadSession !== W._hasActiveSession) loadProviders();
  if (sessBadge) {
    if (info.session_id) {
      const short = info.session_id.split("-").pop() || info.session_id.slice(-8);
      sessBadge.textContent = "session:" + short;
      sessBadge.title = info.session_id;
      sessBadge.style.display = "";
    } else {
      sessBadge.textContent = "no session";
      sessBadge.style.display = "";
    }
  }
}

/* ===== Agent settings ============================================ */

export async function loadAgentSettings(): Promise<void> {
  try {
    let url = "/api/agent_settings";
    if (W.currentSessionId) {
      url += "?session_id=" + encodeURIComponent(W.currentSessionId);
    }
    const resp = await fetch(url);
    W._agentSettings = await resp.json();
  } catch {
    return;
  }
  updateAgentBadges();

  const as = W._agentSettings || {};
  const newChatProv = (as.chat && as.chat.provider) || null;
  const newChatModel = (as.chat && as.chat.model) || null;
  if (
    (W._lastChatProvider != null && newChatProv !== W._lastChatProvider) ||
    (W._lastChatModel != null && newChatModel !== W._lastChatModel)
  ) {
    W._thinkingEffort = null;
  }
  W._lastChatProvider = newChatProv;
  W._lastChatModel = newChatModel;

  const newExecProv = (as.exec && as.exec.provider) || null;
  const newExecModel = (as.exec && as.exec.model) || null;
  if (
    (W._lastExecProvider != null && newExecProv !== W._lastExecProvider) ||
    (W._lastExecModel != null && newExecModel !== W._lastExecModel)
  ) {
    W._execThinkingEffort = null;
  }
  W._lastExecProvider = newExecProv;
  W._lastExecModel = newExecModel;

  if (as.chat && as.chat.thinking) {
    W._thinkingConfig = as.chat.thinking;
    W.buildThinkingMenu?.();
  }
}

function escAgentBadge(s: unknown): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function updateAgentBadges(): void {
  const as = W._agentSettings || {};
  const chatBadge = document.getElementById("chatAgentBadge");
  const execBadge = document.getElementById("execAgentBadge");
  if (chatBadge && as.chat) {
    const cp = as.chat.provider || "?";
    const cm = as.chat.model || "";
    const detailsParts = [cp];
    if (cm) detailsParts.push(cm);
    const sid = as.chat.session_id;
    if (sid) detailsParts.push(sid.slice(0, 8));
    const details = ": " + detailsParts.join(" · ");
    chatBadge.innerHTML =
      '<span class="badge-short">Chat</span>' +
      '<span class="badge-details">' +
      escAgentBadge(details) +
      "</span>";
    chatBadge.title = "Chat agent" + details;
    if (as.chat.locked) chatBadge.classList.add("locked");
    else chatBadge.classList.remove("locked");
  }
  if (execBadge && as.exec) {
    const ep = as.exec.provider || "?";
    const em = as.exec.model || "";
    const execDetailsParts = [ep];
    if (em) execDetailsParts.push(em);
    const execDetails = ": " + execDetailsParts.join(" · ");
    execBadge.innerHTML =
      '<span class="badge-short">Exec</span>' +
      '<span class="badge-details">' +
      escAgentBadge(execDetails) +
      "</span>";
    execBadge.title = "Execution agent" + execDetails;
  }
  try {
    refreshTokenBadge();
  } catch {
    /* ignore */
  }
}

/* ===== Token badge =============================================== */

function fmtTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n || 0);
}

const cacheWriteTs: Record<string, number> = {};
const cacheTtlTimer: Record<string, ReturnType<typeof setTimeout>> = {};
const CACHE_TTL_MS = 5 * 60 * 1000;

export function recordCacheWrite(sessionId: string): void {
  cacheWriteTs[sessionId] = Date.now();
  if (cacheTtlTimer[sessionId]) clearTimeout(cacheTtlTimer[sessionId]);
  cacheTtlTimer[sessionId] = setTimeout(() => {
    delete cacheTtlTimer[sessionId];
    if (W.currentSessionId === sessionId) refreshTokenBadge();
  }, CACHE_TTL_MS);
}

function cacheAlive(sessionId: string): boolean {
  const ts = cacheWriteTs[sessionId];
  if (!ts) return false;
  return Date.now() - ts < CACHE_TTL_MS;
}

interface TokenData {
  current_tokens?: number;
  naive_sum?: number;
  context_window?: number;
  last_assistant_usage?: number;
  last_assistant_cache_read?: number;
  last_turn_hit_rate?: number;
  cache_hit_rate?: number;
  cache_read_total?: number;
  model?: string | null;
  source_mix?: Record<string, unknown> | null;
}

export function renderTokenBadge(data: TokenData, sessionId: string): void {
  const badge = document.getElementById("tokenBadge");
  if (!badge) return;
  const cur = data.current_tokens || data.naive_sum || 0;
  if (!cur && !data.last_assistant_usage) {
    badge.style.display = "none";
    return;
  }
  const win = data.context_window || 0;
  const pct = win ? Math.round((cur / win) * 100) : null;
  let color = "var(--text-muted)";
  if (pct !== null) {
    if (pct > 85) color = "var(--accent-red, #e5534b)";
    else if (pct > 65) color = "var(--accent-yellow, #d2a106)";
  }
  const lastRate = Math.round((data.last_turn_hit_rate || 0) * 100);
  const lastCR = data.last_assistant_cache_read || 0;
  const label = win ? fmtTokens(cur) + "/" + fmtTokens(win) : fmtTokens(cur);
  let cacheHtml = "";
  if (lastCR > 0 || (data.cache_read_total || 0) > 0 || cacheWriteTs[sessionId]) {
    const alive = cacheAlive(sessionId);
    const dotColor = alive ? "var(--accent-blue, #4f8ef7)" : "var(--text-muted)";
    const cacheStatus = alive ? "Cache active" : "Cache expired";
    cacheHtml =
      ' · <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' +
      dotColor +
      ';vertical-align:middle;margin-bottom:1px" title="' +
      cacheStatus +
      '"></span> ' +
      lastRate +
      "%";
  }
  badge.innerHTML = label + cacheHtml;
  badge.style.color = color;
  badge.style.display = "";
  let tip = win
    ? "Context: " + cur.toLocaleString() + " / " + win.toLocaleString() + " (" + pct + "%)"
    : "Context: " + cur.toLocaleString() + " tokens";
  if (lastCR > 0 || (data.cache_read_total || 0) > 0) {
    tip += "\nCache: " + lastCR.toLocaleString() + " cached (" + lastRate + "% hit)";
    const ts = cacheWriteTs[sessionId];
    const remaining = ts
      ? Math.max(0, Math.round((CACHE_TTL_MS - (Date.now() - ts)) / 1000))
      : 0;
    if (cacheAlive(sessionId) && remaining > 0) tip += "\nExpires in " + remaining + "s";
    else if (cacheWriteTs[sessionId]) tip += "\nCache expired";
  }
  if (data.model) tip += "\nModel: " + data.model;
  if (data.source_mix) {
    const mix = Object.keys(data.source_mix)
      .map((k) => k + ": " + data.source_mix![k])
      .join(", ");
    if (mix) tip += "\nSources: " + mix;
  }
  badge.title = tip;
}

export async function refreshTokenBadge(): Promise<void> {
  const badge = document.getElementById("tokenBadge");
  const sid = W.currentSessionId;
  if (!badge) return;
  if (!sid) {
    badge.style.display = "none";
    return;
  }
  try {
    const resp = await fetch("/api/sessions/" + encodeURIComponent(sid) + "/tokens");
    if (!resp.ok) {
      badge.style.display = "none";
      return;
    }
    renderTokenBadge(await resp.json(), sid);
  } catch {
    badge.style.display = "none";
  }
  try {
    W._refreshBranchTokens?.();
  } catch {
    /* ignore */
  }
}

/* ===== Provider list ============================================= */

interface Provider {
  name: string;
  label?: string;
  configurable?: boolean;
  configured?: boolean;
  available?: boolean;
}

export async function loadProviders(): Promise<void> {
  try {
    const resp = await fetch("/api/providers");
    renderProviders(await resp.json());
  } catch {
    /* ignore */
  }
}

function renderProviders(providers: Provider[]): void {
  const el = document.getElementById("providerList");
  if (!el) return;
  const escAttr = W.escAttr || ((s: unknown) => String(s));
  const escHtml = W.escHtml || ((s: unknown) => String(s));
  el.innerHTML = providers
    .map((p) => {
      const isConfigured = p.configurable ? p.configured : p.available;
      const cls = isConfigured ? "provider-item configured" : "provider-item unavailable";
      const typeTag = p.configurable ? "API" : "CLI";
      const badgeCls = isConfigured ? "config-badge configured" : "config-badge";
      const badgeText = isConfigured ? "Configured" : "Set up";
      const configBadge =
        '<a class="' +
        badgeCls +
        '" href="/config" target="_blank" onclick="event.stopPropagation()" title="Configure">' +
        badgeText +
        "</a>";
      return (
        '<div class="' +
        cls +
        '" title="' +
        escAttr(p.label) +
        '">' +
        '<span class="provider-dot"></span>' +
        '<span class="provider-type-tag">' +
        typeTag +
        "</span>" +
        '<span class="provider-name">' +
        escHtml(p.name) +
        "</span>" +
        configBadge +
        "</div>"
      );
    })
    .join("");
}

export async function switchProvider(name: string): Promise<void> {
  try {
    const resp = await fetch("/api/provider/" + encodeURIComponent(name), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: W.currentSessionId }),
    });
    const data = await resp.json();
    if (data.switched) {
      loadProviders();
    } else if (data.error) {
      alert("Switch failed: " + data.error);
    }
  } catch (e) {
    alert("Switch failed: " + (e as Error).message);
  }
}

/* ===== window bridges ============================================ */

W.updateProviderBadge = updateProviderBadge;
W.loadAgentSettings = loadAgentSettings;
W.updateAgentBadges = updateAgentBadges;
W._recordCacheWrite = recordCacheWrite;
W._renderTokenBadge = renderTokenBadge;
W.refreshTokenBadge = refreshTokenBadge;
W.loadProviders = loadProviders;
W.switchProvider = switchProvider;
