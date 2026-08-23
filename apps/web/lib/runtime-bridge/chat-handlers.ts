/**
 * Chat-page WebSocket handlers.
 *
 * TS port of the legacy `public/js/chat/{init,chat-ws,chat}.js`. These
 * are the WS message handlers (`chat_ack` / `chat_response` / `status`
 * / `sessions_list` / `running_task`) plus the retry / follow-up glue.
 * `useWS` calls the exported functions directly.
 *
 * Imported for side effects + `initChatPage()` by `useWS`.
 */

import {
  extractMessagesFromTree,
  fetchBranches,
  newSession,
  refreshBranchBadge,
  refreshChannelBadge,
  refreshBranchTokens,
} from "./conversations";
import {
  mirrorSetConvs,
  mirrorUpsertConv,
} from "./conv-store-mirror";
import {
  responseSessionId,
  responseTargetsActiveChat,
} from "./chat-response-routing";
import {
  draftChannelChoiceHost,
  dropDraftChannelChoice,
} from "./draft-channel-choice";
import { runtimeState, getSocket } from "./state";
import { escHtml, scrollToBottom, setWelcomeVisible } from "./helpers";
import {
  setRunning,
  updateContextStats,
  updatePauseBtn,
  updatePlusBtnIndicator,
  refreshStatusSource,
  refreshWebSearchProviderLabel,
} from "./ui";
import {
  loadAgentSettings,
  loadProviders,
  recordCacheWrite,
  refreshTokenBadge,
  renderTokenBadge,
} from "./providers";
import { refreshHistoryContextRange } from "./dag";
import { sessionAckIsActive, useCenterTabs } from "@/lib/state/center-tabs-store";
import { writeChatScroll } from "@/lib/state/chat-scroll";
import { useSessionStore } from "@/lib/session-store";
import {
  warmContextBreakdown,
  writeContextBreakdownCache,
} from "@/lib/state/context-breakdown-cache";
import { convToChatMsgs } from "@/lib/conv-mapper";
import {
  clearPendingFirstAck,
  clearPendingUserText,
  getPendingUserText,
} from "@/lib/pending-user-text";
import { shouldHydrateTranscriptForTreeUpdate } from "./transcript-hydration";
import {
  shouldHonorRunningTaskClear,
  type ClearedTaskIdentity,
} from "@/lib/state/running-task-clear";
import { translateText } from "@/lib/i18n";

/** The app's single draft-channel-choice host (module-level, backed by
 *  `runtimeState._pendingChannelChoice`). */
const choiceHost = draftChannelChoiceHost;

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
  execution_id?: string;
  /** Effective permission mode the backend adopted for this turn. */
  permission_mode?: string;
  /** Set by a function dispatch (retry_function) whose top-level code node
   *  was pre-created on disk at dispatch time — lets us hydrate the
   *  transcript immediately instead of waiting for the first tree_update. */
  function_run?: boolean;
}

