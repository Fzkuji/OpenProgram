/**
 * Chat-page WebSocket handlers.
 *
 * TS port of the legacy `public/js/chat/{init,chat-ws,chat}.js`. These
 * are the WS message handlers (`chat_ack` / `chat_response` / `status`
 * / `sessions_list` / `running_task`) plus the retry / follow-up glue.
 * `useWS` calls the exported functions directly; some are still bridged
 * onto `window.*` for inline-onclick HTML, React components and the
 * not-yet-migrated legacy scripts.
 *
 * Imported for side effects + `initChatPage()` by `useWS`.
 */

import {
  extractMessagesFromTree,
  fetchBranches,
  renderSessionMessages,
} from "./conversations";
import {
  mirrorSetConvs,
  mirrorUpsertConv,
} from "./conv-store-mirror";

interface ChatWindow {
  ws?: WebSocket | null;
  currentSessionId?: string | null;
  conversations?: Record<string, Record<string, unknown>>;
  pendingResponses?: Record<string, unknown>;
  isPaused?: boolean;
  isRunning?: boolean;
  _elapsedTimer?: ReturnType<typeof setInterval> | null;
  trees?: { path?: string; name?: string }[];
  _thinkingEffort?: string;
  _execThinkingEffort?: string;
  _branchesByConv?: Record<string, unknown>;
  _hasActiveSession?: boolean;
  _toolsEnabled?: boolean;
  _webSearchEnabled?: boolean;
  _lastRunCommand?: string | null;
  __sessionStore?: {
    getState: () => {
      setContextStats: (
        sid: string,
        t: {
          input: number;
          output: number;
          cache_read: number;
          cache_create?: number;
          model?: string | null;
          provider?: string | null;
        },
        ctx: number | null,
      ) => void;
    };
  };
  // Bridges to still-legacy modules.
  escHtml?: (s: unknown) => string;
  escAttr?: (s: unknown) => string;
  parseRunCommandForDisplay?: (t: string) => { funcName: string; params: string };
  scrollToBottom?: (opts?: { force?: boolean }) => void;
  setWelcomeVisible?: (show: boolean) => void;
  addSystemMessage?: (text: string) => void;
  setRunning?: (running: boolean) => void;
  updatePauseBtn?: () => void;
  loadAgentSettings?: () => void;
  loadProviders?: () => void;
  refreshChannelBadge?: () => void;
  refreshBranchBadge?: () => void;
  refreshStatusSource?: () => void;
  refreshTokenBadge?: () => void;
  _renderTokenBadge?: (data: unknown, sid: string) => void;
  _recordCacheWrite?: (sid: string) => void;
  refreshHistoryContextRange?: (sid: string) => void;
  _refreshBranchTokens?: () => void;
  _updatePlusBtnIndicator?: () => void;
  _refreshWebSearchProviderLabel?: () => void;
  renderSessions?: () => void;
  [k: string]: unknown;
}

const W = window as unknown as ChatWindow;

/* ===== Run-active flag =========================================== */

// `data-run-active` on #chatMessages drives CSS greying-out of
// Edit/Retry while a run is in flight.
export function setRunActive(active: boolean): void {
  const c = document.getElementById("chatMessages");
  if (c) c.setAttribute("data-run-active", active ? "true" : "false");
}

/* ===== chat_ack / chat_response / status ========================= */

interface ChatAckData {
  session_id?: string;
  msg_id?: string;
}

