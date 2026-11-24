import { agentSettingsMoved, requestAgentSettings } from "@/lib/agents/agent-settings-request";
import { adoptBranchIdentity, adoptStoredControls } from "@/lib/agents/branch-settings";
/**
 * Provider / agent-settings / token-badge data layer.
 *
 * `useWS` / `conversations.ts` / `chat-handlers.ts` / the React topbar
 * all import the exports directly — nothing here lives on `window`.
 *
 * Several functions write to legacy topbar DOM ids (`#providerBadge`,
 * `#tokenBadge`, `#chatAgentBadge`, …). Where the React topbar already
 * owns that chip the element is absent and the write is a no-op — the
 * data side (`_agentSettings`, cache timers) is what still matters.
 *
 * Imported for side effects by `useWS`.
 */

import { useSessionStore, type AgentBadgeInfo } from "@/lib/session-store";
import { refreshBranchTokens } from "./session/conversations";
import { runtimeState, THINKING_CONFIG_CHANGED, type ThinkingConfig } from "./state";

interface AgentSide {
  provider?: string;
  model?: string;
  session_id?: string;
  locked?: boolean;
  thinking?: ThinkingConfig;
}

/** Narrower view of `runtimeState._agentSettings` — the shared type keeps
 *  the sides as loose records; this file needs `.provider` / `.model`. */
interface AgentSettings {
  chat?: AgentSide;
  exec?: AgentSide;
  available?: Record<string, unknown>;
}

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
  runtimeState._hasActiveSession = !!info.session_id;
  provBadge.textContent =
    info.provider +
    (info.type ? " · " + info.type : "") +
    (runtimeState._hasActiveSession ? " \u{1F512}" : "");
  provBadge.style.display = "";
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

/** Guards the one-shot retry below; was `(window as any)._chatRetried`. */
let chatRetried = false;
/** Each conversation's newest settings request; callers that share it
 *  (``requestAgentSettings``) are equally current. */
const settingsReads = new Map<string, Promise<unknown>>();

/** ``written``: this page just wrote the settings, so a request that left
 *  before the write must not answer this read. */
export interface SettingsReadOptions {
  written?: boolean;
}

/** The branch a settings reply describes: the named one it answered for,
 *  else (an older server) the selected one. */
function settingsBranch(data: { branch_id?: unknown; active_branch_id?: unknown }): unknown {
  return typeof data.branch_id === "string" ? data.branch_id : data.active_branch_id;
}
function beginSettingsRead(sid: string) {
  const branchId = useSessionStore.getState().composerSettingsBySession[sid]?.active_branch_id ?? null;
  // The read names the pane's branch, so a pane on a branch that is not
  // selected reads its own model back (after a broadcast wiped it, too).
  const query = "?session_id=" + encodeURIComponent(sid) + (branchId ? "&branch_id=" + encodeURIComponent(branchId) : "");
  const request = requestAgentSettings(query) as Promise<AgentSettingsReply>;
  settingsReads.set(sid, request);
  const accept = (data: { branch_id?: unknown; active_branch_id?: unknown }) => settingsReads.get(sid) === request
    && (useSessionStore.getState().composerSettingsBySession[sid]?.active_branch_id ?? null) === branchId
    && (settingsBranch(data) === undefined || branchId === null || settingsBranch(data) === branchId);
  return { request, accept };
}

type AgentSettingsPayload = { chat?: AgentBadgeInfo; exec?: AgentBadgeInfo };

/** GET /api/agent_settings: the conversation's model slots, the branch the
 *  answer is for, and its stored composer controls. */
type AgentSettingsReply = {
  chat?: AgentBadgeInfo & Record<string, unknown>;
  exec?: AgentBadgeInfo & Record<string, unknown>;
  branch_id?: unknown;
  active_branch_id?: unknown;
  settings?: unknown;
};

/** Keep one conversation's own model settings, for the composer of the pane
 *  that shows it; a slot without a provider and model hides its chip. */
function rememberSessionAgentSettings(key: string, settings: AgentSettingsPayload): void {
  const valid = (slot?: AgentBadgeInfo) => (slot?.provider && slot.model ? { ...slot } : undefined);
  useSessionStore.getState().setSessionAgentSettings(key, {
    chat: valid(settings.chat),
    exec: valid(settings.exec),
  });
}

/** Model settings of one conversation that is not (necessarily) focused —
 *  a split pane's. Only that conversation's entry changes. */
export async function loadSessionAgentSettings(sessionId: string, options: SettingsReadOptions = {}): Promise<void> {
  try {
    if (options.written) agentSettingsMoved();
    const { request, accept } = beginSettingsRead(sessionId);
    const data = await request;
    if (accept(data)) {
      adoptBranchIdentity(sessionId, useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null, settingsBranch(data));
      adoptStoredControls(sessionId, data.settings);
      rememberSessionAgentSettings(sessionId, data);
    }
  } catch {
    /* the pane keeps what it had */
  }
}

/** ``agent_settings_changed``: a model pick on some conversation's selected
 *  branch, or models enabled or disabled in Settings. The frame does not say
 *  which conversation it is about, so no chip takes its model: every chip
 *  keeps what it shows until its own read lands — the focused conversation's
 *  here, each other shown pane's when the epoch moves. */
export function agentSettingsChanged(): Promise<void> {
  useSessionStore.getState().markSessionAgentSettingsStale();
  return loadAgentSettings({ written: true });
}