export function wsHandleChatAck(data: ChatAckData): void {
  if (data.session_id) {
    const sid = data.session_id;
    const tabs = useCenterTabs.getState();
    const isActive = sessionAckIsActive(sid);
    tabs.markSessionReady(sid);
    dropDraftChannelChoice(choiceHost, sid, isActive);
    const pendingProjectId =
      useSessionStore.getState().pendingProjectsByChat[sid];
    const sock = getSocket();
    if (
      pendingProjectId &&
      sock &&
      sock.readyState === WebSocket.OPEN
    ) {
      try {
        sock.send(
          JSON.stringify({
            action: "set_session_project",
            session_id: sid,
            project_id: pendingProjectId,
          }),
        );
        useSessionStore.getState().takePendingProject(sid);
        window.dispatchEvent(new Event("project-changed"));
      } catch {
        // Keep the pending entry when the socket closes between the
        // readyState check and send so the project choice is not lost.
      }
    }
    if (isActive) {
      runtimeState.currentSessionId = sid;
      if (window.location.pathname !== "/s/" + sid) {
        history.pushState(null, "", "/s/" + sid);
      }
    }
    if (typeof data.permission_mode === "string" && data.permission_mode) {
      useSessionStore.getState().setComposerSettings(
        { effective_permission: data.permission_mode },
        sid,
      );
    }
    const convs = runtimeState.conversations;
    if (!convs[sid]) {
      // Seed a preview + created_at from the just-sent user text so the
      // row shows in the sidebar IMMEDIATELY (on run start), not after
      // the turn finishes. Without a preview, isEmptyPlaceholder() filters
      // a "New conversation"-titled row out until the backend re-lists it
      // with a preview at turn end — which read as "the chat only appears
      // after it's done".
      const pending = getPendingUserText(sid) || "";
      const preview = pending.trim().replace(/\s+/g, " ").slice(0, 80);
      convs[sid] = {
        id: sid,
        title: "New conversation",
        messages: [],
        preview: preview || null,
        created_at: Date.now() / 1000,
      };
    }
    clearPendingUserText(sid);
    clearPendingFirstAck(sid);
    // Mirror the (seeded or pre-existing) conv into the React store so the
    // sidebar row appears IMMEDIATELY — store.conversations is the
    // sidebar's source of truth.
    mirrorUpsertConv(convs[sid]);
    // Light the row's running animation (convRunningFlow) on THIS tab
    // immediately — keyed on the real sid the server just assigned, so
    // it's idempotent with the incoming running_task broadcast (which
    // overwrites the same key with a richer payload). Without this the
    // sending tab's row appears but doesn't flow until that round-trip.
    useSessionStore
      .getState()
      .setRunningTaskFor(sid, {
        session_id: sid,
        msg_id: data.msg_id || "",
        execution_id: data.execution_id
          || (data.msg_id ? `${data.msg_id}_reply` : undefined),
      });
    if (isActive) {
      void loadAgentSettings();
      refreshChannelBadge();
    }
    // A fresh session never went through `load_session`, so fetch the
    // branch list now that the server registered the user turn.
    delete runtimeState._branchesByConv[sid];
    fetchBranches(sid).then(() => {
      if (isActive) refreshBranchBadge();
    });
    if (isActive) setRunActive(true);
  }

  // Function dispatch (Retry): the top-level code node is already on disk
  // (pre-created at dispatch time), so hydrate the transcript NOW rather
  // than waiting ~1.85s for the spawned child's first tree_update. The
  // tree_update path (hydrateTranscriptForTreeUpdate) stays as the fallback
  // and is a no-op once this load_session lands the card. Guarded on
  // function_run so a plain chat ack is untouched.
  if (data.function_run && data.session_id === runtimeState.currentSessionId) {
    runtimeState.__reloadOnTaskClear = data.session_id;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(
        JSON.stringify({ action: "load_session", session_id: data.session_id }),
      );
    }
  }
}

interface ChatResponseData {
  type?: string;
  session_id?: string;
  [k: string]: unknown;
}

export function wsHandleChatResponse(data: ChatResponseData): void {
  // `run_active` is a REJECTION of a turn that never started, not the end
  // of one. The chat-stream reducer re-queues the text; this side must
  // stay out of it entirely — falling through would clear the running
  // task of the run that is still going and append an assistant row for
  // a reply that will never exist.
  if (data && data.type === "error"
      && (data as { code?: unknown }).code === "run_active") return;
  // Cancelled envelope without a msg_id is the force-stop signal.
  if (data && data.type === "cancelled") {
    const sid = responseSessionId(data, runtimeState.currentSessionId);
    const honored = handleRunningTaskClear(sid ?? undefined, {
      execution_id: typeof data.execution_id === "string" ? data.execution_id : undefined,
      msg_id: typeof data.msg_id === "string" ? data.msg_id : undefined,
    });
    if (!honored) return;
    if (!responseTargetsActiveChat(data, runtimeState.currentSessionId)) return;
    try {
      const rp = document.getElementById("runtime_pending");
      if (rp && rp.parentNode) rp.parentNode.removeChild(rp);
    } catch {
      /* ignore */
    }
    Object.keys(runtimeState.pendingResponses).forEach((k) => {
      delete runtimeState.pendingResponses[k];
    });
    setRunActive(false);
    setRunning(false);
    return;
  }
  handleChatResponse(data);
  if (data && (data.type === "result" || data.type === "error")) {
    if (responseTargetsActiveChat(data, runtimeState.currentSessionId)) {
      setRunActive(false);
    }
  }
}