export function wsHandleChatAck(data: ChatAckData): void {
  if (data.session_id) {
    const sid = data.session_id;
    W.currentSessionId = sid;
    if (window.location.pathname !== "/s/" + sid) {
      history.pushState(null, "", "/s/" + sid);
    }
    const convs = W.conversations || (W.conversations = {});
    if (!convs[sid]) {
      // Seed a preview + created_at from the just-sent user text so the
      // row shows in the sidebar IMMEDIATELY (on run start), not after
      // the turn finishes. Without a preview, isEmptyPlaceholder() filters
      // a "New conversation"-titled row out until the backend re-lists it
      // with a preview at turn end — which read as "the chat only appears
      // after it's done".
      const pending =
        (W as unknown as { __pendingUserText?: string }).__pendingUserText || "";
      const preview = pending.trim().replace(/\s+/g, " ").slice(0, 80);
      convs[sid] = {
        id: sid,
        title: "New conversation",
        messages: [],
        preview: preview || null,
        created_at: Date.now() / 1000,
      };
    }
    // Mirror the (seeded or pre-existing) conv into the React store so the
    // sidebar row appears IMMEDIATELY — store.conversations is the
    // sidebar's source of truth.
    mirrorUpsertConv(convs[sid]);
    // Light the row's running animation (convRunningFlow) on THIS tab
    // immediately — keyed on the real sid the server just assigned, so
    // it's idempotent with the incoming running_task broadcast (which
    // overwrites the same key with a richer payload). Without this the
    // sending tab's row appears but doesn't flow until that round-trip.
    const _store = (window as unknown as {
      __sessionStore?: {
        getState: () => {
          setRunningTaskFor?: (s: string, t: unknown) => void;
        };
      };
    }).__sessionStore?.getState();
    _store?.setRunningTaskFor?.(sid, { session_id: sid, msg_id: data.msg_id || "" });
    W.renderSessions?.();
    W.loadAgentSettings?.();
    W.refreshChannelBadge?.();
    // A fresh session never went through `load_session`, so fetch the
    // branch list now that the server registered the user turn.
    if (W._branchesByConv) delete W._branchesByConv[sid];
    fetchBranches(sid).then(() => {
      W.refreshBranchBadge?.();
    });
  }
  // A fresh chat_ack means a run just started — grey out Edit/Retry.
  setRunActive(true);
}

interface ChatResponseData {
  type?: string;
  [k: string]: unknown;
}

export function wsHandleChatResponse(data: ChatResponseData): void {
  // Cancelled envelope without a msg_id is the force-stop signal.
  if (data && data.type === "cancelled") {
    try {
      const rp = document.getElementById("runtime_pending");
      if (rp && rp.parentNode) rp.parentNode.removeChild(rp);
    } catch {
      /* ignore */
    }
    try {
      Object.keys(W.pendingResponses || {}).forEach((k) => {
        delete W.pendingResponses![k];
      });
    } catch {
      /* ignore */
    }
    setRunActive(false);
    W.setRunning?.(false);
    return;
  }
  handleChatResponse(data);
  if (data && (data.type === "result" || data.type === "error")) {
    setRunActive(false);
  }
}

interface StatusMsg {
  paused?: boolean;
  stopped?: boolean;
}

export function wsHandleStatus(msg: StatusMsg): void {
  W.isPaused = msg.paused;
  if (msg.stopped) {
    W.isRunning = false;
    if (W._elapsedTimer) {
      clearInterval(W._elapsedTimer);
      W._elapsedTimer = null;
    }
  }
  W.updatePauseBtn?.();
}

/* ===== sessions_list / running_task ============================== */

interface SessionRow {
  id: string;
  title?: string;
  created_at?: number;
  channel?: string | null;
  account_id?: string | null;
  peer?: string | null;
  peer_display?: string | null;
  source?: string | null;
  agent_id?: string | null;
  preview?: string | null;
  pinned?: boolean;
  archived?: boolean;
  group?: string | null;
  /** Lifecycle status for the sidebar's leading dot (Claude-Code-style):
   *  "needs_input" → amber, "done" → completed; pairs with `unread`. */
  status?: "needs_input" | "done" | "idle" | null;
  /** Finished result not yet opened → blue dot. */
  unread?: boolean;
  /** Project NAME this conversation belongs to (home-folder name for
   *  ad-hoc chats) — drives the sidebar's "group by project" view. */
  project?: string | null;
}

