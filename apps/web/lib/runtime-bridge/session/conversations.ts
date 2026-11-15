import { adoptCheckedOutBranch, pendingBranchSettings } from "@/lib/agents/branch-settings";
import { branchSettingsPatch } from "@/lib/session-store/branch-settings";
import { translateText } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { sendRuntimeCommand } from "./session-load";
import { registerSessionHistory } from "@/lib/chat/history/session-history";
import { seedTurnWindow } from "./session-history-loader";
import type { TurnWindow, WireTurn } from "@/lib/chat/history/history-window";
export { loadOlderSessionHistory } from "./session-history-loader";
/**
 * Conversation / branch / channel data layer.
 *
 * TS port of the legacy `public/js/shared/conversations.js`; `useWS`
 * calls the exported functions directly.
 */

import { mirrorUpsertConv } from "./conv-store-mirror";
import {
  draftChannelChoiceHost,
  switchDraftChannelChoice,
} from "../actions/draft-channel-choice";
import { runtimeState, getSocket, type TreeEntry } from "../state";
import { updateSessionGoal } from "../actions/goal-state";
import { useTodoStore } from "@/lib/chat/transcript/todo-plan";
import { setWelcomeVisible } from "../helpers";
import {
  refreshStatusSource,
  setChannelHealth,
  updateContextStats,
} from "../ui";
import { loadAgentSettings, loadProviders, updateProviderBadge } from "../providers";
import {
  refreshHistoryContextRange,
  renderHistoryGraph,
  repaintBranchTags,
} from "../dag";
import { rowsOfTurns } from "@/lib/chat/transcript/turn-rows";
import { applyReadToView } from "@/lib/chat/transcript/apply-read";
import { showNewChatRoute } from "@/lib/tabs/navigation/route";
import { useSessionStore } from "@/lib/session-store";
import {
  warmContextBreakdown,
  writeContextBreakdownCache,
} from "@/lib/chat/context-breakdown-cache";
import { pushBranchInfo } from "@/lib/tabs/top-bar-sync";
import { isDraftSession } from "@/lib/tabs/center-tabs-store";
import { showToast } from "@/lib/format-utils/toast";

interface LegacyConv {
  id?: string;
  title?: string;
  channel?: string | null;
  account_id?: string | null;
  peer?: string | null;
  graph?: unknown;
  head_id?: string | null;
  run_active?: boolean;
  status?: string;
  [k: string]: unknown;
}

interface TreeNode extends TreeEntry {
  children?: TreeNode[];
  params?: Record<string, unknown>;
  output?: unknown;
}

interface BranchRow {
  head_msg_id?: string;
  head_id?: string;
  name?: string;
  active?: boolean;
  [k: string]: unknown;
}

interface ChannelAccount {
  channel: string;
  account_id: string;
  name?: string;
  enabled?: boolean;
  configured?: boolean;
}

/** The app's single draft-channel-choice host, shared with the composer,
 *  the top-bar channel menu and the draft-persistence layer. */
const choiceHost = draftChannelChoiceHost;

/** `runtimeState.conversations` typed as this module's richer conv shape. */
function convs(): Record<string, LegacyConv> {
  return runtimeState.conversations as unknown as Record<string, LegacyConv>;
}

/* ===== Channel icons ============================================= */

export function channelIcon(plat: string): string {
  const letter = ((plat || "?")[0] || "?").toUpperCase();
  return '<span class="provider-icon-letter">' + letter + "</span>";
}

/* ===== Channel health poll ======================================= */

let channelHealthTimer: ReturnType<typeof setInterval> | null = null;
let channelHealthKey: string | null = null;

export function stopChannelHealthPoll(): void {
  if (channelHealthTimer) {
    clearInterval(channelHealthTimer);
    channelHealthTimer = null;
  }
  channelHealthKey = null;
  setChannelHealth("unknown");
}

export function startChannelHealthPoll(channel: string, accountId?: string): void {
  const key = channel + ":" + (accountId || "default");
  if (channelHealthKey === key) return;
  stopChannelHealthPoll();
  channelHealthKey = key;

  function probe(): void {
    if (channelHealthKey !== key) return;
    const url =
      "/api/channels/" +
      encodeURIComponent(channel) +
      "/" +
      encodeURIComponent(accountId || "default") +
      "/status";
    fetch(url, { cache: "no-store" })
      .then((r) => r.json())
      .then((data) => {
        if (channelHealthKey !== key) return;
        let state = "err";
        if (data.alive) state = "ok";
        else if (data.state === "unknown") state = "warn";
        setChannelHealth(state);
      })
      .catch(() => {
        if (channelHealthKey !== key) return;
        setChannelHealth("err");
      });
  }
  probe();
  channelHealthTimer = setInterval(probe, 5000);
}