interface StatusMsg {
  paused?: boolean;
  stopped?: boolean;
}

export function wsHandleStatus(msg: StatusMsg): void {
  runtimeState.isPaused = !!msg.paused;
  if (msg.stopped) {
    runtimeState.isRunning = false;
    if (runtimeState._elapsedTimer) {
      clearInterval(runtimeState._elapsedTimer);
      runtimeState._elapsedTimer = null;
    }
  }
  updatePauseBtn();
}

/* ===== sessions_list / running_task ============================== */

interface SessionRow {
  id: string;
  title?: string;
  created_at?: number;
  /** 最后活跃时间（服务端每次列表都带上，权威值总是覆盖）。 */
  updated_at?: number;
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

/** 上一条 sessions_list 消息里的会话 id 集合（首条到达前为 null）。 */
let prevServerListedIds: Set<string> | null = null;

export function handleSessionsList(data: SessionRow[]): void {
  const convs = runtimeState.conversations;
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
          updated_at: c.updated_at,
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
        // updated_at 是活跃度，服务端每次都是最新值——总是覆盖。
        if (c.updated_at != null) convs[c.id].updated_at = c.updated_at;
        if (c.title && !convs[c.id].title) convs[c.id].title = c.title;
      }
    }
  }
  // Replace the React store's summary map from the freshly-synced legacy
  // map (handles adds / deletes / field updates in one pass). The sidebar
  // reads store.conversations, so this is what makes the list authoritative.
  mirrorSetConvs(Object.values(convs));
  const sid = runtimeState.currentSessionId;
  // 当前会话不在列表 → 只有"上一次服务器列表里有、这次没了"才是真删除。
  // 冷加载 /s/<id> 深链时 list_sessions 先于 load_session 到达，而列表只
  // 反映内存注册表——磁盘上存在但尚未补水的会话天然缺席，此时弹回 /chat
  // 会把合法深链吞掉（load_session 随后就会把它补进注册表）。
  if (sid && !convs[sid] && prevServerListedIds?.has(sid)) {
    newSession();
  }
  prevServerListedIds = serverIds;
  if (sid && convs[sid]) {
    runtimeState._hasActiveSession = true;
    const provBadge = document.getElementById("providerBadge");
    if (provBadge && provBadge.textContent!.indexOf("\u{1F512}") === -1) {
      provBadge.textContent += " \u{1F512}";
    }
    void loadProviders();
  }
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
  const conv = runtimeState.conversations[data.id];
  if (!conv) return;
  if (typeof data.title === "string") conv.title = data.title;
  if ("pinned" in data) conv.pinned = !!data.pinned;
  if ("archived" in data) conv.archived = !!data.archived;
  if ("group" in data) conv.group = data.group || "";
  if ("status" in data) conv.status = data.status || undefined;
  if ("unread" in data) conv.unread = !!data.unread;
  mirrorUpsertConv(conv);
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
    execution_id?: string;
  };

  // 先做 cancelled 守卫，再碰任何 occupancy。旧代码先 setRunning(true)
  //（它会写入 {msg_id:""} 的占位 task）再检查取消，迟到的 running_task
  // 既复活了槽位、又把已有 task 的 execution_id 冲掉——之后停止键发不出
  // execution.cancel（turn-occupancy.md：迟到帧不得复活）。
  const sid = t.session_id;
  const mid = t.msg_id;
  const store = useSessionStore.getState();
  const replyId = mid ? mid + "_reply" : "";
  const executionId = t.execution_id || replyId;
  const targetId = executionId && store.messagesById[executionId]
    ? executionId
    : replyId && store.messagesById[replyId]
      ? replyId
      : mid || "";
  const current = targetId ? store.messagesById[targetId] : undefined;
  // Stop already marked this turn cancelled and released occupancy.
  // A late running_task for the same msg_id must not revive the slot.
  if (current?.status === "cancelling" || current?.status === "cancelled") {
    return;
  }

  // 1) Flip the composer's send/stop button immediately — but only
  //    if this event targets the currently-active session. Without
  //    this guard, a background session starting a turn would also
  //    flip the composer for whatever other session the user is
  //    looking at right now.
  if (!sid || sid === runtimeState.currentSessionId) {
    setRunning(true);
  }

  // 2) Mark the in-flight assistant message as "running" in the
  //    React store so its bubble shows the waiting indicator. The
  //    backend has already persisted the assistant placeholder + any
  //    tool rows that fired before the refresh; the WS load gave us
  //    the message but with status="done" (placeholder content is
  //    empty). Without this patch, the chat looked finished even
  //    though the turn was still running server-side.
  if (!sid || !mid) return;
  store.updateMessage(sid, targetId, { status: "running" });
  store.setRunningTaskFor(sid, {
    session_id: sid,
    msg_id: mid,
    func_name: t.func_name,
    started_at: t.started_at,
    execution_id: executionId,
  });
}