export function handleSessionsList(data: SessionRow[]): void {
  const convs = W.conversations || (W.conversations = {});
  const serverIds = new Set((data || []).map((c) => c.id));
  Object.keys(convs).forEach((id) => {
    if (!serverIds.has(id)) delete convs[id];
  });
  if (data && data.length > 0) {
    for (const c of data) {
      if (!convs[c.id]) {
        convs[c.id] = {
          id: c.id,
          title: c.title,
          messages: [],
          created_at: c.created_at,
          channel: c.channel || null,
          account_id: c.account_id || null,
          peer: c.peer || null,
          peer_display: c.peer_display || null,
          source: c.source || null,
          agent_id: c.agent_id || null,
          preview: c.preview || null,
          pinned: !!c.pinned,
          archived: !!c.archived,
          group: c.group || "",
          status: c.status || undefined,
          unread: !!c.unread,
          project: c.project || "",
        };
      } else {
        if (c.created_at && (!convs[c.id].created_at || convs[c.id].created_at === 0)) {
          convs[c.id].created_at = c.created_at;
        }
        if ("channel" in c) convs[c.id].channel = c.channel || null;
        if ("account_id" in c) convs[c.id].account_id = c.account_id || null;
        if ("peer" in c) convs[c.id].peer = c.peer || null;
        if ("peer_display" in c) convs[c.id].peer_display = c.peer_display || null;
        if ("preview" in c) convs[c.id].preview = c.preview || null;
        // Conversation-management flags are authoritative from the
        // server on every list — always overwrite (unlike title/preview
        // which we only backfill) so a pin/archive/group change made in
        // another tab propagates here on the next list.
        if ("pinned" in c) convs[c.id].pinned = !!c.pinned;
        if ("archived" in c) convs[c.id].archived = !!c.archived;
        if ("group" in c) convs[c.id].group = c.group || "";
        if ("status" in c) convs[c.id].status = c.status || undefined;
        if ("unread" in c) convs[c.id].unread = !!c.unread;
        if ("project" in c) convs[c.id].project = c.project || "";
        // session_loaded 早到时 conv 没 created_at, 这里 sessions_list
        // 后到要补上, 不然 sidebar 排序拿不到时间戳, 新会话沉底.
        if (c.created_at != null && convs[c.id].created_at == null) {
          convs[c.id].created_at = c.created_at;
        }
        if (c.title && !convs[c.id].title) convs[c.id].title = c.title;
      }
    }
  }
  // Replace the React store's summary map from the freshly-synced legacy
  // map (handles adds / deletes / field updates in one pass). The sidebar
  // reads store.conversations, so this is what makes the list authoritative.
  mirrorSetConvs(Object.values(convs));
  const sid = W.currentSessionId;
  if (sid && !convs[sid]) {
    newSessionImport();
  }
  W.renderSessions?.();
  if (sid && convs[sid]) {
    W._hasActiveSession = true;
    const provBadge = document.getElementById("providerBadge");
    if (provBadge && provBadge.textContent!.indexOf("\u{1F512}") === -1) {
      provBadge.textContent += " \u{1F512}";
    }
    W.loadProviders?.();
  }
}

// `newSession` lives in conversations.ts; call it lazily through
// window to avoid an import cycle (it's only hit on a stale id).
function newSessionImport(): void {
  (W.newSession as (() => void) | undefined)?.();
}

/** Patch a single conversation's title / pinned / archived / group
 *  in place from a ``session_updated`` echo, then re-render. Lets a
 *  rename / pin / archive / move-to-group done in this tab (or another
 *  client) reflect immediately without a full re-list. */
export function handleSessionUpdated(
  data: {
    id?: string;
    title?: string;
    pinned?: boolean;
    archived?: boolean;
    group?: string | null;
    status?: "needs_input" | "done" | "idle" | null;
    unread?: boolean;
  } | null,
): void {
  if (!data || !data.id) return;
  const convs = W.conversations;
  const conv = convs?.[data.id];
  if (!conv) return;
  if (typeof data.title === "string") conv.title = data.title;
  if ("pinned" in data) conv.pinned = !!data.pinned;
  if ("archived" in data) conv.archived = !!data.archived;
  if ("group" in data) conv.group = data.group || "";
  if ("status" in data) conv.status = data.status || undefined;
  if ("unread" in data) conv.unread = !!data.unread;
  mirrorUpsertConv(conv);
  W.renderSessions?.();
}

