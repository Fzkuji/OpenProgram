import { requestSessionLoad } from "@/lib/runtime-bridge/session/session-load";
import { showToast } from "@/lib/format-utils/toast";
import { settleFunctionRetry } from "../actions/function-retry";
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

import { sendExecutionCancel } from "@/lib/execution/cancel-execution";
import {
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
} from "../actions/draft-channel-choice";
import { runtimeState, getSocket } from "../state";
import { escHtml, setWelcomeVisible } from "../helpers";
import {
  setRunning,
  updateContextStats,
  updatePauseBtn,
  updatePlusBtnIndicator,
  refreshStatusSource,
  refreshWebSearchProviderLabel,
} from "../ui";
import {
  loadAgentSettings,
  recordCacheWrite,
  refreshTokenBadge,
  renderTokenBadge,
} from "../providers";
import { refreshHistoryContextRange } from "../dag";
import { chatAckIsActive, useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { visibleSessionIds } from "@/lib/tabs/navigation/shell-session";
import { selectRow, useSessionStore } from "@/lib/session-store";
import { offSelectedBranch } from "@/lib/chat/run/selected-branch";
import {
  warmContextBreakdown,
  writeContextBreakdownCache,
} from "@/lib/chat/context-breakdown-cache";
import {
  acknowledgePendingUserText,
  clearPendingFirstAck,
  clearPendingUserText,
  getPendingUserMessageId,
  getPendingUserText,
  hasPendingFirstAck,
  hasPendingUserText,
  rejectPendingUserText,
} from "@/lib/chat/send/pending-user-text";
import { frameNamesPendingSend } from "@/lib/net/ws/unacked-sends";
import {
  shouldHonorRunningTaskClear,
  type ClearedTaskIdentity,
} from "@/lib/chat/decisions/running-task-clear";
import { translateText } from "@/lib/i18n";
import { isBeingStopped, isOpen } from "../../chat/transcript/row-status.ts";
import { appendLocalNotice } from "@/lib/chat/transcript/apply-read";
import { stopOverlay } from "@/lib/chat/transcript/apply-transcript-update";
import type { TurnWindow, WireTurn } from "@/lib/chat/history/history-window";
import { applyFirstRead } from "./transcript-updates";

/** The app's single draft-channel-choice host (module-level, backed by
 *  `runtimeState._pendingChannelChoice`). */
const choiceHost = draftChannelChoiceHost;

/* ===== Run-active flag =========================================== */

// `data-run-active` on #chatMessages drives CSS greying-out of
// Edit/Retry while a run is in flight.
export function setRunActive(active: boolean): void {
  if (typeof document === "undefined") return;
  const c = document.getElementById("chatMessages");
  if (c) c.setAttribute("data-run-active", active ? "true" : "false");
}

// The flag is one DOM attribute for whichever chat the shell shows: it
// follows that chat's running task, so switching chats never carries the
// previous chat's run over. Explicit setRunActive calls (a retry before its
// running_task arrives) hold until the shown chat or its task changes.
let runActiveShown: { key: string | null; running: boolean } | null = null;
useSessionStore.subscribe((s) => {
  const key = s.activeChatKey ?? s.currentSessionId ?? null;
  const running = Boolean(key && s.runningTasks[key]);
  if (runActiveShown?.key === key && runActiveShown.running === running) return;
  runActiveShown = { key, running };
  setRunActive(running);
});

/* ===== chat_ack / chat_response / status ========================= */

/** A command on the page's socket; `false` when it is not open. */
function sendOnSocket(payload: unknown): boolean {
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  sock.send(JSON.stringify(payload));
  return true;
}

interface ChatAckData {
  session_id?: string;
  client_request_id?: string;
  msg_id?: string;
  /** The reply row the server writes for this turn. */
  reply_id?: string;
  execution_id?: string;
  status_version?: number;
  /** Effective permission mode the backend adopted for this turn. */
  permission_mode?: string;
  /** Set by a function dispatch (retry_function) whose top-level code node
   *  was pre-created on disk at dispatch time — lets us hydrate the
   *  transcript immediately instead of waiting for the first tree_update. */
  function_run?: boolean;
  retry_request_id?: string;
  /** Set when a Job (sub-agent or a message from another conversation)
   *  accepted its own turn in this session. */
  job_id?: string;
  /** What the view holds, for a socket that held no view of this session. */
  first_read?: { turns: WireTurn[]; window: TurnWindow };
}

export function wsHandleChatAck(data: ChatAckData): void {
  // A Job's turn is not the conversation's: it neither runs the session
  // nor consumes the composer's pending text (see wsHandleChatResponse).
  if (data.job_id) return;
  if (data.session_id && data.first_read) applyFirstRead(data.session_id, data.first_read);
  if (data.session_id && data.function_run) settleFunctionRetry(data.session_id, data.retry_request_id);
  if (data.session_id) {
    const sid = data.session_id;
    const tabs = useCenterTabs.getState();
    const isActive = chatAckIsActive(sid);
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
      // The draft's /chat entry becomes the conversation's URL: one history
      // entry per conversation, so Back never lands on an empty /chat under
      // this tab. A page shown from the tab (Settings…) keeps its own URL.
      const path = window.location.pathname;
      if (path !== "/s/" + sid && (path === "/chat" || path.startsWith("/s/"))) {
        history.replaceState(null, "", "/s/" + sid);
      }
    }
    if (typeof data.permission_mode === "string" && data.permission_mode
      && !useSessionStore.getState().composerSettingsBySession[sid]?.permission_version) {
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
    // Keep the ACK boundary here as well as in the message reducer so a
    // direct handler call cannot clear a composer before backend acceptance.
    // An ack naming another request leaves this client's pending send alone.
    if (frameNamesPendingSend(sid, data)) {
      acknowledgePendingUserText(sid);
      clearPendingUserText(sid);
      clearPendingFirstAck(sid);
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
    const optimisticStop = runtimeState._optimisticStops[sid];
    // A Stop pressed before this ack is sent now that the turn has a name.
    // A stop names its execution; the version (absent from a function
    // run's ack) is only what the page last saw.
    const stoppedOptimistically = Boolean(optimisticStop);
    if (optimisticStop) {
      delete runtimeState._optimisticStops[sid];
      const replyId = data.execution_id || data.reply_id || "";
      // A stop that already named the send is in force on the Worker; only
      // one that could not is sent now, naming the execution.
      const sentByRequest = Boolean(optimisticStop.commandId);
      const commandId = optimisticStop.commandId || crypto.randomUUID();
      const cancels = Boolean(data.execution_id);
      if (replyId) {
        useSessionStore.getState().applyLocalChange(sid, stopOverlay(replyId, cancels ? [{ until_command_rejected: commandId }] : []));
      }
      if (data.execution_id) {
        const version = typeof data.status_version === "number" ? data.status_version : 0;
        runtimeState._optimisticCancels[commandId] = {
          sessionId: sid,
          task: {
            session_id: sid,
            msg_id: data.msg_id || "",
            execution_id: data.execution_id,
            status_version: version,
          },
          messageId: optimisticStop.messageId || replyId || undefined,
          previousMessageStatus: optimisticStop.previousMessageStatus,
        };
        if (!sentByRequest && !sendExecutionCancel(sendOnSocket, data.execution_id, version, commandId)) {
          delete runtimeState._optimisticCancels[commandId];
        }
      }
      if (isActive) setRunning(false);
    } else {
      useSessionStore
        .getState()
        .setRunningTaskFor(sid, {
          session_id: sid,
          msg_id: data.msg_id || "",
          execution_id: data.execution_id,
          status_version: data.status_version,
        });
    }
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
    if (isActive && !stoppedOptimistically) setRunActive(true);
  }
}

interface ChatResponseData {
  type?: string;
  session_id?: string;
  [k: string]: unknown;
}

function frameOffSelection(data: ChatResponseData): boolean {
  const sid = typeof data.session_id === "string" ? data.session_id : null;
  if (!sid) return false;
  const selected = useSessionStore.getState().composerSettingsBySession[sid]?.active_branch_id;
  return offSelectedBranch(data.branch_id, selected);
}

export function wsHandleChatResponse(data: ChatResponseData): void {
  // A sub-agent's frames (a Job working in this session) end the Job's
  // own turn, not the conversation's: no running-state, unread or reply
  // bookkeeping here. chat-stream keeps them for the agent's step row.
  if (data.job_id) return;
  // A turn on a branch the person switched away from runs on; its frames
  // change nothing on the branch shown, whose run state is its own.
  if (frameOffSelection(data)) return;
  // A bound chat's reply did not reach its channel conversation. The turn
  // itself is finished and unaffected; only the user needs to know.
  if (data.type === "channel_delivery_failed") {
    showChannelDeliveryFailure(data);
    return;
  }
  if (data.type === "error" && typeof data.retry_request_id === "string") {
    settleFunctionRetry(data.session_id || "", data.retry_request_id, String(data.content || "Retry failed"));
    return;
  }
  // `run_active` is a REJECTION of a turn that never started, not the end
  // of one. The chat-stream reducer re-queues the text; this side must
  // stay out of it entirely — falling through would clear the running
  // task of the run that is still going and append an assistant row for
  // a reply that will never exist.
  if (data && data.type === "error"
      && (data as { code?: unknown }).code === "run_active") return;
  // An error with no msg_id answers a request, not a turn (a local command
  // that could not run, a malformed chat frame, a failed compaction). No
  // reply row will carry it, so it surfaces as a toast; when it rejects
  // this client's own send, that turn's bubble and run state settle too.
  if (data && data.type === "error" && !data.msg_id) {
    if (settleUnscopedChatError(data)) return;
  }
  // An error that ends a turn before its ack (the frame's msg_id is fresh,
  // no reply stands behind it) leaves only the send's reservation in the
  // running slot; a running turn's slot names its ids and stays.
  if (data && data.type === "error" && typeof data.session_id === "string") {
    clearSendPlaceholder(data.session_id);
  }
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
  }
  updatePauseBtn();
}

type ExecutionCommandFrame = {
  command?: {
    command_id?: unknown;
    execution_id?: unknown;
    status?: unknown;
    result_version?: unknown;
    rejection_code?: unknown;
    latest_snapshot?: {
      execution_id?: unknown;
      session_id?: unknown;
      status?: unknown;
      status_version?: unknown;
      current_attempt_id?: unknown;
    };
  };
};

/** Apply the canonical exact-command acknowledgement before its paired
 * `execution.updated` frame. A stale rejection carries the authoritative
 * execution snapshot and must undo the local stop for that exact command. */
export function handleExecutionCommandUpdated(frame: unknown): void {
  const command = (frame as ExecutionCommandFrame | null)?.command;
  if (!command || typeof command !== "object") return;
  const commandId = typeof command.command_id === "string"
    ? command.command_id : "";
  const executionId = typeof command.execution_id === "string"
    ? command.execution_id : "";
  const status = String(command.status || "").toLowerCase();
  if (!commandId || !executionId) return;
  const pending = runtimeState._optimisticCancels[commandId];
  // A stop of an execution that had already ended: nothing was left to
  // stop, and nothing failed. The execution's own frame settles the view.
  if (status === "already_terminal") {
    delete runtimeState._optimisticCancels[commandId];
    return;
  }
  if (status !== "accepted" && status !== "applied" && status !== "rejected") {
    return;
  }
  if (status !== "rejected") {
    const resultVersion = typeof command.result_version === "number"
      ? command.result_version
      : command.latest_snapshot?.status_version;
    const store = useSessionStore.getState();
    const sessionId = pending?.sessionId
      || (typeof command.latest_snapshot?.session_id === "string"
        ? command.latest_snapshot.session_id
        : undefined)
      || Object.entries(store.runningTasks)
      .find(([, task]) => task.execution_id === executionId)?.[0];
    if (sessionId && typeof resultVersion === "number") {
      const current = store.runningTasks[sessionId];
      if (
        current
        && current.execution_id === executionId
        && !(current && typeof current.status_version === "number"
          && current.status_version > resultVersion)
      ) {
        store.setRunningTaskFor(sessionId, {
          ...current,
          execution_id: executionId,
          status_version: resultVersion,
        }, "never");
      }
    }
    // A stop's command answers that the stop is in force, not that the
    // turn has ended (a repeated stop is accepted while the turn still
    // runs): the execution's cancelled frame says "Stopped." and closes
    // the optimistic entry (use-ws).
    return;
  }

  // Rejections are broadcast to every tab; only the tab that sent the
  // command reports it (a steer's own row shows its outcome).
  if (pending) showToast(`Execution request failed: ${command.rejection_code || "unknown error"}`, { tone: "error" });
  const snapshot = command.latest_snapshot;
  const snapshotExecutionId = typeof snapshot?.execution_id === "string"
    ? snapshot.execution_id : "";
  const snapshotSessionId = typeof snapshot?.session_id === "string"
    ? snapshot.session_id : "";
  const snapshotVersion = snapshot?.status_version;
  // `latest_snapshot` is the canonical recovery payload. Without it a
  // rejection cannot prove that the execution still exists, so preserve the
  // optimistic stop and let the normal transcript/run-state reload settle it.
  if (
    !pending
    || snapshotExecutionId !== executionId
    || !snapshotSessionId
    || snapshotSessionId !== pending.sessionId
    || typeof snapshotVersion !== "number"
  ) {
    delete runtimeState._optimisticCancels[commandId];
    return;
  }
  const store = useSessionStore.getState();
  const current = store.runningTasks[snapshotSessionId];
  // A queued follow-up may already own the session. An old rejection must
  // never replace that newer execution with the cancelled command's task.
  if (current?.execution_id && current.execution_id !== executionId) {
    delete runtimeState._optimisticCancels[commandId];
    return;
  }
  if (
    current
    && typeof current.status_version === "number"
    && current.status_version > snapshotVersion
  ) {
    // The slot already holds this turn at a newer version: it runs, so
    // the stop drawn over its reply goes, as for any other refusal.
    store.applyLocalChange(snapshotSessionId, { kind: "overlay_command_rejected", commandId });
    delete runtimeState._optimisticCancels[commandId];
    return;
  }
  const ownsForeground = snapshot?.status !== "paused"
    && !(snapshot?.status === "reconciliation_required" && !snapshot?.current_attempt_id);
  const restoredTask = {
    ...pending.task,
    session_id: snapshotSessionId,
    execution_id: executionId,
    status_version: snapshotVersion,
    cancelling: false,
  };
  store.setRunningTaskFor(snapshotSessionId, ownsForeground ? restoredTask : null, "never");
  // The row keeps its server status; only what was drawn over it goes.
  store.applyLocalChange(snapshotSessionId, { kind: "overlay_command_rejected", commandId });
  if (snapshotSessionId === runtimeState.currentSessionId) {
    runtimeState.isPaused = String(snapshot?.status || "") === "paused";
    setRunning(ownsForeground);
    setRunActive(ownsForeground);
    updatePauseBtn();
  }
  delete runtimeState._optimisticCancels[commandId];
}

/* ===== sessions_list / running_task ============================== */

interface SessionRow {
  id: string;
  title?: string;
  created_at?: number;
  /** 最后活跃时间（服务端每次列表都带上，权威值总是覆盖）。 */
  updated_at?: number;
  /** Sidebar placement in the project list (see sessions-list helpers). */
  sort_at?: number;
  sort_since?: number;
  channel?: string | null;
  harness?: string | null;
  model?: string | null;
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
   *  "needs_input" → amber, "done" → completed, "failed" → the last turn
   *  failed; "done"/"failed" pair with `unread` for the blue/red dot. */
  status?: "needs_input" | "done" | "failed" | "idle" | null;
  /** Finished result not yet opened → blue dot. */
  unread?: boolean;
  /** Project NAME this conversation belongs to (home-folder name for
   *  ad-hoc chats) — drives the sidebar's "group by project" view. */
  project?: string | null;
}

/** A conversation the user is looking at never shows the unread dot: its
 *  own finished turn is already seen. The focused one (the route) is also
 *  marked read on the server, which keeps that socket's focus on it; a
 *  split's other pane is cleared here only, so focus stays put. */
function unreadOnScreen(sessionId: string): boolean {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return false;
  if (!visibleSessionIds(useCenterTabs.getState()).includes(sessionId)) return false;
  if (sessionId === runtimeState.currentSessionId
      && typeof window !== "undefined" && window.location.pathname === "/s/" + sessionId) {
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "mark_session_read", session_id: sessionId }));
    }
  }
  return true;
}