/** Hydrate the transcript when a function run's FIRST tree_update lands.
 *
 *  Runs dispatched via POST /api/function (fn-form, welcome button,
 *  retry) stream NO transcript placeholder — the code node is persisted
 *  server-side only, so a user watching the session sees either a blank
 *  transcript (fresh run) or the stale previous version (retry) for the
 *  whole run. ``running_task`` fires BEFORE the code node exists, so
 *  hydrating on it races and loads the old branch; the first
 *  ``tree_update`` carries the code node's own id in ``tree.path`` and
 *  proves it is persisted (and HEAD has moved to it at append). Hydrate
 *  once per run: the pending card appears — for a retry, replacing the
 *  old version — and later tree_updates fill it live. One more hydrate
 *  fires on completion (running_task_clear) for the final result state.
 */
const hydratedTreePaths = new Set<string>();

/** Forget which runs were already hydrated. Called on every
 *  `session_loaded` (use-ws.ts): a fresh transcript is the natural drain
 *  point — every tree-structure change (session switch, branch checkout,
 *  rewind, attach/merge `session_reload`) funnels into a `load_session`
 *  whose reply is that frame. A card the reload delivered is still
 *  deduped by the `messagesById` guard below; a path genuinely missing
 *  again after the tree changed must be allowed to re-hydrate. Also
 *  keeps the set from growing without bound across sessions. */
export function clearHydratedTreePaths(): void {
  hydratedTreePaths.clear();
}

function hydrateTranscriptForTreeUpdate(data: ChatResponseData): void {
  const sid = (data as { session_id?: string }).session_id;
  const path = ((data as { tree?: { path?: string } }).tree || {}).path;
  if (!sid || !path) return;
  const store = useSessionStore.getState();
  if (
    !shouldHydrateTranscriptForTreeUpdate({
      currentSessionId: runtimeState.currentSessionId,
      sessionId: sid,
      path,
      messagesById: store.messagesById,
      messageOrder: store.messageOrder,
      hydratedPaths: hydratedTreePaths,
    })
  ) {
    return;
  }
  runtimeState.__reloadOnTaskClear = sid;
  const sock = getSocket();
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(JSON.stringify({ action: "load_session", session_id: sid }));
  }
}

export function handleRunningTaskClear(
  sessionId: string | undefined,
  cleared?: ClearedTaskIdentity & { force?: boolean },
): boolean {
  if (!sessionId) return false;
  const store = useSessionStore.getState();
  const current = store.runningTasks[sessionId];
  if (!cleared?.force && !shouldHonorRunningTaskClear(current, cleared)) {
    return false;
  }
  store.setRunningTaskFor(sessionId, null, "always");
  warmContextBreakdown(sessionId, store.heads[sessionId] ?? null);
  // If the clear is for the currently-active session, also drop the
  // legacy single-task / button state so the composer un-locks.
  if ((store.activeChatKey ?? store.currentSessionId) === sessionId) {
    setRunning(false);
  }
  // One-shot reload requested by the Function-call Retry button: the
  // retried run is a sibling branch whose HEAD lands at run completion,
  // so re-hydrate now — the branch view then renders only the active
  // version and the old run moves behind the < N/M > switcher.
  if (runtimeState.__reloadOnTaskClear === sessionId) {
    runtimeState.__reloadOnTaskClear = null;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "load_session", session_id: sessionId }));
    }
  }
  return true;
}