export function handleRunningTask(rt: unknown): void {
  if (!rt) return;
  const t = rt as {
    session_id?: string;
    msg_id?: string;
    func_name?: string;
    started_at?: number;
    display_params?: string;
    stream_events?: unknown[];
  };

  // 1) Flip the composer's send/stop button immediately — but only
  //    if this event targets the currently-active session. Without
  //    this guard, a background session starting a turn would also
  //    flip the composer for whatever other session the user is
  //    looking at right now.
  if (!t.session_id || t.session_id === W.currentSessionId) {
    W.setRunning?.(true);
  }

  // 2) Mark the in-flight assistant message as "running" in the
  //    React store so its bubble shows the waiting indicator. The
  //    backend has already persisted the assistant placeholder + any
  //    tool rows that fired before the refresh; the WS load gave us
  //    the message but with status="done" (placeholder content is
  //    empty). Without this patch, the chat looked finished even
  //    though the turn was still running server-side.
  const sid = t.session_id;
  const mid = t.msg_id;
  if (!sid || !mid) return;
  try {
    const w = window as unknown as {
      __sessionStore?: {
        getState: () => {
          messagesById?: Record<string, { id: string; status?: string }>;
          updateMessage?: (
            sessionId: string,
            msgId: string,
            patch: Record<string, unknown>,
          ) => void;
          setRunningTask?: (task: unknown) => void;
          setRunningTaskFor?: (
            sessionId: string,
            task: unknown,
          ) => void;
        };
      };
    };
    const store = w.__sessionStore?.getState();
    if (!store) return;
    const replyId = mid + "_reply";
    const replyMsg = store.messagesById?.[replyId];
    if (replyMsg && store.updateMessage) {
      store.updateMessage(sid, replyId, { status: "running" });
    } else if (store.updateMessage) {
      store.updateMessage(sid, mid, { status: "running" });
    }
    const taskPayload = {
      session_id: sid,
      msg_id: mid,
      func_name: t.func_name,
      started_at: t.started_at,
    };
    store.setRunningTaskFor?.(sid, taskPayload);
  } catch {
    // store not yet mounted (legacy-only page) — fall back to the
    // simple button flip above.
  }
  // A function run dispatched via POST /api/function (fn-form, welcome
  // button, retry) streams NO transcript placeholder — its code node is
  // persisted server-side only, so a user watching the session sees a
  // blank transcript for the whole run. When the run starts in the
  // session we're viewing, hydrate once so the pending card appears
  // (tree_update then fills it live) and ask for one more hydrate on
  // completion (running_task_clear) for the final result/branch state.
  // Chat turns (func_name "_chat") stream their own rows — reloading
  // mid-stream would reset the live bubble, so they're excluded. The
  // per-msg_id guard also stops the re-emit inside the load_session
  // response from looping.
  const live = window as Window & {
    currentSessionId?: string;
    ws?: WebSocket;
    __functionRunHydrated?: string | null;
    __reloadOnTaskClear?: string | null;
  };
  if (
    t.func_name &&
    t.func_name !== "_chat" &&
    live.currentSessionId === sid &&
    live.__functionRunHydrated !== mid &&
    live.ws &&
    live.ws.readyState === WebSocket.OPEN
  ) {
    live.__functionRunHydrated = mid;
    live.__reloadOnTaskClear = sid;
    live.ws.send(JSON.stringify({ action: "load_session", session_id: sid }));
  }
}

