/**
 * Conversation / branch / channel data layer.
 *
 * TS port of the legacy `public/js/shared/conversations.js`. The
 * functions are still bridged onto `window.*` so the not-yet-migrated
 * legacy scripts (ui.js / init.js / history-graph.js) can call them;
 * `useWS` calls the exported functions directly. As those legacy
 * modules migrate, the `window.*` calls inside here become direct
 * imports.
 *
 * Imported for side effects by `useWS` so the `window.*` assignments
 * run before any WS event fires.
 */

import { mirrorUpsertConv } from "./conv-store-mirror";
import { switchDraftChannelChoice } from "./draft-channel-choice";
import {
  readChatScroll,
  restoreChatScrollIfCurrent,
} from "@/lib/state/chat-scroll";

interface LegacyConv {
  id?: string;
  title?: string;
  messages?: LegacyMessage[];
  channel?: string | null;
  account_id?: string | null;
  peer?: string | null;
  graph?: unknown;
  head_id?: string | null;
  run_active?: boolean;
  status?: string;
  [k: string]: unknown;
}

interface LegacyMessage {
  role?: string;
  content?: string;
  display?: string;
  function?: string | null;
  type?: string;
  original_content?: string;
  context_tree?: unknown;
  attempts?: unknown[];
  current_attempt?: number;
  [k: string]: unknown;
}