/* ===== Channel accounts ========================================== */

let channelAccountsCache: ChannelAccount[] | null = null;
let channelAccountsPending: ((v: ChannelAccount[]) => void) | null = null;

export function fetchChannelAccounts(): Promise<ChannelAccount[]> {
  if (channelAccountsCache) return Promise.resolve(channelAccountsCache);
  if (channelAccountsPending) {
    return new Promise((res) => {
      const prev = channelAccountsPending!;
      channelAccountsPending = (v) => {
        prev(v);
        res(v);
      };
    });
  }
  return new Promise((res) => {
    channelAccountsPending = res;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "list_channel_accounts" }));
    } else {
      channelAccountsPending = null;
      res([]);
    }
    setTimeout(() => {
      if (channelAccountsPending === res) {
        channelAccountsPending = null;
        res(channelAccountsCache || []);
      }
    }, 3000);
  });
}

export function onChannelAccountsMessage(rows: ChannelAccount[]): void {
  channelAccountsCache = Array.isArray(rows) ? rows : [];
  if (channelAccountsPending) {
    const fn = channelAccountsPending;
    channelAccountsPending = null;
    fn(channelAccountsCache);
  }
}

export function refreshChannelBadge(): void {
  refreshStatusSource();
}

/* ===== Branches ================================================== */

const branchesByConv = runtimeState._branchesByConv as Record<string, BranchRow[]>;
const branchesPending: Record<string, (v: BranchRow[]) => void> = {};
const branchTokensByConv: Record<string, Record<string, unknown>> = {};

export function fetchBranches(
  sessionId: string | null | undefined,
  opts?: { force?: boolean },
): Promise<BranchRow[]> {
  if (!sessionId) return Promise.resolve([]);
  const force = !!(opts && opts.force);
  if (force) delete branchesByConv[sessionId];
  if (branchesByConv[sessionId]) return Promise.resolve(branchesByConv[sessionId]);
  if (branchesPending[sessionId]) {
    return new Promise((res) => {
      const prev = branchesPending[sessionId];
      branchesPending[sessionId] = (v) => {
        prev(v);
        res(v);
      };
    });
  }
  return new Promise((res) => {
    branchesPending[sessionId] = res;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "list_branches", session_id: sessionId, include_graph: false }));
    } else {
      delete branchesPending[sessionId];
      res([]);
    }
    setTimeout(() => {
      if (branchesPending[sessionId] === res) {
        delete branchesPending[sessionId];
        res(branchesByConv[sessionId] || []);
      }
    }, 3000);
  });
}

interface BranchesListPayload {
  session_id?: string;
  branches?: BranchRow[];
  graph?: unknown;
  active?: string | null;
  trunk_head?: string | null;
}

// HEAD 位置元数据（active head / 主干 tip），供顶栏 chip 判断
// "main / 分支名 / detached" 三态。
const branchMetaByConv: Record<
  string,
  { active?: string | null; trunk?: string | null }
> = {};

export function onBranchesListMessage(payload: BranchesListPayload): void {
  if (!payload || !payload.session_id) return;
  const sid = payload.session_id;
  const rows = Array.isArray(payload.branches) ? payload.branches : [];
  branchesByConv[sid] = rows;
  branchMetaByConv[sid] = {
    active: payload.active ?? null,
    trunk: payload.trunk_head ?? null,
  };
  if (branchesPending[sid]) {
    const fn = branchesPending[sid];
    delete branchesPending[sid];
    fn(rows);
  }
  if (sid === runtimeState.currentSessionId) {
    refreshBranchBadge();
    renderBranchesPanel();
    if (Array.isArray(payload.graph) && (typeof document === "undefined" || document.getElementById("historyPanel")?.getAttribute("aria-hidden") !== "true")) {
      renderHistoryGraph(payload.graph as never[], payload.active || null);
      refreshHistoryContextRange(sid);
      const conv = convs()[sid];
      if (conv) {
        conv.graph = payload.graph;
        if (payload.active) conv.head_id = payload.active;
      }
      // 把当前分支头镜像进 Zustand，让订阅 store 的 React 组件（如 /context
      // 弹窗）在切分支时自动感知并重取当前分支的上下文。
      useSessionStore.getState().setHead(sid, payload.active || null);
    } else {
      repaintBranchTags();
    }
  }
}