export function handleRunningTaskClear(sessionId: string | undefined): void {
  if (!sessionId) return;
  try {
    const w = window as unknown as {
      __sessionStore?: {
        getState: () => {
          currentSessionId?: string | null;
          setRunningTaskFor?: (sid: string, t: unknown) => void;
        };
      };
    };
    const store = w.__sessionStore?.getState();
    store?.setRunningTaskFor?.(sessionId, null);
    // If the clear is for the currently-active session, also drop the
    // legacy single-task / button state so the composer un-locks.
    if (store?.currentSessionId === sessionId) {
      W.setRunning?.(false);
    }
  } catch {
    /* ignore */
  }
  // One-shot reload requested by the Function-call Retry button: the
  // retried run is a sibling branch whose HEAD lands at run completion,
  // so re-hydrate now — the branch view then renders only the active
  // version and the old run moves behind the < N/M > switcher.
  const flagged = window as Window & { __reloadOnTaskClear?: string | null };
  if (flagged.__reloadOnTaskClear === sessionId) {
    flagged.__reloadOnTaskClear = null;
    const ws = (window as Window & { ws?: WebSocket }).ws;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "load_session", session_id: sessionId }));
    }
  }
}

/* ===== handleChatResponse (bookkeeping) ========================== */

export function handleChatResponse(data: ChatResponseData): void {
  const type = data.type;

  if (type === "context_stats") {
    handleContextStats(data as ContextStatsData);
    return;
  }
  if (type === "status") {
    handleStatusResponse(data as StatusResponseData);
    return;
  }
  if (type === "follow_up_question") {
    handleFollowUpQuestion(data as { question?: string });
    return;
  }
  if (type === "stream_event" || type === "tree_update" || type === "user_message") {
    return;
  }

  // Final response (result / error / retry_result) -- task done.
  // Clear per-session running state from the response's session_id
  // (NOT W.currentSessionId — the user may have switched away while
  // the background turn was finishing). The clear helper itself
  // flips the legacy button if the cleared session is the active one.
  const respSid = (data as { session_id?: string }).session_id;
  handleRunningTaskClear(respSid || W.currentSessionId || undefined);
  W.loadAgentSettings?.();
  if (typeof W.refreshTokenBadge === "function") {
    try {
      W.refreshTokenBadge();
    } catch {
      /* ignore */
    }
  }
  const sid = W.currentSessionId;
  if (sid) {
    try {
      fetchBranches(sid, { force: true }).then(() => {
        try {
          W._refreshBranchTokens?.();
        } catch {
          /* ignore */
        }
      });
    } catch {
      /* ignore */
    }
  }

  if (W._elapsedTimer) {
    clearInterval(W._elapsedTimer);
    W._elapsedTimer = null;
  }

  const isRuntimeResult =
    data.display === "runtime" ||
    (!!data.function && data.function !== "chat");

  // Store assistant message.
  if (sid && W.conversations?.[sid]) {
    const conv = W.conversations[sid] as { messages?: Record<string, unknown>[]; title?: string };
    if (!conv.messages) conv.messages = [];
    const storedMsg: Record<string, unknown> = {
      role: "assistant",
      content: data.content || "",
      type,
      function: data.function || null,
      display: isRuntimeResult ? "runtime" : undefined,
      blocks:
        Array.isArray(data.blocks) && (data.blocks as unknown[]).length
          ? data.blocks
          : undefined,
    };
    if (type === "result" && data.function) {
      storedMsg.attempts = [
        {
          content: data.content || "",
          tree: data.context_tree || null,
          timestamp: Date.now() / 1000,
        },
      ];
      storedMsg.current_attempt = 0;
    }
    // Runtime (function-call) results are owned by the React message
    // store: the chat-stream reducer writes the single runtime row that
    // <MessageList /> renders as one Function-call card, and reload
    // rebuilds it from the persisted execution tree. Pushing a second
    // assistant row here would re-enter `convToChatMsgs` (on the next
    // `__feedStoreFromConv`) as a duplicate `display:"runtime"` row,
    // producing a second card. Only plain chat replies belong in the
    // legacy `conv.messages` mirror.
    if (!isRuntimeResult) conv.messages.push(storedMsg);
    (W.updateContextStats as ((m: unknown[]) => void) | undefined)?.(conv.messages);

    // Conversation title.
    if (!conv.title || conv.title === "New conversation") {
      const msgs = conv.messages;
      if (msgs.length > 0) {
        conv.title = String((msgs[0].content as string) || "").slice(0, 50);
        W.renderSessions?.();
        W.refreshStatusSource?.();
      }
    }
  }
}