/* ===== handleChatResponse (bookkeeping) ========================== */

export function handleChatResponse(data: ChatResponseData): void {
  const type = data.type;
  const sid = responseSessionId(data, runtimeState.currentSessionId);
  const targetsActive = responseTargetsActiveChat(
    data,
    runtimeState.currentSessionId,
  );

  if (type === "context_stats") {
    handleContextStats(data as ContextStatsData, sid);
    return;
  }
  // Compaction / snip frames can arrive mid-turn. They must not fall
  // through to the final-reply branch (that clears running + pending).
  if (type === "compaction_started") {
    if (sid) {
      useSessionStore.getState().setCompactionUi(sid, {
        running: true, recommended: false,
      });
      useSessionStore.getState().appendMessage(sid, {
        id: "compaction_started_" + Date.now().toString(36),
        role: "system",
        content: translateText("Compacting context…", "正在压缩上下文…"),
        status: "done",
      });
      if (targetsActive) scrollToBottom();
    }
    return;
  }
  if (type === "compaction_finished") {
    // 压缩把保留尾部重挂到摘要节点下，活跃分支的节点集合整个换了一批。
    // Context tab 的明暗靠 /context-range 的 node_ids，不刷新就停在压缩前
    // 的旧集合上 → 整图全暗。
    if (targetsActive && sid) refreshHistoryContextRange(sid);
    if (sid) {
      useSessionStore.getState().setCompactionUi(sid, {
        running: false, recommended: false,
      });
      const noOp = Boolean(data.no_op);
      const before = data.tokens_before;
      const after = data.tokens_after;
      const n = Number(data.summarised_count);
      const content = noOp
        ? translateText(
            "Context is already compacted; no older messages to fold.",
            "当前上下文已是压缩后状态，没有可压缩的旧消息",
          )
        : data.summarised_count != null && Number.isFinite(n)
          ? translateText(
              `Context compacted: covered ${n} older messages, ${before} → ${after} tokens`,
              `上下文已压缩：盖住 ${n} 条旧消息，${before} → ${after} tokens`,
            )
          : translateText(
              `Context compacted: ${before} → ${after} tokens`,
              `上下文已压缩：${before} → ${after} tokens`,
            );
      useSessionStore.getState().appendMessage(sid, {
        id: "compaction_finished_" + Date.now().toString(36),
        role: "system",
        content,
        status: "done",
      });
      if (targetsActive) scrollToBottom();
    }
    return;
  }
  if (type === "compaction_failed") {
    if (sid) {
      useSessionStore.getState().setCompactionUi(sid, { running: false });
      const err = String((data as { error?: unknown }).error ?? "");
      useSessionStore.getState().appendMessage(sid, {
        id: "compaction_failed_" + Date.now().toString(36),
        role: "system",
        content: err
          ? translateText("Context compaction failed: ", "上下文压缩失败：") + err
          : translateText("Context compaction failed", "上下文压缩失败"),
        status: "done",
      });
      if (targetsActive) scrollToBottom();
    }
    return;
  }
  if (type === "compaction_recommended") {
    if (sid) {
      useSessionStore.getState().setCompactionUi(sid, { recommended: true });
    }
    return;
  }
  if (type === "snip") {
    return;
  }
  if (type === "status") {
    handleStatusResponse(data as StatusResponseData, sid, targetsActive);
    return;
  }
  if (type === "local_command") {
    // Backend-executed builtin (/goal status / clear …) — show its reply
    // as a transient system row in the transcript. Not persisted
    // server-side (same as a REPL console print), so it drops on reload.
    const content = String((data as { content?: unknown }).content ?? "");
    const store = useSessionStore.getState();
    const targetsActiveLocal = targetsActive || (!!sid && store.activeChatKey === sid);
    if (sid) {
      clearPendingUserText(sid);
      clearPendingFirstAck(sid);
      handleRunningTaskClear(sid, { force: true });
      if (targetsActiveLocal) setRunActive(false);
    }
    if (sid && content) {
      store.appendMessage(sid, {
        id: "local_cmd_" + Date.now().toString(36),
        role: "system",
        content,
        status: "done",
      });
      if (targetsActiveLocal) scrollToBottom();
    }
    return;
  }
  if (type === "follow_up_question") {
    if (targetsActive) handleFollowUpQuestion(data as { question?: string });
    return;
  }
  if (type === "stream_event" || type === "tree_update" || type === "user_message") {
    if (type === "tree_update" && targetsActive) hydrateTranscriptForTreeUpdate(data);
    return;
  }

  // Final response (result / error / retry_result) -- task done.
  // Clear per-session running state from the response's session_id
  // (NOT runtimeState.currentSessionId — the user may have switched away
  // while the background turn was finishing). The clear helper itself
  // flips the legacy button if the cleared session is the active one.
  //
  // 例外：聊天回合中途完成的内联 @agentic_function / spawn 结果也走
  // display:"runtime" 的 result/error 帧（_execute/chat.py 即时转发），
  // 它结束的是子卡片、不是回合本身——误清 occupancy 会让停止键在流式
  // 输出中途消失（正在推理却像已停止）。frame 的 msg_id 是子块自己的
  // id，与 runningTask 的回合 msg_id 对不上时，跳过清理。
  const runningTask = sid
    ? useSessionStore.getState().runningTasks[sid]
    : undefined;
  const nestedRuntimeResult =
    data.display === "runtime"
    && !!runningTask?.msg_id
    && !!data.msg_id
    && data.msg_id !== runningTask.msg_id;
  if (!nestedRuntimeResult) {
    handleRunningTaskClear(sid ?? undefined, {
      execution_id: typeof data.execution_id === "string" ? data.execution_id : undefined,
      msg_id: typeof data.msg_id === "string" ? data.msg_id : undefined,
    });
  }
  if (targetsActive) {
    void loadAgentSettings();
    void refreshTokenBadge();
    if (sid) {
      fetchBranches(sid, { force: true }).then(() => refreshBranchTokens());
    }

    if (runtimeState._elapsedTimer) {
      clearInterval(runtimeState._elapsedTimer);
      runtimeState._elapsedTimer = null;
    }
  }

  const isRuntimeResult =
    data.display === "runtime" ||
    (!!data.function && data.function !== "chat");

  // Store the assistant reply — in the SESSION STORE, the transcript's
  // single source. The legacy ``conv.messages`` mirror takes no
  // incremental writes any more: it is a one-shot load_session
  // snapshot, nothing else (the dual-bookkeeping era ended with the
  // retry-race bug it caused).
  if (sid && runtimeState.conversations[sid]) {
    const conv = runtimeState.conversations[sid] as { title?: string };
    // Self-heal after a fork: retry/edit moved the branch, and this
    // result may have raced the load_session that repaints the view.
    // Writing the reply into the old branch's view would stack the
    // two dialogues — reload wholesale instead.
    if (runtimeState._pendingBranchReload[sid]) {
      delete runtimeState._pendingBranchReload[sid];
      const sock = getSocket();
      if (sock && sock.readyState === WebSocket.OPEN) {
        sock.send(JSON.stringify({ action: "load_session", session_id: sid }));
      }
      return;
    }
    // Runtime (function-call) results stay store-owned via the
    // chat-stream reducer / tree hydration — writing a second row here
    // would double the Function-call card. Plain replies upsert: the
    // focused session already streamed the row (patch it final), a
    // background session gets it appended so its transcript is whole
    // without a reload.
    if (!isRuntimeResult) {
      const st = useSessionStore.getState();
      // The result envelope's msg_id is the USER turn's id; the reply
      // row is minted as ``<user_msg_id>_reply`` everywhere (dispatcher,
      // stream reducer, DAG). Resolve to the reply row — patching the
      // raw msg_id would overwrite the user's own bubble.
      const ridRaw = String((data as { msg_id?: unknown }).msg_id || "");
      const rid = !ridRaw
        ? ""
        : ridRaw.endsWith("_reply") ? ridRaw : ridRaw + "_reply";
      const content = data.content || "";
      const blocks =
        Array.isArray(data.blocks) && (data.blocks as unknown[]).length
          ? data.blocks
          : undefined;
      if (rid && st.messagesById[rid]) {
        st.updateMessage(sid, rid, { content, blocks } as never);
      } else if (rid) {
        st.appendMessage(sid, {
          id: rid, role: "assistant", content, blocks, status: "done",
        } as never);
      }
    }
    if (targetsActive) updateContextStats();

    // Conversation title — sidebar metadata, not transcript state.
    if (!conv.title || conv.title === "New conversation") {
      const st = useSessionStore.getState();
      const firstId = (st.messageOrder[sid] ?? [])[0];
      const seed = firstId
        ? String(st.messagesById[firstId]?.content || "")
        : String(data.content || "");
      if (seed) {
        conv.title = seed.slice(0, 50);
        if (targetsActive) refreshStatusSource();
      }
    }
  }
}

