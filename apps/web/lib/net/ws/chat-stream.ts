/**
 * Chat-frame WS reducer.
 *
 * Numbered `transcript_update` frames own every server row (see
 * runtime-bridge/session/transcript-updates.ts). What is left here is what
 * the chat frames still say on their own:
 *
 *   chat_ack       the send was accepted: its local row takes the server id.
 *   chat_response  { type, msg_id, session_id, ... }
 *       stream_event  text, thinking and text_end are placed by offset;
 *                     retry sets the client-only retryStatus.
 *       error         a send refused before its ack, or the end of a turn.
 *       result, cancelled  the end of a turn: no row is written here.
 *
 * NOT attached to a socket here: `use-ws.ts` owns the connection and feeds
 * envelopes in.
 */

import type { PastedTextElement } from "../../chat/send/paste-elements.ts";
import {
  applyExecutionStreamFromChat,
  useExecutionStreamStore,
} from "@/lib/execution-stream";
import {
  selectRow,
  useSessionStore,
  type ChatMsg,
} from "@/lib/session-store";
import { chatAckIsActive, useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { settleAgentInvocation } from "@/lib/agents/settle-agent-invocation";
import { adoptDraftContextBreakdown } from "@/lib/chat/context-breakdown-cache";
import {
  acknowledgePendingUserText,
  clearPendingUserText,
  clearPendingFirstAck,
  getPendingUserTimestamp,
  getPendingUserMessageId,
  hasPendingUserText,
  getPendingUserText,
  pendingUserHasAttachments,
  rejectPendingUserText,
} from "@/lib/chat/send/pending-user-text";
import { frameNamesPendingSend } from "./unacked-sends";
import { offSelectedBranch } from "../../chat/run/selected-branch.ts";
import { appendCredentialNotice, patchHeldRow, placeCitations, queueTextFrame } from "./text-frames.ts";
import { normalizeCitations } from "../../chat/transcript/web-sources.ts";

function requestExecutionStreamSnapshot(event: {
  session_id: string;
  execution_id: string;
  node_id: string;
  generation?: number;
  revision?: number;
}): void {
  try {
    void import("@/lib/runtime-bridge/state").then(({ getSocket }) => {
      const sock = getSocket();
      if (!sock || sock.readyState !== WebSocket.OPEN) return;
      sock.send(JSON.stringify({
        action: "subscribe_execution_stream",
        session_id: event.session_id,
        execution_id: event.execution_id,
        node_ids: [event.node_id],
        generation: event.generation ?? 0,
        revision: event.revision ?? 0,
        schema_version: 1,
        subscription_id: `sync_${event.node_id}_${Date.now()}`,
      }));
    });
  } catch {
    /* ignore */
  }
}

useExecutionStreamStore.getState().setSnapshotRequester((ev) => {
  requestExecutionStreamSnapshot(ev);
});


interface StreamEvent {
  type: string;
  attempt?: number;
  next_attempt?: number;
  max_attempts?: number;
  reason?: string;
  issues?: { code?: string }[];
  delay_ms?: number;
  /** retry: what failed, one short line. */
  error?: string;
  text?: string;
  /** text: what the reply's flat text takes before this delta, on the frame
   *  that opens a passage after a tool call, reasoning or a steer. */
  separator?: string;
  /** text_end: sources the provider cited for the block it closes. */
  citations?: unknown;
  /** retry with reason credential / credential_wait: the credential switch. */
  credential?: { to?: string; from?: string; cause?: string };
}

interface ChatResponseData {
  type: string;
  msg_id?: string;
  session_id?: string;
  /** The branch the turn runs on. */
  branch_id?: string;
  event?: StreamEvent;
  content?: string;
  text?: string;
  cancelled?: boolean;
  /** Structured error taxonomy on a `type:"error"` response. */
  error_detail?: string;
  reason?: string;
  retryable?: boolean;
  /** Rejection code. ``"run_active"`` means the backend refused the turn
   *  because that session already had one running. */
  code?: string;
  /** The rejected message text, echoed back so the client can re-send it
   *  without the user retyping. Only present on a ``run_active`` error. */
  retry_query?: string;
  /** Set on every frame of a Job's turn: a sub-agent working in this session. */
  job_id?: string;
  /** Echo of the chat frame's `client_request_id`: names the exact send an
   *  ack or a rejection before the ack answers. */
  client_request_id?: string;
  /** Set on the error frame of a failed function Retry; it answers that
   *  request and ends no chat turn. */
  retry_request_id?: string;
  /** Text frames: the block this frame writes, and where in it. */
  row_id?: string;
  item_id?: string;
  item_index?: number;
  offset?: number;
  /** Text frames: the task and step the block belongs to, when it has one. */
  task?: string;
  step?: string;
}

interface WsEnvelope {
  type: string;
  data?: unknown;
}

const sessionByMsgId = new Map<string, string>();

/** Connection teardown forgets which session each turn belongs to. */
export function clearSessionByMsgId(): void {
  sessionByMsgId.clear();
}

function isTurnEnd(type: string): boolean {
  return type === "result" || type === "error" || type === "cancelled";
}

export function applyChatWsMessage(msg: WsEnvelope): void {
  if (msg.type === "chat_ack") {
    handleAck(msg.data as { session_id?: string; msg_id?: string; job_id?: string; starts_goal?: boolean });
    return;
  }
  if (msg.type === "chat_response") {
    const data = msg.data as ChatResponseData & { type?: string };
    // Nested LLM stream: independent of msg_id / outer reply content.
    if (data && data.type === "execution_stream") {
      applyExecutionStreamFromChat(data as unknown as Record<string, unknown>);
      return;
    }
    handleResponse(data as ChatResponseData);
  }
}

/** A `chat_ack` tells us which conversation the turn belongs to — for a
 *  brand-new chat that's the first time the server-assigned id is known.
 *  The locally stashed user turn takes the server's id here. */
function handleAck(
  d: { session_id?: string; msg_id?: string; text?: string; job_id?: string; client_request_id?: string; starts_goal?: boolean } | undefined,
): void {
  // A Job's ack names the Job's own turn; numbered updates draw it.
  if (!d?.session_id || d.job_id) return;
  const sid = d.session_id;
  const tabs = useCenterTabs.getState();
  const isActive = chatAckIsActive(sid);
  tabs.markSessionReady(sid);
  // The backend has durably accepted the Agent binding/snapshot. A rejected
  // first send keeps this intent for retry; only an ACK consumes it, and the
  // pick becomes the conversation's own Agent and model.
  settleAgentInvocation(sid);
  if (isActive) {
    const before = useSessionStore.getState();
    // The shown draft becomes this session: its context estimate goes with
    // it (the same snapshot the composer ring read for the draft).
    if (before.currentSessionId == null && before.activeChatKey === sid) {
      adoptDraftContextBreakdown(
        sid,
        before.agentSettings.chat?.provider,
        before.agentSettings.chat?.model,
        before.pendingProjectsByChat[sid] ?? null,
      );
    }
    before.setCurrentConv(sid);
  }
  if (d.msg_id) sessionByMsgId.set(d.msg_id, sid);

  // An ack naming another request is not this client's pending send's.
  const ours = frameNamesPendingSend(sid, d);
  const text = ours ? getPendingUserText(sid) : undefined;
  const timestamp = getPendingUserTimestamp(sid);
  const pendingId = getPendingUserMessageId(sid);
  if (d.msg_id && typeof text === "string" && text) {
    // The ack echoes the STORED text, which differs from the draft in
    // one way that matters: attachment mentions now carry the absolute
    // path the backend wrote once the bytes hit disk.
    const content = typeof d.text === "string" && d.text ? d.text : text;
    // A /goal's message is its objective, labelled Goal from the ack on.
    const goal = d.starts_goal === true ? { startsGoal: true } : {};
    const store = useSessionStore.getState();
    const local = pendingId ? selectRow(sid, pendingId)(store) : undefined;
    if (selectRow(sid, d.msg_id)(store)) {
      // The server's own row beat this ack and already replaced the local one.
      if (local) store.applyLocalChange(sid, { kind: "local_remove", rowId: pendingId! });
    } else if (local) {
      store.applyLocalChange(sid, {
        kind: "local_replace", rowId: pendingId!,
        row: { ...local, content, status: "done", ...goal, ...(timestamp === undefined ? {} : { timestamp }), id: d.msg_id, localAnchor: undefined },
      });
    } else {
      appendLocalUserTurn(sid, d.msg_id, content, undefined, timestamp, "done", undefined, d.client_request_id);
      if (d.starts_goal === true) store.applyLocalChange(sid, { kind: "local_patch", rowId: d.msg_id, fields: goal });
    }
  }
  // The frame is the backend's durable acceptance boundary. Run the
  // composer cleanup only now; socket.write success is not acceptance.
  if (!ours) return;
  acknowledgePendingUserText(sid);
  clearPendingFirstAck(sid);
}

function handleResponse(d: ChatResponseData | undefined): void {
  if (!d || !d.msg_id) return;
  // Older envelopes may lack `session_id`; their turn is then known from its
  // `chat_ack` or from the running task that owns the msg_id. Never the
  // conversation on screen: after a reload, a run started earlier would
  // land in whichever chat the user opened.
  const sid = d.session_id || sessionByMsgId.get(d.msg_id)
    || Object.values(useSessionStore.getState().runningTasks)
      .find(task => task?.msg_id === d.msg_id)?.session_id;
  if (!sid) return;
  // A turn on a branch other than the selected one is not drawn: its rows
  // are not in this transcript, and switching to it loads them.
  if (offSelectedBranch(d.branch_id,
    useSessionStore.getState().composerSettingsBySession[sid]?.active_branch_id)) {
    if (isTurnEnd(d.type)) sessionByMsgId.delete(d.msg_id);
    return;
  }
  if (d.type === "stream_event") {
    if (d.event) applyStreamFrame(d, d.event);
    return;
  }
  if (d.job_id) {
    if (isTurnEnd(d.type)) sessionByMsgId.delete(d.msg_id);
    return;
  }
  // A failed function Retry answers its request (runtime-block restores the
  // card and toasts); its random msg_id names no turn, row or pending send.
  if (d.type === "error" && typeof d.retry_request_id === "string") return;
  // A frame of a turn this client already saw acknowledged (or loaded)
  // answers that turn, never the send still waiting for its ACK.
  const ownsTurn = sessionByMsgId.has(d.msg_id)
    || (!!d.row_id && !!selectRow(sid, d.row_id)(useSessionStore.getState()));
  const answersSend = !ownsTurn && frameNamesPendingSend(sid, d);
  const pendingId = getPendingUserMessageId(sid);
  if (isTurnEnd(d.type)) sessionByMsgId.delete(d.msg_id);


  // `run_active`: the client believed the session was idle and dispatched
  // a turn the backend then refused, because a run really was in flight
  // (its own optimistic state can trail the server's by a beat). Nothing
  // is wrong from the user's point of view — the message just needs to
  // wait. Put it back in the send queue instead of surfacing an error
  // bubble for a race they didn't cause and can't act on. The frame
  // carries a msg_id of its own with no reply row behind it, so this
  // must return here: it ends no turn.
  if (d.type === "error" && d.code === "run_active") {
    // Another send's rejection: this client's pending send is untouched.
    if (!answersSend) return;
    const rejected = typeof d.retry_query === "string" ? d.retry_query : "";
    const hasAttachments = pendingUserHasAttachments(sid);
    // Attachment turns cannot be represented by the text-only retry queue.
    // Leave their original composer draft and files in place for an explicit
    // retry instead of silently converting the turn to plain text.
    if (rejected && !hasAttachments) {
      void import("@/lib/chat/send/send-queue").then((m) =>
        m.requeueRejected(sid, rejected),
      );
    }
    if (hasAttachments) {
      if (pendingId) useSessionStore.getState().applyLocalChange(sid, {
        kind: "local_patch", rowId: pendingId,
        fields: { status: "error", rawType: "error", errorReason: "run_active", ...notSentDetail(d) },
      });
      rejectPendingUserText(sid);
    } else {
      clearPendingUserText(sid);
      if (pendingId) useSessionStore.getState().applyLocalChange(sid, { kind: "local_remove", rowId: pendingId });
    }
    clearPendingFirstAck(sid);
    useCenterTabs.getState().markSessionSent(sid, false);
    return;
  }

  // A terminal backend rejection is not an ACK. Release only the internal
  // ACK reservation so the user can submit the unchanged composer draft
  // again; the composer cleanup callback is deliberately discarded.
  if (d.type === "error" && answersSend) {
    const hadSend = !!pendingId || hasPendingUserText(sid);
    if (pendingId) useSessionStore.getState().applyLocalChange(sid, {
      kind: "local_patch", rowId: pendingId,
      fields: { status: "error", rawType: "error", errorReason: d.reason, errorRetryable: d.retryable, ...notSentDetail(d) },
    });
    // With no bubble to carry "Not sent", the refusal would be invisible:
    // the draft just stays in the composer. Say why.
    if (!pendingId) {
      void import("@/lib/format-utils/toast").then((m) =>
        m.showToast(String(d.content || "The message was not sent."), { tone: "error" }),
      );
    }
    rejectPendingUserText(sid);
    clearPendingFirstAck(sid);
    useCenterTabs.getState().markSessionSent(sid, false);
    // The local row says why; the turn never started, so no reply stands behind it.
    if (hadSend) return;
  }

  if (isTurnEnd(d.type)) endTurn(sid, d, answersSend);
}

/** The server's words for a refused send, shown after "Not sent". */
function notSentDetail(d: ChatResponseData): { errorDetail?: string } {
  const text = d.error_detail ?? d.content ?? d.text;
  return typeof text === "string" && text ? { errorDetail: text } : {};
}

function applyStreamFrame(d: ChatResponseData, event: StreamEvent): void {
  if (d.row_id && d.item_id) {
    if ((event.type === "text" || event.type === "thinking") && typeof d.offset === "number" && typeof d.item_index === "number") {
      queueTextFrame({
        rowId: d.row_id, itemId: d.item_id, itemIndex: d.item_index, offset: d.offset, type: event.type, text: event.text ?? "",
        ...(event.type === "text" && event.separator ? { separator: event.separator } : {}),
        ...(d.task ? { task: d.task } : {}),
        ...(d.step ? { step: d.step } : {}),
      });
      return;
    }
    if (event.type === "text_end") {
      const citations = normalizeCitations(event.citations);
      if (citations) placeCitations(d.row_id, d.item_id, citations);
      return;
    }
  }
  if (event.type === "retry" && event.reason === "credential") {
    // Another of the provider's credentials took over before the reply
    // started: one muted line over the reply, no retry state.
    const switched = event.credential;
    if (d.row_id && switched?.to) {
      appendCredentialNotice(d.row_id, { to: switched.to, from: switched.from ?? "", ...(switched.cause ? { cause: switched.cause } : {}) });
    }
    return;
  }
  if (event.type === "retry" || event.type === "structured_output_retry") {
    if (d.row_id) patchHeldRow(d.row_id, {
      retryStatus: {
        attempt: event.next_attempt ?? event.attempt ?? 2,
        maxAttempts: event.max_attempts,
        reason: event.reason ?? event.issues?.[0]?.code,
        delayMs: event.delay_ms,
        error: event.error || undefined,
        ...(event.reason === "credential_wait" && event.credential?.to ? { waitingFor: event.credential.to } : {}),
        receivedAt: Date.now(),
      },
    });
  }
}

/** The row's end comes as an update; these are the parts only the frame says. */
function endTurn(sid: string, d: ChatResponseData, answersSend: boolean): void {
  const rowId = d.row_id;
  const store = useSessionStore.getState();
  if (rowId) {
    patchHeldRow(rowId, { retryStatus: undefined });
    const finished = d.type === "result" && !d.cancelled;
    if (finished && store.currentSessionId === sid && selectRow(sid, rowId)(store)?.autoPreviewFiles === undefined) {
      patchHeldRow(rowId, { autoPreviewFiles: true });
    }
  }
  if (d.type === "error" && !answersSend) releaseTurnAfterError(sid, d.msg_id as string, rowId);
}

/** A reply that failed is the end of its turn on the user's screen: the
 *  composer sends again instead of queueing behind a run that is over.
 *  The frame decides, not the row: the reply's error status is published
 *  on the next update tick, after this frame. Only a reply already settled
 *  as done says the error is a late one of a finished turn. The server's
 *  own clear usually lands a beat later and finds nothing to do; if the
 *  run really is still held, the send comes back as `run_active` and
 *  re-queues on its own. */
function releaseTurnAfterError(sid: string, msgId: string, rowId: string | undefined): void {
  const store = useSessionStore.getState();
  if (rowId && selectRow(sid, rowId)(store)?.status === "done") return;
  const current = store.runningTasks[sid];
  if (!current || (current.msg_id || "") !== msgId) return;
  void import("@/lib/runtime-bridge/session/chat-handlers").then(({ handleRunningTaskClear }) =>
    handleRunningTaskClear(sid, { msg_id: msgId, execution_id: current.execution_id }),
  );
}

/**
 * Add the just-sent user turn so the bubble appears immediately, before the
 * server has it. Later frames for the same send replace it by request id.
 */
export function appendLocalUserTurn(
  sessionId: string,
  msgId: string,
  text: string,
  display?: "runtime" | "normal",
  timestamp = Date.now(),
  status: ChatMsg["status"] = "done",
  pastedElements?: PastedTextElement[],
  clientRequestId?: string,
): void {
  const store = useSessionStore.getState();
  if (selectRow(sessionId, msgId)(store)) return;
  // The composer's optimistic turn: a draft holding it is no longer an
  // empty New chat, even before the backend acknowledges it.
  if (status === "pending") useCenterTabs.getState().markSessionSent(sessionId);
  store.applyLocalChange(sessionId, {
    kind: "local_append",
    row: {
      id: msgId, role: "user", rowKind: "user", content: text,
      display: display === "runtime" ? "runtime" : undefined,
      status, timestamp, clientRequestId,
      ...(pastedElements?.length ? { pastedElements } : {}),
    },
  });
}