/** Opening a conversation clears its dot at once, before the server's
 *  session_updated echo of mark_session_read. */
export function markSessionReadLocally(sessionId: string): void {
  const conv = runtimeState.conversations[sessionId];
  if (conv) conv.unread = false;
  const summary = useSessionStore.getState().conversations[sessionId];
  if (summary?.unread) useSessionStore.getState().upsertConversation({ ...summary, unread: false });
}

/** The window was hidden or shown. A hidden one keeps its focus but stops
 *  counting as on screen. Coming back to a window whose open chat finished
 *  meanwhile puts the chat on screen again, so its dot clears like on opening
 *  it. The flag goes first: the server ignores a read from a hidden window. */
export function windowVisibilityChanged(): void {
  const sock = getSocket();
  const open = sock && sock.readyState === WebSocket.OPEN;
  const hidden = document.visibilityState === "hidden";
  if (open) sock.send(JSON.stringify({ action: "set_window_hidden", hidden }));
  const sessionId = runtimeState.currentSessionId;
  if (hidden || !sessionId) return;
  if (!useSessionStore.getState().conversations[sessionId]?.unread) return;
  markSessionReadLocally(sessionId);
  if (open) sock.send(JSON.stringify({ action: "mark_session_read", session_id: sessionId }));
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
          harness: c.harness || null,
          model: c.model || null,
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
          unread: !!c.unread && !unreadOnScreen(c.id),
          sort_at: c.sort_at || 0,
          sort_since: c.sort_since || 0,
          project: c.project || "",
        };
      } else {
        if (c.created_at && (!convs[c.id].created_at || convs[c.id].created_at === 0)) {
          convs[c.id].created_at = c.created_at;
        }
        if ("channel" in c) convs[c.id].channel = c.channel || null;
        if ("account_id" in c) convs[c.id].account_id = c.account_id || null;
        // The agent it runs in changes with its Harness chip: always current.
        convs[c.id].harness = c.harness || null;
        convs[c.id].model = c.model || null;
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
        if ("unread" in c) convs[c.id].unread = !!c.unread && !unreadOnScreen(c.id);
        if ("sort_at" in c) convs[c.id].sort_at = c.sort_at || 0;
        if ("sort_since" in c) convs[c.id].sort_since = c.sort_since || 0;
        if ("project" in c) convs[c.id].project = c.project || "";
        // A conversation first seen through its ACK or load has no Agent
        // yet; the list is where it arrives.
        if (c.agent_id) convs[c.id].agent_id = c.agent_id;
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
    status?: "needs_input" | "done" | "failed" | "idle" | null;
    unread?: boolean;
    sort_at?: number;
    sort_since?: number;
    agent_id?: string;
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
  if ("unread" in data) conv.unread = !!data.unread && !unreadOnScreen(data.id);
  if ("sort_at" in data) conv.sort_at = data.sort_at || 0;
  if ("sort_since" in data) conv.sort_since = data.sort_since || 0;
  if (typeof data.agent_id === "string") conv.agent_id = data.agent_id;
  mirrorUpsertConv(conv);
}

