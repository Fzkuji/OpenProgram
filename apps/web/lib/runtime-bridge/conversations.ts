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
} from "./draft-channel-choice";
import { runtimeState, getSocket, type TreeEntry } from "./state";
import { updateSessionGoal } from "./goal-state";
import {
  formatProgramResultContent,
  scrollToBottom,
  setWelcomeVisible,
} from "./helpers";
import {
  refreshStatusSource,
  setStatusDotHealth,
  updateContextStats,
} from "./ui";
import { loadAgentSettings, loadProviders, updateProviderBadge } from "./providers";
import {
  refreshHistoryContextRange,
  renderHistoryGraph,
  repaintBranchTags,
} from "./dag";
import { convToChatMsgs } from "@/lib/conv-mapper";
import { navigate } from "@/lib/navigate";
import { useSessionStore } from "@/lib/session-store";
import {
  warmContextBreakdown,
  writeContextBreakdownCache,
} from "@/lib/state/context-breakdown-cache";
import { pushBranchInfo } from "@/lib/top-bar-sync";
import { showToast } from "@/lib/format-utils/toast";
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

/** Mirror a conversation's messages into the React store — the only feed
 *  for a transcript that is not the focused session. */
function feedStoreFromConv(conv: LegacyConv): void {
  if (!conv || !conv.id) return;
  useSessionStore
    .getState()
    .setMessages(conv.id, convToChatMsgs((conv.messages as never[]) || []));
}