export async function loadAgentSettings(options: SettingsReadOptions = {}): Promise<void> {
  try {
    if (options.written) agentSettingsMoved();
    const st = useSessionStore.getState();
    // Unsent Agent tabs must not create a default-Agent session through
    // the legacy settings endpoint before their explicit first message.
    const pendingAgent = st.activeChatKey && st.composerSettingsBySession[st.activeChatKey]?.agentInvocation;
    const sid = pendingAgent ? null : (
      st.currentSessionId
      ?? runtimeState.currentSessionId
      ?? (st.activeChatKey && st.activeChatKey !== "__new__" ? st.activeChatKey : null));
    const read = sid ? beginSettingsRead(sid) : null;
    const newSettings = await (read ? read.request : requestAgentSettings() as Promise<AgentSettingsReply>);
    if (read && !read.accept(newSettings)) return;
    if (sid) adoptBranchIdentity(sid, useSessionStore.getState().composerSettingsBySession[sid]?.active_branch_id ?? null, settingsBranch(newSettings));
    if (sid) adoptStoredControls(sid, newSettings.settings);
    if (sid) rememberSessionAgentSettings(sid, newSettings);
    const focused = useSessionStore.getState().currentSessionId ?? runtimeState.currentSessionId;
    if (sid && focused && sid !== focused) return;
    if (newSettings.chat) runtimeState._agentSettings.chat = newSettings.chat;
    if (newSettings.exec) runtimeState._agentSettings.exec = newSettings.exec;
  } catch {
    return;
  }
  // Push directly to store as primary data source
  updateAgentBadges();

  const as = runtimeState._agentSettings as AgentSettings;
  const newChatProv = (as.chat && as.chat.provider) || null;
  const newChatModel = (as.chat && as.chat.model) || null;
  if (
    (runtimeState._lastChatProvider != null &&
      newChatProv !== runtimeState._lastChatProvider) ||
    (runtimeState._lastChatModel != null &&
      newChatModel !== runtimeState._lastChatModel)
  ) {
    runtimeState._thinkingEffort = null;
  }
  runtimeState._lastChatProvider = newChatProv;
  runtimeState._lastChatModel = newChatModel;

  const newExecProv = (as.exec && as.exec.provider) || null;
  const newExecModel = (as.exec && as.exec.model) || null;
  if (
    (runtimeState._lastExecProvider != null &&
      newExecProv !== runtimeState._lastExecProvider) ||
    (runtimeState._lastExecModel != null &&
      newExecModel !== runtimeState._lastExecModel)
  ) {
    runtimeState._execThinkingEffort = null;
  }
  runtimeState._lastExecProvider = newExecProv;
  runtimeState._lastExecModel = newExecModel;

  if (as.chat && as.chat.thinking) {
    runtimeState._thinkingConfig = as.chat.thinking;
    // The composer's effort control redraws from it (use-thinking-effort).
    if (typeof window !== "undefined") window.dispatchEvent(new Event(THINKING_CONFIG_CHANGED));
    // Lazy: a static `./ui` import would close the cycle
    // ui → functions-panel → chat-handlers → providers → ui.
    void import("./ui").then((m) => m.buildThinkingMenu());
  }

  // Retry once if chat provider is null but exec is set — the backend
  // may still be initializing providers on first request after restart.
  if (!newChatProv && newExecProv && !chatRetried) {
    chatRetried = true;
    setTimeout(() => {
      chatRetried = false;
      loadAgentSettings();
    }, 2000);
  }
}

export function updateAgentBadges(): void {
  const as = runtimeState._agentSettings as AgentSettings;
  if (!as) return;
  try {
    const chatValid = as.chat?.provider && as.chat?.model;
    const execValid = as.exec?.provider && as.exec?.model;
    // null (not undefined) so a now-disabled model CLEARS its chip —
    // the store treats undefined as "keep previous".
    useSessionStore.getState().setAgentSettings({
      chat: chatValid ? { ...as.chat } : null,
      exec: execValid ? { ...as.exec } : null,
    });
  } catch {
    /* ignore — store not ready yet */
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
    const curSid = useSessionStore.getState().currentSessionId ?? runtimeState.currentSessionId;
    if (curSid === sessionId) refreshTokenBadge();
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
    if (pct > 85) color = "var(--accent-red)";
    else if (pct > 65) color = "var(--accent-yellow)";
  }
  const lastRate = Math.round((data.last_turn_hit_rate || 0) * 100);
  const lastCR = data.last_assistant_cache_read || 0;
  const label = win ? fmtTokens(cur) + "/" + fmtTokens(win) : fmtTokens(cur);
  let cacheHtml = "";
  if (lastCR > 0 || (data.cache_read_total || 0) > 0 || cacheWriteTs[sessionId]) {
    const alive = cacheAlive(sessionId);
    const dotColor = alive ? "var(--accent-blue)" : "var(--text-muted)";
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
  const sid = useSessionStore.getState().currentSessionId ?? runtimeState.currentSessionId;
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
    renderTokenBadge(await resp.json() as TokenData, sid);
  } catch {
    badge.style.display = "none";
  }
  try {
    void refreshBranchTokens();
  } catch {
    /* ignore */
  }
}

/* No `window.*` bridges: this module generates no inline `onclick=`
   markup, and every consumer imports the exports directly. */