/* ===== context_stats ============================================= */

interface ContextStatsData {
  chat?: { input_tokens?: number; output_tokens?: number; cache_read?: number; cache_write?: number; context_tokens?: number };
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_write_tokens?: number;
  context_window?: number;
  /** Server-computed occupancy — the single number the ring AND the
   *  /context panel both render. `basis` says where it came from:
   *  "measured" (a request just reported its real prompt size) or
   *  "estimated" (the graph moved since, so it was recomputed). */
  window?: number;
  total_used?: number;
  basis?: string;
  estimated?: number;
  calibration?: number;
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
  breakdown?: import("@/lib/state/context-breakdown-cache").ContextBreakdown & {
    head_id?: string | null;
  };
}

function handleContextStats(data: ContextStatsData, sid: string | null): void {
  let chat = data.chat || {};
  if (!data.chat && (data.input_tokens || data.output_tokens)) {
    chat = {
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      cache_read: data.cache_read || 0,
    };
  }
  const cacheWrite = chat.cache_write || data.cache_write_tokens || 0;
  if (cacheWrite > 0 && sid) recordCacheWrite(sid);

  if (sid) {
    // A graph-change refresh (compaction / model switch / branch move)
    // carries occupancy only — no `chat` block, since no request ran.
    // Keep the previous per-call numbers in that case instead of zeroing
    // them, and always take total_used/basis from the server.
    const prev = useSessionStore.getState().tokens[sid];
    const hasCall = !!data.chat || !!data.input_tokens || !!data.output_tokens;
    useSessionStore.getState().setContextStats(
      sid,
      {
        input: hasCall ? chat.input_tokens || 0 : prev?.input,
        output: hasCall ? chat.output_tokens || 0 : prev?.output,
        cache_read: hasCall ? chat.cache_read || 0 : prev?.cache_read,
        cache_create: hasCall ? cacheWrite : prev?.cache_create,
        context: hasCall ? chat.context_tokens || 0 : prev?.context,
        total_used: data.total_used,
        basis: data.basis || null,
        model: data.model || prev?.model || null,
        provider:
          (data as unknown as { provider?: string }).provider ||
          prev?.provider ||
          null,
      },
      data.window || data.context_window || null,
    );

    const headId =
      (data.breakdown && "head_id" in data.breakdown
        ? data.breakdown.head_id
        : undefined) ?? useSessionStore.getState().heads[sid] ?? null;
    if (data.breakdown) {
      writeContextBreakdownCache(sid, headId, data.breakdown);
    }
    // Occupancy just moved (turn done / graph change). Keep the panel
    // snapshot in lockstep — cache hit is not a reason to skip.
    warmContextBreakdown(sid, headId);
  }

  const targetsActive = sid !== null && sid === runtimeState.currentSessionId;
  if (targetsActive) {
    renderTokenBadge(
      {
        current_tokens:
          data.current_tokens ||
          (chat.input_tokens || 0) + (chat.output_tokens || 0),
        naive_sum: data.naive_sum || 0,
        context_window: data.context_window || 0,
        cache_hit_rate: data.cache_hit_rate || 0,
        cache_read_total: data.cache_read_total || chat.cache_read || 0,
        last_assistant_usage: data.last_assistant_usage || 0,
        last_assistant_cache_read: data.last_assistant_cache_read || 0,
        last_turn_hit_rate: data.last_turn_hit_rate || 0,
        model: data.model || null,
        source_mix: (data.source_mix as Record<string, unknown>) || null,
      },
      sid,
    );
    refreshHistoryContextRange(sid);
  }
}