/** Rebuild card + event rows when the wire list omitted them. */
function spliceCompactionFromGraph(
  messages: LegacyMessage[],
  graph: unknown,
): LegacyMessage[] {
  if (messages.some((m) => m.kind === "compaction" && (m.slot === "card" || m.slot === "event"))) {
    return messages;
  }
  if (!Array.isArray(graph)) return messages;
  const inserts: { at: number; row: LegacyMessage }[] = [];
  const index = new Map<string, number>();
  messages.forEach((m, i) => {
    if (typeof m.id === "string" && m.id) index.set(m.id, i);
  });
  for (const raw of graph) {
    if (!raw || typeof raw !== "object") continue;
    const n = raw as Record<string, unknown>;
    const id = typeof n.id === "string" ? n.id : "";
    if (!id) continue;
    const covers = Array.isArray(n.covers_ids)
      ? (n.covers_ids as unknown[]).map(String).filter(Boolean)
      : [];
    const active = covers.length > 0 && !n.superseded_summary;
    const relic = !!n.superseded_summary;
    if (!active && !relic) continue;
    const nCov = typeof n.summarised_count === "number" ? n.summarised_count : covers.length;
    const execTs = typeof n.compacted_at === "number" ? n.compacted_at : 0;
    let execAt = messages.length;
    if (execTs) {
      let at = 0;
      messages.forEach((m, i) => {
        const t = typeof m.timestamp === "number" ? m.timestamp
          : typeof m.created_at === "number" ? m.created_at : 0;
        if (t && t <= execTs) at = i + 1;
      });
      execAt = at;
    }
    const tb = typeof n.tokens_before === "number" ? n.tokens_before : undefined;
    const ta = typeof n.tokens_after === "number" ? n.tokens_after : undefined;
    inserts.push({
      at: execAt,
      row: {
        id: `${id}_ui`,
        role: "system",
        kind: "compaction",
        slot: "event",
        summarised_count: nCov,
        tokens_before: tb,
        tokens_after: ta,
        content: tb != null && ta != null
          ? `Context compacted here: covered ${nCov} messages, ${tb} → ${ta} tokens`
          : `Context compacted here: covered ${nCov} messages`,
        timestamp: execTs || n.created_at,
      },
    });
    if (!active) continue;
    const coverSet = new Set(covers);
    const covAt = covers.map((c) => index.get(c)).filter((i): i is number => i !== undefined);
    const fold = covAt.length
      ? Math.max(...covAt) + 1
      : Math.max(0, messages.findIndex((m) => typeof m.id === "string" && m.id && !coverSet.has(m.id)));
    inserts.push({
      at: fold,
      row: {
        id: `${id}_card`,
        role: "system",
        kind: "compaction",
        slot: "card",
        summarised_count: nCov,
        covers_ids: covers,
        content: typeof n.preview === "string" ? n.preview : "",
        timestamp: execTs || n.created_at,
      },
    });
  }
  if (!inserts.length) return messages;
  inserts.sort((a, b) => b.at - a.at);
  const out = messages.slice();
  for (const { at, row } of inserts) out.splice(at, 0, row);
  return out;
}

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
        let state = "err";
        if (data.alive) state = "ok";
        else if (data.state === "unknown") state = "warn";
        setStatusDotHealth(state);
      })
      .catch(() => {
        if (channelHealthKey !== key) return;
        setStatusDotHealth("err");
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

export function currentChannelChoice(): { channel: string | null; account_id: string | null } {
  const sid = runtimeState.currentSessionId;
  const c = sid ? convs()[sid] : null;
  if (c) {
    return { channel: c.channel || null, account_id: c.account_id || null };
  }
  return choiceHost._pendingChannelChoice || { channel: null, account_id: null };
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
      sock.send(JSON.stringify({ action: "list_branches", session_id: sessionId }));
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
    if (Array.isArray(payload.graph)) {
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

export function onBranchCheckedOut(payload: {
  ok?: boolean;
  session_id?: string;
  workspace_alignment?: import("@/lib/session-store/types").ConvSummary["workspace_alignment"];
}): void {
  if (!payload || !payload.ok || !payload.session_id) return;
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
  const needsNavigation = window.location.pathname !== "/chat";
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

  if (needsNavigation) {
    navigate("/chat");
  } else {
    history.replaceState(null, "", "/chat");
  }
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
  if (!data.messages) data.messages = [];
  data.messages = spliceCompactionFromGraph(data.messages, data.graph);
  const id = data.id as string;
  const map = convs();
  // Merge data into existing conv. data 里没有的字段 (例如 created_at)
  // 不该被覆盖为 undefined; 显式 filter 一下 data 里的 undefined 值.
  const cleanedData: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined && k !== "goal") cleanedData[k] = v;
  }
  map[id] = Object.assign({}, map[id] || {}, cleanedData);
  // Keep the sidebar's store entry in sync with the freshly-loaded
  // session's summary fields (title / channel / preview / flags).
  mirrorUpsertConv(map[id] as Record<string, unknown>);
  // A slow session load must not overwrite a newer HTTP or live Goal update.
  try {
    const loaded = data as { goal?: { version?: number } | null };
    if (loaded.goal !== undefined) updateSessionGoal(id, loaded.goal);
  } catch { /* defensive */ }
  // 清 transcript skeleton — 不能只在 currentSessionId 分支里清:
  // 回包晚到、用户已切走时, loading 态若还指着这个 id 也要释放。
  const st = useSessionStore.getState();
  if (st.transcriptLoadingId === id) st.setTranscriptLoading(null);
  // A session loaded while NOT focused still needs its messages in the
  // React store — that's what a split-view peer pane renders from.
  // `renderSessionMessages` below is the only path that feeds the store,
  // and it's gated on `currentSessionId` because the rest of what it does
  // is legacy-DOM work for the focused chat. Feed the store directly here
  // so a peer's transcript lands without touching the focused session.
  if (id !== runtimeState.currentSessionId) {
    feedStoreFromConv(map[id]);
    return;
  }

  // `refreshChannelBadge` is just `refreshStatusSource` — one call covers
  // both of the legacy pair here.
  refreshStatusSource();
  delete branchesByConv[id];
  fetchBranches(id).then(() => refreshBranchBadge());

  const area = document.getElementById("chatArea");
  const savedScroll = readChatScroll(sessionStorage, id);
  if (savedScroll !== null) runtimeState._skipScrollToBottom = true;
  renderSessionMessages(map[id]);
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
    warmContextBreakdown(id, headId);
  }
  if (area && savedScroll !== null) {
    requestAnimationFrame(() => {
      restoreChatScrollIfCurrent(
        area,
        id,
        runtimeState.currentSessionId ?? null,
        savedScroll,
      );
    });
  }
}

/* ===== Tree → messages =========================================== */

export function extractMessagesFromTree(tree: TreeNode): LegacyMessage[] {
  if (!tree || !tree.children) return [];
  const messages: LegacyMessage[] = [];
  const fmt = formatProgramResultContent;
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
  runtimeState.trees.length = 0;

  feedStoreFromConv(conv);

  if (!conv.messages || conv.messages.length === 0) {
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
      runtimeState._skipScrollToBottom = false;
      return;
    }
  }

  if (!runtimeState._skipScrollToBottom) scrollToBottom({ force: true });
  runtimeState._skipScrollToBottom = false;
}

/* ===== window bridge ============================================= */

// Still read through `window` by components/page-shell.tsx, which paints a
// cached transcript before this module's importers have run.