/** Whether a durable turn runs on the branch this page shows for its
 *  conversation: its `branch_id` against the selection when both are
 *  known, else the `side_branch` flag frozen when it was admitted. */
export function onSelectedBranch(task: { session_id?: string; branch_id?: string; side_branch?: boolean }): boolean {
  const selected = task.session_id
    ? useSessionStore.getState().composerSettingsBySession[task.session_id]?.active_branch_id
    : undefined;
  if (typeof task.branch_id === "string" && task.branch_id && typeof selected === "string" && selected) {
    return task.branch_id === selected;
  }
  return task.side_branch !== true;
}

/** Restore a durable continuation after its initial transport task ended.
 * Called only after the canonical execution update passes ordering checks. */
export function restoreForegroundExecutionTask(value: unknown, execution?: unknown): void {
  const canonical = execution as { session_id?: string; execution_id?: string; status?: string; reason_code?: string | null; current_attempt_id?: string | null } | undefined;
  // A turn paused on a question or approval is still the foreground turn
  // (the server keeps its run slot), so its task stays; any other pause
  // and an attempt-less reconciliation release the slot.
  const pausedOnUser = canonical?.status === "paused"
    && (canonical.reason_code === "wait_open" || canonical.reason_code === "system_access_required");
  if ((canonical?.status === "paused" && !pausedOnUser)
    || (canonical?.status === "reconciliation_required" && !canonical.current_attempt_id)) {
    const sid = canonical.session_id;
    const eid = canonical.execution_id;
    if (sid && eid && useSessionStore.getState().runningTasks[sid]?.execution_id === eid) {
      handleRunningTaskClear(sid, { execution_id: eid });
    }
    return;
  }
  if (!value || typeof value !== "object") return;
  const task = value as { session_id?: string; execution_id?: string; msg_id?: string; status_version?: number; side_branch?: boolean; branch_id?: string };
  // A turn on a branch that is not selected is not this conversation's
  // foreground turn: the pane shows the selected branch, so its Stop and
  // its steers must not reach that turn. Which branch is selected is
  // read now, the way the server's own run state reads it (a load, a
  // `run_state`); the flag frozen at admission answers only while this
  // page does not know the selection or the turn names no branch.
  if (!onSelectedBranch(task)) return;
  const sid = task.session_id;
  const eid = task.execution_id;
  if (!sid || !eid || !task.msg_id || typeof task.status_version !== "number") return;
  const current = useSessionStore.getState().runningTasks[sid];
  // A task restored before its ack carries the reply id as a stand-in;
  // the same turn's canonical execution replaces it.
  if (current?.execution_id && current.execution_id !== eid && current.msg_id !== task.msg_id) return;
  if (Object.values(runtimeState._optimisticCancels).some(
    (cancel) => cancel.sessionId === sid && cancel.task.execution_id === eid,
  )) return;
  handleRunningTask(task);
}