/* ===== status response =========================================== */

interface StatusResponseData {
  context_tree?: { path?: string; name?: string };
}

function handleStatusResponse(
  data: StatusResponseData,
  sid: string | null,
  targetsActive: boolean,
): void {
  if (!targetsActive) return;
  if (data.context_tree) {
    const ct = data.context_tree;
    const rootKey = ct.path || ct.name;
    const trees = runtimeState.trees;
    const idx = trees.findIndex((t) => t.path === rootKey || t.name === ct.name);
    if (idx >= 0) trees[idx] = ct;
    else trees.push(ct);
    if (sid && runtimeState.conversations[sid]) {
      // Store-only write: the tree-derived transcript replaces the
      // session's rows in the store; the conv mirror stays untouched
      // (one-shot load snapshot, never incrementally written).
      useSessionStore.getState().setMessages(
        sid,
        convToChatMsgs(extractMessagesFromTree(ct as never) as never[]),
      );
    }
  }
  scrollToBottom();
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

  const esc = escHtml;
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
  scrollToBottom();
}

/* ===== follow-up submit ========================================== */

export function submitFollowUp(): void {
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (!inp) return;
  const answer = inp.value.trim();
  if (!answer) return;
  const container = inp.closest(".follow-up-container");
  if (container) container.remove();
  const sock = getSocket();
  if (sock && sock.readyState === 1) {
    sock.send(
      JSON.stringify({
        action: "follow_up_answer",
        session_id: runtimeState.currentSessionId,
        answer,
      }),
    );
  }
}