export async function refreshBranchTokens(): Promise<void> {
  const sid = runtimeState.currentSessionId;
  if (!sid) return;
  try {
    const r = await fetch(
      "/api/sessions/" + encodeURIComponent(sid) + "/branches/tokens",
    );
    if (!r.ok) return;
    const d = await r.json();
    const map: Record<string, unknown> = {};
    (d.branches || []).forEach((b: { head_id: string }) => {
      map[b.head_id] = b;
    });
    branchTokensByConv[sid] = map;
    renderBranchesPanel();
  } catch {
    /* ignore */
  }
}

// React <BranchesPanel /> listens for this event and re-reads
// `runtimeState._branchesByConv`.
function renderBranchesPanel(): void {
  window.dispatchEvent(new Event("branches-updated"));
}

/** Checkouts whose caller handles a refusal itself. */
const answeredCheckouts = new Set<string>();

/** Move the conversation to `headMsgId`. Resolves to the server's answer;
 *  a refusal is the caller's to show. */
export async function checkoutBranch(sessionId: string, headMsgId: string): Promise<{ ok: boolean; code?: string }> {
  await pendingBranchSettings(sessionId);
  const key = `${sessionId}\n${headMsgId}`;
  answeredCheckouts.add(key);
  try {
    const reply = await wsRequest<{ session_id?: string; head_msg_id?: string; ok?: boolean; code?: string }>(
      "checkout_branch", { session_id: sessionId, head_msg_id: headMsgId }, "branch_checked_out",
      (data) => data?.session_id === sessionId && data?.head_msg_id === headMsgId, 8000);
    if (reply?.ok) sendRuntimeCommand({ action: "load_session", session_id: sessionId });
    return { ok: !!reply?.ok, code: reply?.code };
  } finally {
    answeredCheckouts.delete(key);
  }
}

export function onBranchCheckedOut(payload: {
  ok?: boolean;
  code?: string;
  active_branch_id?: string;
  session_id?: string;
  head_msg_id?: string;
  workspace_alignment?: import("@/lib/session-store/types").ConvSummary["workspace_alignment"];
}): void {
  if (payload?.ok === false && !answeredCheckouts.has(`${payload.session_id}\n${payload.head_msg_id}`)) {
    showToast(translateText("Could not switch branches.", "切换分支失败。"), { tone: "error" });
  }
  if (!payload || !payload.ok || !payload.session_id) return;
  // Switching never waits for a turn; frames of the branch left stop
  // drawing from here on.
  adoptCheckedOutBranch(payload.session_id, payload);
  const sessionStore = useSessionStore.getState();
  const existing = sessionStore.conversations[payload.session_id] ?? {
    id: payload.session_id,
    title: "",
  };
  sessionStore.upsertConversation({
    ...existing,
    workspace_alignment: payload.workspace_alignment,
  });
  delete branchesByConv[payload.session_id];
  if (payload.session_id === runtimeState.currentSessionId) {
    fetchBranches(payload.session_id).then(() => refreshBranchBadge());
  }
}

export function onWorkspaceAlignmentResolved(payload: {
  ok?: boolean;
  session_id?: string;
  error?: string;
  workspace_alignment?: import("@/lib/session-store/types").ConvSummary["workspace_alignment"];
}): void {
  try {
    window.dispatchEvent(new CustomEvent("workspace-alignment-response", {
      detail: payload,
    }));
  } catch { /* defensive */ }
  if (!payload?.ok) {
    showToast(payload?.error || "Workspace alignment failed");
    return;
  }
  if (!payload.session_id) return;
  const store = useSessionStore.getState();
  const existing = store.conversations[payload.session_id] ?? {
    id: payload.session_id,
    title: "",
  };
  store.upsertConversation({
    ...existing,
    workspace_alignment: payload.workspace_alignment,
  });
}

export function refreshBranchBadge(): void {
  // React's BranchesPanel renders off the store; the legacy #branchBadge
  // element is optional (usually absent) so the push must come first.
  pushBranchInfo();
  const badge = document.getElementById("branchBadge");
  if (!badge) return;
  const sid = runtimeState.currentSessionId;
  if (!sid) {
    badge.style.display = "none";
    return;
  }
  const list = branchesByConv[sid] || [];
  if (list.length === 0) {
    badge.style.display = "none";
    return;
  }
  const active = list.find((b) => b.active);
  // 三态（学 git）：在命名分支上→分支名；在主干 tip 上→main（主线不是
  // 游离态）；checkout 到链中间的历史节点才是 detached。
  const meta = branchMetaByConv[sid] || {};
  const onTrunk = !!meta.active && !!meta.trunk && meta.active === meta.trunk;
  const label = active ? active.name : onTrunk ? "main" : "detached";
  const nameEl = badge.querySelector(".branch-name") as HTMLElement | null;
  if (nameEl) {
    nameEl.textContent = label + " (" + list.length + ")";
    nameEl.style.display = "inline-block";
    nameEl.style.maxWidth = "180px";
    nameEl.style.overflow = "hidden";
    nameEl.style.textOverflow = "ellipsis";
    nameEl.style.whiteSpace = "nowrap";
    nameEl.style.verticalAlign = "bottom";
  }
  badge.title = label + " (" + list.length + " branches)";
  badge.style.display = "";
}