export function handleRunningTask(rt: unknown): void {
  if (!rt) return;
  const t = rt as {
    session_id?: string;
    msg_id?: string;
    reply_id?: string;
    func_name?: string;
    started_at?: number;
    display_params?: string;
    stream_events?: unknown[];
    execution_id?: string;
    status_version?: number;
    op_kind?: string;
    op_id?: string;
  };

  // 先做 cancelled 守卫，再碰任何 occupancy。旧代码先 setRunning(true)
  //（它会写入 {msg_id:""} 的占位 task）再检查取消，迟到的 running_task
  // 既复活了槽位、又把已有 task 的 execution_id 冲掉——之后停止键发不出
  // execution.cancel（turn-occupancy.md：迟到帧不得复活）。
  const sid = t.session_id;
  const mid = t.msg_id;
  const store = useSessionStore.getState();
  const priorTask = sid ? store.runningTasks[sid] : undefined;
  const replyId = t.reply_id || (priorTask?.msg_id === mid ? priorTask?.reply_id : undefined) || "";
  const executionId = t.execution_id || priorTask?.execution_id || replyId;
  const statusVersion = typeof t.status_version === "number"
    ? t.status_version
    : priorTask?.status_version;
  const current = sid && executionId
    ? selectRow(sid, executionId)(store) ?? (replyId ? selectRow(sid, replyId)(store) : undefined)
    : undefined;
  // Stop already marked this turn cancelled and released occupancy.
  // A late running_task for the same msg_id must not revive the slot.
  if (isBeingStopped(current?.status)) {
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

  // 2) Remember the slot. Which row runs is the server's: its status arrives on the row.
  if (!sid || !mid) return;
  store.setRunningTaskFor(sid, {
    session_id: sid,
    msg_id: mid,
    reply_id: replyId || undefined,
    func_name: t.func_name,
    started_at: t.started_at,
    execution_id: executionId,
    status_version: statusVersion,
    // What the branch's operation is (a reply, /compact, a rewind ...) and
    // the id a Stop of an operation with no execution names.
    ...(t.op_kind ? { op_kind: t.op_kind } : {}),
    ...(t.op_id ? { op_id: t.op_id } : {}),
    ...(priorTask?.msg_id === mid && priorTask?.phase ? { phase: priorTask.phase } : {}),
  });
}

export function handleRunningTaskClear(
  sessionId: string | undefined,
  cleared?: ClearedTaskIdentity & { force?: boolean },
): boolean {
  if (!sessionId) return false;
  const store = useSessionStore.getState();
  const current = store.runningTasks[sessionId];
  if (!cleared?.force && !shouldHonorRunningTaskClear(current, cleared)) {
    // A slot without ids cannot tell whether this clear is its turn's:
    // the session is asked once more.
    if (current && !(current.msg_id || "").trim() && !(current.execution_id || "").trim()) {
      void import("@/lib/chat/send/send-queue")
        .then(({ runStateMayHaveChanged }) => runStateMayHaveChanged(sessionId))
        .catch(() => { /* queue module unavailable: a reconnect asks */ });
    }
    return false;
  }
  store.setRunningTaskFor(sessionId, null, "always");
  // Nothing of the finished turn stays on the user's screen: the control
  // overlay over an app it operated closes with the turn.
  void import("@/lib/browser/control/native-window-store")
    .then(({ endNativeControl }) => endNativeControl(sessionId))
    .catch(() => { /* not a desktop shell */ });
  // The server let the turn go: a queue held after Stop may send now.
  void import("@/lib/chat/send/send-queue")
    .then(({ releaseStopHold }) => releaseStopHold(sessionId))
    .catch(() => { /* queue module unavailable: nothing is held */ });
  warmContextBreakdown(sessionId, store.heads[sessionId] ?? null);
  // If the clear is for the currently-active session, also drop the
  // legacy single-task / button state so the composer un-locks.
  if ((store.activeChatKey ?? store.currentSessionId) === sessionId) {
    setRunning(false);
  }
  return true;
}

/** Release the running slot a send reserved before its acknowledgement
 *  (no msg_id, no execution_id yet). A slot that names a real turn stays:
 *  only that turn's own end clears it. */
export function clearSendPlaceholder(sessionId: string): boolean {
  const store = useSessionStore.getState();
  const current = store.runningTasks[sessionId];
  if (!current || (current.msg_id || "").trim() || (current.execution_id || "").trim()) {
    return false;
  }
  handleRunningTaskClear(sessionId, { force: true });
  if ((store.activeChatKey ?? store.currentSessionId) === sessionId) setRunActive(false);
  return true;
}

/** Toast that a bound chat's reply did not reach its channel conversation
 *  (a `channel_delivery_failed` frame); the turn itself is unaffected. */
function showChannelDeliveryFailure(data: ChatResponseData): void {
  const label = data.channel === "wechat"
    ? translateText("WeChat", "微信")
    : String(data.channel_label || data.channel || "") || translateText("the channel", "渠道");
  const kind = String(data.error_kind || "unknown");
  showToast(
    translateText(
      `The reply was not delivered to ${label} (${kind}).`,
      `回复没有发到${label}（${kind}）。`,
    ),
    {
      tone: "error",
      duration: 8000,
      ...(typeof data.session_id === "string" ? { sessionId: data.session_id } : {}),
    },
  );
}

/** Toast an error frame that names no turn. Returns true when it rejected
 *  this client's pending send for the session, which it then settles. */
function settleUnscopedChatError(data: ChatResponseData): boolean {
  const content = String(data.content || data.code || "")
    || translateText("Request failed", "请求失败");
  showToast(content, { tone: "error" });
  const sid = typeof data.session_id === "string" ? data.session_id : "";
  if (!sid) return false;
  const rejectsSend = frameNamesPendingSend(sid, data) && (hasPendingFirstAck(sid)
    || (typeof data.code === "string" && !!data.code && hasPendingUserText(sid)));
  if (!rejectsSend) return false;
  const store = useSessionStore.getState();
  const pendingId = getPendingUserMessageId(sid);
  if (pendingId && selectRow(sid, pendingId)(store)) {
    store.applyLocalChange(sid, {
      kind: "local_patch", rowId: pendingId,
      fields: {
        status: "error", rawType: "error", ...(content ? { errorDetail: content } : {}),
        ...(typeof data.reason === "string" ? { errorReason: data.reason } : {}),
      },
    });
  }
  rejectPendingUserText(sid);
  clearPendingFirstAck(sid);
  useCenterTabs.getState().markSessionSent(sid, false);
  clearSendPlaceholder(sid);
  return true;
}

/* ===== handleChatResponse (bookkeeping) ========================== */

/** A turn compacting its own context mid-run: the run indicator names the
 *  step while it lasts. /compact itself is the branch's operation
 *  (`op_kind: "compact"`) and needs nothing here. */
function setRunPhase(sid: string, phase: string | undefined): void {
  const store = useSessionStore.getState();
  const task = store.runningTasks[sid];
  if (!task || task.op_kind === "compact" || task.phase === phase) return;
  const next = { ...task };
  if (phase) next.phase = phase;
  else delete next.phase;
  store.setRunningTaskFor(sid, next, "never");
}

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
    // The run indicator shows it: /compact is the branch's operation, a
    // turn's own compaction is a phase of that turn.
    if (sid) setRunPhase(sid, "compact");
    return;
  }
  if (type === "compaction_finished") {
    // 压缩把保留尾部重挂到摘要节点下，活跃分支的节点集合整个换了一批。
    // Context tab 的明暗靠 /context-range 的 node_ids，不刷新就停在压缩前
    // 的旧集合上 → 整图全暗。
    if (targetsActive && sid) {
      refreshHistoryContextRange(sid, { changed: true });
      if (!data.no_op) void fetchBranches(sid, { force: true });
    }
    if (sid) {
      setRunPhase(sid, undefined);
      const noOp = Boolean(data.no_op);
      // A turn's own compaction that had nothing to cut leaves the history
      // as it was, which is all the person needs to see: only a /compact
      // they asked for answers that it changed nothing.
      if (noOp) {
        if (!data.user_initiated) return;
        appendLocalNotice(sid, {
          id: "compaction_finished_" + Date.now().toString(36),
          role: "system",
          rowKind: "system_event",
          kind: "compaction",
          content: translateText(
            "Context unchanged; no smaller summary was applied.",
            "上下文保持不变，本次没有应用更小的摘要",
          ),
          status: "done",
        });
      } else {
        // The read brings the summary card that takes this notice's place.
        const sock = getSocket();
        if (sock && sock.readyState === WebSocket.OPEN) {
          requestSessionLoad({ action: "load_session", session_id: sid }, true);
        }
      }
    }
    return;
  }
  if (type === "compaction_failed") {
    if (sid) {
      setRunPhase(sid, undefined);
      const info = data as { error?: unknown; user_initiated?: unknown; cancelled?: unknown };
      // A turn's own compaction that did not go through leaves the history
      // as it was and is tried again on a later request, so it says nothing.
      // Only a /compact the person ran reports how it ended.
      if (!info.user_initiated) return;
      const err = String(info.error ?? "");
      appendLocalNotice(sid, {
        id: "compaction_failed_" + Date.now().toString(36),
        role: "system",
        rowKind: "system_event",
        kind: "compaction",
        content: info.cancelled
          ? translateText("Compaction stopped; context unchanged.", "已停止压缩，上下文保持不变")
          : err
            ? translateText("Context compaction failed: ", "上下文压缩失败：") + err
            : translateText("Context compaction failed", "上下文压缩失败"),
        status: "done",
      });
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
    // The frame does not end a running turn: /goal status mid-run leaves
    // that run going. Only the command's own send settles here: it was
    // no message, so a bubble drawn for it (a command typed before the
    // registry loaded) goes, and only the reply line stays.
    const content = String((data as { content?: unknown }).content ?? "");
    const store = useSessionStore.getState();
    if (sid) {
      const pendingId = getPendingUserMessageId(sid);
      if (pendingId && selectRow(sid, pendingId)(store)?.status === "pending") {
        store.applyLocalChange(sid, { kind: "local_remove", rowId: pendingId });
      }
      clearPendingUserText(sid);
      clearPendingFirstAck(sid);
      useCenterTabs.getState().markSessionSent(sid, false);
      clearSendPlaceholder(sid);
    }
    if (sid && content) {
      appendLocalNotice(sid, {
        id: "local_cmd_" + Date.now().toString(36),
        role: "system",
        rowKind: "system_text",
        content,
        status: (data as { error?: unknown }).error ? "error" : "done",
      });
    }
    return;
  }
  if (type === "follow_up_question") {
    if (targetsActive) handleFollowUpQuestion(data as { question?: string });
    return;
  }
  if (type === "stream_event" || type === "tree_update" || type === "user_message") {
    return;
  }

  // Nested execution streams and future protocol extensions are not final
  // replies. Only the terminal envelope types may change parent bookkeeping.
  if (type !== "result" && type !== "error" && type !== "retry_result") return;

  // Final response (result / error / retry_result) -- task done.
  // Clear per-session running state from the response's session_id
  // (NOT runtimeState.currentSessionId — the user may have switched away
  // while the background turn was finishing). The clear helper itself
  // flips the legacy button if the cleared session is the active one.
  //
  // 例外：聊天回合中途完成的内联 Agent method / spawn 结果也走
  // display:"runtime" 的 result/error 帧（Server 即时转发），
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
  if (!nestedRuntimeResult && data.turn_continues !== true) {
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
    if (targetsActive) updateContextStats();

    // Conversation title — sidebar metadata, not transcript state.
    if (!conv.title || conv.title === "New conversation") {
      const st = useSessionStore.getState();
      const firstId = (st.messageOrder[sid] ?? [])[0];
      const seed = firstId
        ? String(selectRow(sid, firstId)(st)?.content || "")
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
  context_window?: number | null;
  /** Server-computed occupancy — the single number the ring AND the
   *  /context panel both render. `basis` says where it came from:
   *  "measured" (a request just reported its real prompt size) or
   *  "estimated" (the graph moved since, so it was recomputed). */
  window?: number | null;
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
  breakdown?: import("@/lib/chat/context-breakdown-cache").ContextBreakdown & {
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
      // A record that names its window field but leaves it empty says the
      // window is unknown (0 clears it); an older record without one keeps it.
      data.window || data.context_window
        || ("window" in data || "context_window" in data ? 0 : null),
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
    refreshHistoryContextRange(sid, { changed: true });
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
    // A status tree is execution metadata, not a transcript snapshot.
    // It has neither stable message IDs nor ordered assistant blocks.

  }
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
    '<div class="follow-up-container" style="margin:12px 0;padding:12px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg-secondary)">' +
    '<div style="color:var(--accent-yellow);font-weight:600;margin-bottom:8px">&#9888; Follow-up Question</div>' +
    '<div style="margin-bottom:10px;color:var(--text-primary)">' +
    esc(data.question) +
    "</div>" +
    '<div style="display:flex;gap:8px">' +
    '<input type="text" id="followUpInput" placeholder="Type your answer..." ' +
    'style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-primary);color:var(--text-primary);font-size:var(--fs-base)" ' +
    "onkeydown=\"if(event.key==='Enter')submitFollowUp()\">" +
    '<button onclick="submitFollowUp()" ' +
    'style="padding:8px 16px;border:none;border-radius:var(--radius-sm);background:var(--accent-blue);color:white;cursor:pointer;font-size:var(--fs-base)">Submit</button>' +
    "</div>" +
    "</div>";
  contentArea.insertAdjacentHTML("beforeend", fuHtml);
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (inp) inp.focus();
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

  if (!window.location.pathname.match(/^\/s\//)) {
    setWelcomeVisible(true);
  }

  // Rehydrate the web search chip flag from localStorage.
  try {
    if (localStorage.getItem("agentic_web_search_enabled") === "1") {
      runtimeState._webSearchEnabled = true;
    }
  } catch {
    /* ignore */
  }
  updatePlusBtnIndicator();
  refreshWebSearchProviderLabel();
}

/* ===== inline-handler bridge ===================================== */

// The follow-up prompt is injected as an HTML string carrying
// `onclick="submitFollowUp()"`, so that one name has to resolve off
// `window` at click time. Everything else is a direct import. The
// server prerender imports this module too (the sidebar marks rows
// read through it), where there is no window to attach to.
if (typeof window !== "undefined") Object.assign(window, { submitFollowUp });