/* ===== assistant message (programs-panel toast) ================== */

export function addAssistantMessage(text: string): void {
  setWelcomeVisible(false);
  // The legacy bubble DOM is dropped (React owns the stream); this is
  // kept only so programs-panel.js's delete-function toast doesn't
  // throw. A real React toast can replace it later.
  void text;
}

/* ===== page init ================================================= */

export function initChatPage(): void {
  // Re-derive currentSessionId from the URL on every chat-page mount.
  const m = window.location.pathname.match(/^\/s\/([^/]+)/);
  runtimeState.currentSessionId = m ? m[1] : null;

  void loadProviders();
  if (!window.location.pathname.match(/^\/s\//)) {
    setWelcomeVisible(true);
  }

  // Rehydrate the tools chip flags from localStorage.
  try {
    if (localStorage.getItem("agentic_tools_enabled") === "1") {
      runtimeState._toolsEnabled = true;
    }
    if (localStorage.getItem("agentic_web_search_enabled") === "1") {
      runtimeState._webSearchEnabled = true;
    }
  } catch {
    /* ignore */
  }
  updatePlusBtnIndicator();
  refreshWebSearchProviderLabel();
}

// beforeunload — persist scroll position. Installed once.
window.addEventListener("beforeunload", () => {
  const area = document.getElementById("chatArea");
  const chatKey =
    useSessionStore.getState().activeChatKey ?? runtimeState.currentSessionId;
  if (area && chatKey) {
    writeChatScroll(sessionStorage, chatKey, area.scrollTop);
  }
});

/* ===== inline-handler bridge ===================================== */

// The follow-up prompt is injected as an HTML string carrying
// `onclick="submitFollowUp()"`, so that one name has to resolve off
// `window` at click time. Everything else is a direct import.
Object.assign(window, { submitFollowUp });