/* ===== context_stats ============================================= */

interface ContextStatsData {
  chat?: { input_tokens?: number; output_tokens?: number; cache_read?: number; cache_write?: number };
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_write_tokens?: number;
  context_window?: number;
  current_tokens?: number;
  naive_sum?: number;
  cache_hit_rate?: number;
  cache_read_total?: number;
  last_assistant_usage?: number;
  last_assistant_input?: number;
  last_assistant_cache_read?: number;
  last_turn_hit_rate?: number;
  input_total?: number;
  model?: string | null;
  source_mix?: unknown;
}

function handleContextStats(data: ContextStatsData): void {
  let chat = data.chat || {};
  if (!data.chat && (data.input_tokens || data.output_tokens)) {
    chat = {
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      cache_read: data.cache_read || 0,
    };
  }
  const sid = W.currentSessionId;

  const cacheWrite = chat.cache_write || data.cache_write_tokens || 0;
  if (cacheWrite > 0 && sid) W._recordCacheWrite?.(sid);

  if (W.__sessionStore && sid) {
    try {
      W.__sessionStore.getState().setContextStats(
        sid,
        {
          input: chat.input_tokens || 0,
          output: chat.output_tokens || 0,
          cache_read: chat.cache_read || 0,
          cache_create: cacheWrite,
          model: data.model || null,
          provider: (data as unknown as {provider?: string}).provider || null,
        },
        data.context_window || null,
      );
    } catch {
      /* store not ready — a later stats event lands */
    }
  }

  if (typeof W._renderTokenBadge === "function" && sid) {
    W._renderTokenBadge(
      {
        current_tokens:
          data.current_tokens ||
          (chat.input_tokens || 0) + (chat.output_tokens || 0),
        naive_sum: data.naive_sum || 0,
        context_window: data.context_window || 0,
        cache_hit_rate: data.cache_hit_rate || 0,
        cache_read_total: data.cache_read_total || chat.cache_read || 0,
        last_assistant_usage: data.last_assistant_usage || 0,
        last_assistant_input: data.last_assistant_input || 0,
        last_assistant_cache_read: data.last_assistant_cache_read || 0,
        last_turn_hit_rate: data.last_turn_hit_rate || 0,
        input_total: data.input_total || 0,
        model: data.model || null,
        source_mix: data.source_mix || null,
      },
      sid,
    );
  }

  if (sid) W.refreshHistoryContextRange?.(sid);
}

/* ===== status response =========================================== */

interface StatusResponseData {
  context_tree?: { path?: string; name?: string };
}

function handleStatusResponse(data: StatusResponseData): void {
  if (data.context_tree) {
    const ct = data.context_tree;
    const rootKey = ct.path || ct.name;
    const trees = W.trees || (W.trees = []);
    const idx = trees.findIndex((t) => t.path === rootKey || t.name === ct.name);
    if (idx >= 0) trees[idx] = ct;
    else trees.push(ct);
    const sid = W.currentSessionId;
    if (sid && W.conversations?.[sid]) {
      const conv = W.conversations[sid] as { messages?: unknown[] };
      conv.messages = extractMessagesFromTree(ct as never);
      renderSessionMessages(W.conversations[sid] as never);
    }
  }
  W.scrollToBottom?.();
}

/* ===== follow-up question ======================================== */