/* ===== New session =============================================== */

export function newSession(draftId?: string): void {
  // Clear both session sources synchronously before SPA navigation. Otherwise
  // CenterTabStrip can observe /chat with the previous session id and replace
  // a newly claimed draft tab with that stale session.
  runtimeState.currentSessionId = null;
  const store = useSessionStore.getState();
  switchDraftChannelChoice(
    choiceHost,
    store.activeChatKey ?? store.currentSessionId,
    draftId,
  );
  if (draftId) store.setCurrentDraft(draftId);
  else store.setCurrentConv(null);

  showNewChatRoute();
  Object.keys(runtimeState.pendingResponses).forEach(
    (k) => delete runtimeState.pendingResponses[k],
  );
  runtimeState.trees.length = 0;
  const container = document.getElementById("chatMessages");
  if (container) {
    Array.from(container.children).forEach((ch) => {
      if (ch.id === "welcome-mount" || ch.id === "messages-mount") return;
      container.removeChild(ch);
    });
  }
  refreshChannelBadge();
  setWelcomeVisible(true);
  renderBranchesPanel();
  renderHistoryGraph([], null);
  const ctxEl = document.getElementById("contextStats");
  if (ctxEl) ctxEl.textContent = "";
  runtimeState._hasActiveSession = false;
  const provBadge = document.getElementById("providerBadge");
  if (provBadge) {
    provBadge.textContent = provBadge.textContent!.replace(" \u{1F512}", "");
  }
  const sessBadge = document.getElementById("sessionBadge");
  if (sessBadge) {
    sessBadge.textContent = "no session";
    sessBadge.title = "";
  }
  void loadProviders();
  void loadAgentSettings();
  refreshStatusSource();
  Object.keys(branchesByConv).forEach((k) => delete branchesByConv[k]);
  refreshBranchBadge();
}

/* ===== Load session ============================================== */

export function loadSessionData(data: LegacyConv): void {
  const id = data.id as string;
  if (Array.isArray(data.running_branches)) {
    const running = data.running_branches;
    void import("@/lib/chat/run/other-branch-runs").then(({ useOtherBranchRuns }) => {
      useOtherBranchRuns.getState().seed(id, running);
    });
  }
  const loadedSettings = data.settings as Record<string, unknown> | undefined;
  if (loadedSettings) {
    const store = useSessionStore.getState();
    const branchId = typeof data.active_branch_id === "string" ? data.active_branch_id : null;
    store.setComposerSettings(branchSettingsPatch(store.composerSettingsBySession[id] ?? {}, branchId, loadedSettings), id);
  }
  const turns = (data.turns as WireTurn[] | undefined) ?? [];
  const window = data.window as TurnWindow | undefined;
  const served = rowsOfTurns(turns);
  if (window) seedTurnWindow(id, turns, window);
  registerSessionHistory(id, window);
  const map = convs();
  // Merge data into existing conv. data 里没有的字段 (例如 created_at)
  // 不该被覆盖为 undefined; 显式 filter 一下 data 里的 undefined 值.
  const cleanedData: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined && k !== "goal" && k !== "todos" && k !== "todo_plans" && k !== "turns" && k !== "window") cleanedData[k] = v;
  }
  map[id] = Object.assign({}, map[id] || {}, cleanedData);
  // Keep the sidebar's store entry in sync with the freshly-loaded
  // session's summary fields (title / channel / preview / flags). A draft's
  // provisional key has no conversation yet: a load of it never adds a row.
  if (!isDraftSession(id)) mirrorUpsertConv(map[id] as Record<string, unknown>);
  // A slow session load must not overwrite a newer HTTP or live Goal update.
  try {
    const loaded = data as { goal?: { version?: number } | null };
    if (loaded.goal !== undefined) updateSessionGoal(id, loaded.goal);
  } catch { /* defensive */ }
  const loadedPlans = (data as { todo_plans?: unknown }).todo_plans;
  if (Array.isArray(loadedPlans)) useTodoStore.getState().setPlans(id, loadedPlans);
  // 清 transcript skeleton — 不能只在 currentSessionId 分支里清:
  // 回包晚到、用户已切走时, loading 态若还指着这个 id 也要释放。
  const st = useSessionStore.getState();
  if (st.transcriptLoadingId === id) st.setTranscriptLoading(null);
  // Every read lands in its view, focused or not: a split pane draws from it.
  applyReadToView(id, served);
  if (id !== runtimeState.currentSessionId) return;

  // `refreshChannelBadge` is just `refreshStatusSource` — one call covers
  // both of the legacy pair here.
  refreshStatusSource();
  delete branchesByConv[id];
  fetchBranches(id).then(() => refreshBranchBadge());

  renderSessionMessages(map[id]);
  refreshFocusedSession(id, data);
}