interface TreeNode {
  path?: string;
  name?: string;
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

interface ConvWindow {
  ws?: WebSocket | null;
  currentSessionId?: string | null;
  conversations?: Record<string, LegacyConv>;
  trees?: TreeNode[];
  pendingResponses?: Record<string, unknown>;
  isRunning?: boolean;
  _skipScrollToBottom?: boolean;
  _hasActiveSession?: boolean;
  _pendingChannelChoice?: { channel: string | null; account_id: string | null } | null;
  __pendingChannelChoices?: Record<
    string,
    { channel: string | null; account_id: string | null }
  >;
  _branchesPanelCollapsed?: boolean;
  _postCheckoutScrollTo?: string | null;
  _allMessages?: LegacyMessage[];
  _branchesByConv?: Record<string, BranchRow[]>;
  __navigate?: (path: string) => void;
  __sessionStore?: {
    getState: () => {
      activeChatKey: string | null;
      currentSessionId: string | null;
      setCurrentConv: (id: string | null) => void;
      setCurrentDraft: (key: string) => void;
      setHead?: (sessionId: string, headId: string | null) => void;
      transcriptLoadingId?: string | null;
      setTranscriptLoading?: (id: string | null) => void;
    };
  };
  __feedStoreFromConv?: (conv: LegacyConv) => void;
  // Bridges to still-legacy modules.
  setStatusDotHealth?: (state: string) => void;
  refreshStatusSource?: () => void;
  refreshChannelBadge?: () => void;
  refreshBranchBadge?: () => void;
  repaintBranchTags?: () => void;
  renderBranchesPanel?: () => void;
  renderHistoryGraph?: (graph: unknown, active: string | null) => void;
  refreshHistoryContextRange?: (sid: string) => void;
  updateProviderBadge?: (info: unknown) => void;
  loadAgentSettings?: () => void;
  loadProviders?: () => void;
  handleChatResponse?: (data: unknown) => void;
  updateContextStats?: (messages: unknown[]) => void;
  setWelcomeVisible?: (show: boolean) => void;
  scrollToBottom?: (opts?: { force?: boolean }) => void;
  formatProgramResultContent?: (output: unknown) => string;
  _refreshBranchTokens?: () => void;
  // Own exports mirrored onto window for legacy callers.
  [k: string]: unknown;
}

const W = window as unknown as ConvWindow;

/* ===== Channel icons ============================================= */

// simple-icons CDN brand marks, each embedding the platform's own hue.
const CHANNEL_ICON_URL: Record<string, string> = {
  wechat: "https://cdn.simpleicons.org/wechat/07C160",
  discord: "https://cdn.simpleicons.org/discord/5865F2",
  telegram: "https://cdn.simpleicons.org/telegram/26A5E4",
  slack: "https://cdn.simpleicons.org/slack/4A154B",
};

export function channelIcon(plat: string): string {
  const lc = String(plat || "").toLowerCase();
  const url = CHANNEL_ICON_URL[lc];
  const letter = ((plat || "?")[0] || "?").toUpperCase();
  const letterSpan = '<span class="provider-icon-letter">' + letter + "</span>";
  if (!url) return letterSpan;
  // Guard `parentNode`: if the icon errors after the menu closed the
  // <img> is detached and setting outerHTML throws NoModificationAllowed.
  return (
    '<img src="' +
    url +
    '" alt="" onerror="if(this.parentNode)this.outerHTML=&quot;' +
    letterSpan.replace(/"/g, "&amp;quot;") +
    '&quot;">'
  );
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
        if (typeof W.setStatusDotHealth !== "function") return;
        let state = "err";
        if (data.alive) state = "ok";
        else if (data.state === "unknown") state = "warn";
        W.setStatusDotHealth(state);
      })
      .catch(() => {
        if (channelHealthKey !== key) return;
        W.setStatusDotHealth?.("err");
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
    if (W.ws && W.ws.readyState === WebSocket.OPEN) {
      W.ws.send(JSON.stringify({ action: "list_channel_accounts" }));
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

export function currentChannelChoice(): { channel: string | null; account_id: string | null } {
  const sid = W.currentSessionId;
  if (sid && W.conversations?.[sid]) {
    const c = W.conversations[sid];
    return { channel: c.channel || null, account_id: c.account_id || null };
  }
  return W._pendingChannelChoice || { channel: null, account_id: null };
}

function refreshChannelBadge(): void {
  W.refreshStatusSource?.();
}

/* ===== Sessions list (React owns rendering) ====================== */

function renderSessions(): void {
  // React owns this (components/sidebar/sessions-list.tsx).
}

/* ===== Branches ================================================== */

const branchesByConv: Record<string, BranchRow[]> = {};
W._branchesByConv = branchesByConv;
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
    if (W.ws && W.ws.readyState === WebSocket.OPEN) {
      W.ws.send(JSON.stringify({ action: "list_branches", session_id: sessionId }));
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
  if (sid === W.currentSessionId) {
    W.refreshBranchBadge?.();
    W.repaintBranchTags?.();
    renderBranchesPanel();
    if (Array.isArray(payload.graph) && typeof W.renderHistoryGraph === "function") {
      try {
        W.renderHistoryGraph(payload.graph, payload.active || null);
      } catch {
        /* ignore */
      }
      W.refreshHistoryContextRange?.(sid);
      const conv = W.conversations?.[sid];
      if (conv) {
        conv.graph = payload.graph;
        if (payload.active) conv.head_id = payload.active;
      }
      // 把当前分支头镜像进 Zustand，让订阅 store 的 React 组件（如 /context
      // 弹窗）在切分支时自动感知并重取当前分支的上下文。
      W.__sessionStore?.getState().setHead?.(sid, payload.active || null);
    }
  }
}

export async function refreshBranchTokens(): Promise<void> {
  const sid = W.currentSessionId;
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
// `window._branchesByConv`.
function renderBranchesPanel(): void {
  window.dispatchEvent(new Event("branches-updated"));
}

export function onBranchCheckedOut(payload: {
  ok?: boolean;
  session_id?: string;
}): void {
  if (!payload || !payload.ok || !payload.session_id) return;
  delete branchesByConv[payload.session_id];
  if (payload.session_id === W.currentSessionId && typeof W.refreshBranchBadge === "function") {
    fetchBranches(payload.session_id).then(W.refreshBranchBadge);
  }
}

function refreshBranchBadge(): void {
  const badge = document.getElementById("branchBadge");
  if (!badge) return;
  const sid = W.currentSessionId;
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
  const needsNavigation = window.location.pathname !== "/chat";
  // Clear both session sources synchronously before SPA navigation. Otherwise
  // CenterTabStrip can observe /chat with the previous session id and replace
  // a newly claimed draft tab with that stale session.
  W.currentSessionId = null;
  try {
    const store = W.__sessionStore?.getState();
    switchDraftChannelChoice(
      W,
      store?.activeChatKey ?? store?.currentSessionId,
      draftId,
    );
    if (draftId) store?.setCurrentDraft(draftId);
    else store?.setCurrentConv(null);
  } catch {
    /* ignore */
  }

  if (needsNavigation) {
    if (W.__navigate) {
      W.__navigate("/chat");
    } else {
      window.location.href = "/chat";
      return;
    }
  } else {
    history.replaceState(null, "", "/chat");
  }
  W.pendingResponses = {};
  W.trees = [];
  const container = document.getElementById("chatMessages");
  if (container) {
    Array.from(container.children).forEach((ch) => {
      if (ch.id === "welcome-mount" || ch.id === "messages-mount") return;
      container.removeChild(ch);
    });
  }
  refreshChannelBadge();
  W.setWelcomeVisible?.(true);
  renderSessions();
  renderBranchesPanel();
  if (typeof W.renderHistoryGraph === "function") {
    try {
      W.renderHistoryGraph([], null);
    } catch {
      /* ignore */
    }
  }
  const ctxEl = document.getElementById("contextStats");
  if (ctxEl) ctxEl.textContent = "";
  W._hasActiveSession = false;
  const provBadge = document.getElementById("providerBadge");
  if (provBadge) {
    provBadge.textContent = provBadge.textContent!.replace(" \u{1F512}", "");
  }
  const sessBadge = document.getElementById("sessionBadge");
  if (sessBadge) {
    sessBadge.textContent = "no session";
    sessBadge.title = "";
  }
  W.loadProviders?.();
  W.loadAgentSettings?.();
  W.refreshStatusSource?.();
  Object.keys(branchesByConv).forEach((k) => delete branchesByConv[k]);
  refreshBranchBadge();
}

/* ===== Load session ============================================== */

export function loadSessionData(data: LegacyConv): void {
  if (!data.messages) data.messages = [];
  const id = data.id as string;
  const convs = W.conversations || (W.conversations = {});
  // Merge data into existing conv. data 里没有的字段 (例如 created_at)
  // 不该被覆盖为 undefined; 显式 filter 一下 data 里的 undefined 值.
  const cleanedData: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined) cleanedData[k] = v;
  }
  convs[id] = Object.assign({}, convs[id] || {}, cleanedData);
  // Keep the sidebar's store entry in sync with the freshly-loaded
  // session's summary fields (title / channel / preview / flags).
  mirrorUpsertConv(convs[id] as Record<string, unknown>);
  renderSessions();
  // 清 transcript skeleton — 不能只在 currentSessionId 分支里清:
  // 回包晚到、用户已切走时, loading 态若还指着这个 id 也要释放。
  try {
    const st = W.__sessionStore?.getState();
    if (st?.transcriptLoadingId === id) st.setTranscriptLoading?.(null);
  } catch {
    /* ignore */
  }
  W._branchesPanelCollapsed = true;
  if (id === W.currentSessionId) {
    W.refreshStatusSource?.();
    refreshChannelBadge();
    delete branchesByConv[id];
    fetchBranches(id).then(() => refreshBranchBadge());
  }
  if (id === W.currentSessionId) {
    const area = document.getElementById("chatArea");
    const savedScroll = readChatScroll(sessionStorage, id);
    const hasSavedScroll = savedScroll !== null;
    if (hasSavedScroll) W._skipScrollToBottom = true;
    renderSessionMessages(convs[id]);
    const fts = (data.function_trees as TreeNode[] | undefined) || [];
    if (fts.length > 0) {
      const trees = W.trees || (W.trees = []);
      for (const ft of fts) {
        if (ft && (ft.path || ft.name)) trees.push(ft);
      }
    }
    if (data.provider_info) W.updateProviderBadge?.(data.provider_info);
    W.loadAgentSettings?.();
    if (data.context_stats) {
      W.handleChatResponse?.(data.context_stats);
    } else {
      W.updateContextStats?.(data.messages || []);
    }
    if (area && savedScroll !== null) {
      requestAnimationFrame(() => {
        restoreChatScrollIfCurrent(
          area,
          id,
          W.currentSessionId ?? null,
          savedScroll,
        );
      });
    }
  }
}

/* ===== Tree → messages =========================================== */

export function extractMessagesFromTree(tree: TreeNode): LegacyMessage[] {
  if (!tree || !tree.children) return [];
  const messages: LegacyMessage[] = [];
  const fmt = W.formatProgramResultContent;
  for (const child of tree.children) {
    if (child.name === "_chat_query") {
      const query = child.params && child.params.query;
      if (query) messages.push({ role: "user", content: String(query) });
      if (child.output) {
        messages.push({
          role: "assistant",
          content: fmt ? fmt(child.output) : String(child.output),
          type: "result",
          function: null,
        });
      }
    } else if (child.name && child.name !== "_chat_query" && !child.name.startsWith("_")) {
      const funcName = child.name;
      const kwargs = child.params || {};
      const argStr = Object.entries(kwargs)
        .filter((e) => e[0] !== "runtime")
        .map((e) => e[0] + "=" + JSON.stringify(e[1]))
        .join(" ");
      messages.push({
        role: "user",
        content: "run " + funcName + (argStr ? " " + argStr : ""),
        display: "runtime",
      });
      if (child.output) {
        messages.push({
          role: "assistant",
          content: fmt ? fmt(child.output) : String(child.output),
          type: "result",
          function: funcName,
          display: "runtime",
        });
      }
    }
  }
  if (messages.length > 0) {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") {
        messages[i].context_tree = tree;
        break;
      }
    }
  }
  return messages;
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
  W.trees = [];

  W.__feedStoreFromConv?.(conv);

  if (!conv.messages || conv.messages.length === 0) {
    clearChatMessages(container);
    W.setWelcomeVisible?.(true);
    return;
  }

  W.setWelcomeVisible?.(false);
  clearChatMessages(container);

  W._allMessages = conv.messages.slice();
  if (typeof W.renderHistoryGraph === "function") {
    W.renderHistoryGraph(conv.graph || [], conv.head_id || null);
    if (W.currentSessionId) W.refreshHistoryContextRange?.(W.currentSessionId);
  }
  const chatContainer = document.getElementById("chatMessages");
  if (chatContainer) {
    const isRunning = conv.status === "running" || conv.run_active;
    chatContainer.setAttribute("data-run-active", isRunning ? "true" : "false");
  }

  try {
    if (!W.isRunning) {
      Object.keys(W.pendingResponses || {}).forEach((k) => {
        delete W.pendingResponses![k];
      });
    }
  } catch {
    /* ignore */
  }

  const pivot = W._postCheckoutScrollTo;
  if (pivot && container) {
    W._postCheckoutScrollTo = null;
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
      W._skipScrollToBottom = false;
      return;
    }
  }

  if (!W._skipScrollToBottom) W.scrollToBottom?.({ force: true });
  W._skipScrollToBottom = false;
}

/* ===== window bridges for still-legacy callers =================== */

W._stopChannelHealthPoll = stopChannelHealthPoll;
W._startChannelHealthPoll = startChannelHealthPoll;
W._channelIcon = channelIcon;
W.fetchChannelAccounts = fetchChannelAccounts;
W._onChannelAccountsMessage = onChannelAccountsMessage;
W._currentChannelChoice = currentChannelChoice;
W.refreshChannelBadge = refreshChannelBadge;
W.renderSessions = renderSessions;
W.fetchBranches = fetchBranches;
W._onBranchesListMessage = onBranchesListMessage;
W._refreshBranchTokens = refreshBranchTokens;
W.renderBranchesPanel = renderBranchesPanel;
W._onBranchCheckedOut = onBranchCheckedOut;
W.refreshBranchBadge = refreshBranchBadge;
W.newSession = newSession;
W.loadSessionData = loadSessionData;
W.extractMessagesFromTree = extractMessagesFromTree;
W.renderSessionMessages = renderSessionMessages;