function handleFollowUpQuestion(data: { question?: string }): void {
  const pendingBlock = document.getElementById("runtime_pending");
  if (!pendingBlock) return;
  const contentArea =
    pendingBlock.querySelector(".runtime-block-content") ||
    pendingBlock.querySelector(".runtime-block-body");
  if (!contentArea) return;

  const existing = contentArea.querySelector(".follow-up-container");
  if (existing) existing.remove();

  const esc = W.escHtml || ((s: unknown) => String(s));
  const fuHtml =
    '<div class="follow-up-container" style="margin:12px 0;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">' +
    '<div style="color:var(--accent-yellow);font-weight:600;margin-bottom:8px">&#9888; Follow-up Question</div>' +
    '<div style="margin-bottom:10px;color:var(--text-primary)">' +
    esc(data.question) +
    "</div>" +
    '<div style="display:flex;gap:8px">' +
    '<input type="text" id="followUpInput" placeholder="Type your answer..." ' +
    'style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);color:var(--text-primary);font-size:14px" ' +
    "onkeydown=\"if(event.key==='Enter')submitFollowUp()\">" +
    '<button onclick="submitFollowUp()" ' +
    'style="padding:8px 16px;border:none;border-radius:6px;background:var(--accent-blue);color:white;cursor:pointer;font-size:14px">Submit</button>' +
    "</div>" +
    "</div>";
  contentArea.insertAdjacentHTML("beforeend", fuHtml);
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (inp) inp.focus();
  W.scrollToBottom?.();
}

/* ===== follow-up submit ========================================== */

export function submitFollowUp(): void {
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (!inp) return;
  const answer = inp.value.trim();
  if (!answer) return;
  const container = inp.closest(".follow-up-container");
  if (container) container.remove();
  if (W.ws && W.ws.readyState === 1) {
    W.ws.send(
      JSON.stringify({
        action: "follow_up_answer",
        session_id: W.currentSessionId,
        answer,
      }),
    );
  }
}

/* ===== retry / pause-retry ======================================= */

// Per-node retry is the React <ExecutionTree /> retry panel now; the
// legacy ui.js node-detail panel still emits an onclick to this stub.
export function rerunFromNode(): void {}

/* ===== assistant message (programs-panel toast) ================== */

export function addAssistantMessage(text: string): void {
  W.setWelcomeVisible?.(false);
  // The legacy bubble DOM is dropped (React owns the stream); this is
  // kept only so programs-panel.js's delete-function toast doesn't
  // throw. A real React toast can replace it later.
  void text;
}

/* ===== page init ================================================= */

export function initChatPage(): void {
  // Re-derive currentSessionId from the URL on every chat-page mount.
  const m = window.location.pathname.match(/^\/s\/([^/]+)/);
  W.currentSessionId = m ? m[1] : null;

  W.loadProviders?.();
  if (!window.location.pathname.match(/^\/s\//)) {
    W.setWelcomeVisible?.(true);
  }

  // Rehydrate the tools chip flags from localStorage.
  try {
    if (localStorage.getItem("agentic_tools_enabled") === "1") {
      W._toolsEnabled = true;
    }
    if (localStorage.getItem("agentic_web_search_enabled") === "1") {
      W._webSearchEnabled = true;
    }
  } catch {
    /* ignore */
  }
  W._updatePlusBtnIndicator?.();
  W._refreshWebSearchProviderLabel?.();
}

// beforeunload — persist scroll position. Installed once.
window.addEventListener("beforeunload", () => {
  const area = document.getElementById("chatArea");
  if (area) sessionStorage.setItem("agentic_scroll", String(area.scrollTop));
});

/* ===== window bridges ============================================ */

W.setRunActive = setRunActive;
W._wsHandleChatAck = wsHandleChatAck;
W._wsHandleChatResponse = wsHandleChatResponse;
W._wsHandleStatus = wsHandleStatus;
W._handleSessionsList = handleSessionsList;
W._handleRunningTask = handleRunningTask;
W.handleChatResponse = handleChatResponse;
W.submitFollowUp = submitFollowUp;
W.rerunFromNode = rerunFromNode;
W.addAssistantMessage = addAssistantMessage;