/** Focus moved to a session a split pane already shows and keeps current:
 *  refresh what follows the focused session (status, branches, provider,
 *  agent settings, context) from the loaded data. The transcript and its
 *  history window stay as the pane holds them; a reload would reset that
 *  window to the latest page and back. */
export function focusLoadedSession(id: string): void {
  const conv = convs()[id] as LegacyConv | undefined;
  if (!conv) return;
  refreshStatusSource();
  delete branchesByConv[id];
  fetchBranches(id).then(() => refreshBranchBadge());
  refreshFocusedSession(id, conv);
}

function refreshFocusedSession(id: string, data: LegacyConv): void {
  const map = convs();
  const fts = (data.function_trees as TreeNode[] | undefined) || [];
  for (const ft of fts) {
    if (ft && (ft.path || ft.name)) runtimeState.trees.push(ft);
  }
  if (data.provider_info) updateProviderBadge(data.provider_info as never);
  void loadAgentSettings();
  const headId = (map[id] as { head_id?: string | null }).head_id ?? null;
  const incoming = data.context_stats as { breakdown?: object } | undefined;
  if (incoming?.breakdown) {
    writeContextBreakdownCache(id, headId, incoming.breakdown as never);
  }
  if (data.context_stats) {
    // Lazy: chat-handlers imports this module, so a static import cycles.
    void import("./chat-handlers").then((m) =>
      m.handleChatResponse(data.context_stats as never),
    );
  } else {
    updateContextStats();
  }
  // Only hit GET /context when the session payload had no snapshot.
  if (!incoming?.breakdown) {
    warmContextBreakdown(id, headId, { focus: true });
  }
  // Viewport hooks restore history anchors after this data commit. Saved
  // pixels from a larger cached window are invalid in the latest page.

}

/* ===== Render session messages =================================== */

// Clear #chatMessages WITHOUT destroying the React portal hosts.
function clearChatMessages(container: HTMLElement | null): void {
  if (!container) return;
  Array.from(container.children).forEach((ch) => {
    if (ch.id === "welcome-mount" || ch.id === "messages-mount") return;
    container.removeChild(ch);
  });
}

export function renderSessionMessages(conv: LegacyConv): void {
  const container = document.getElementById("chatMessages");
  runtimeState.trees.length = 0;

  if (!conv.id || !(useSessionStore.getState().messageOrder[conv.id]?.length)) {
    clearChatMessages(container);
    setWelcomeVisible(true);
    return;
  }

  setWelcomeVisible(false);
  clearChatMessages(container);

  renderHistoryGraph((conv.graph as never[]) || [], conv.head_id || null);
  if (runtimeState.currentSessionId) {
    refreshHistoryContextRange(runtimeState.currentSessionId);
  }
  const chatContainer = document.getElementById("chatMessages");
  if (chatContainer) {
    const isRunning = conv.status === "running" || conv.run_active;
    chatContainer.setAttribute("data-run-active", isRunning ? "true" : "false");
  }

  if (!runtimeState.isRunning) {
    Object.keys(runtimeState.pendingResponses).forEach((k) => {
      delete runtimeState.pendingResponses[k];
    });
  }

  const pivot = runtimeState._postCheckoutScrollTo;
  if (pivot && container) {
    runtimeState._postCheckoutScrollTo = null;
    let pivotEl: Element | null = null;
    const key = window.CSS && CSS.escape ? CSS.escape(pivot) : pivot;
    const matches = container.querySelectorAll(
      '[data-msg-id="' + key + '"], [data-msg-ids~="' + key + '"]',
    );
    if (matches.length) pivotEl = matches[0];
    if (pivotEl) {
      requestAnimationFrame(() => {
        (pivotEl as HTMLElement).scrollIntoView({ behavior: "auto", block: "start" });
      });
      return;
    }
  }

}

/* ===== window bridge ============================================= */

// Still read through `window` by components/page-shell.tsx, which paints a
// cached transcript before this module's importers have run.
