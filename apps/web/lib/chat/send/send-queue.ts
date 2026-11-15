"use client";

/**
 * Client-side send queue — "type while it runs".
 *
 * A message typed while the session's turn is still running is parked
 * here instead of being dropped (the backend rejects a concurrent
 * `chat` with `code:"run_active"`). The entries render as dimmed
 * "queued" bubbles above the composer and drain one at a time: when
 * a session's running task clears, the head entry is sent as an
 * ordinary turn.
 *
 * Rows survive a reload, a closed window and an App restart until they are
 * sent (queue-storage.ts); their files are kept in IndexedDB
 * (queued-attachment-store.ts) and read back before the row can send.
 * Each queue belongs to one conversation.
 */

import { create } from "zustand";
import { adoptOrphanedQueues, saveQueues } from "./queue-storage";
import { hasPendingFirstAck } from "./pending-user-text";
import type { ChatAttachment } from "@/components/chat/composer/submit/send-chat-message";
import type { PastedTextElement } from "./paste-elements";
import { queuedMessagePayload, snapshotQueuedAttachments, type QueuedAttachments } from "./queued-attachments";
import { restoreQueuedAttachments, syncQueuedAttachments } from "./queued-attachment-store";
import type { ExecutionCommand } from "@/lib/execution/execution-debugger";

// ponytail: `send-chat-message` imports back into this module (it records the
// per-session turn settings), so the send function is reached through a
// registered callback rather than a static import. One indirection beats
// an import cycle between a store and the socket writer.
import { useSessionStore } from "@/lib/session-store";
import type { RunningTask } from "@/lib/session-store/types";

type SendFn = (args: {
  text: string;
  textElements?: PastedTextElement[];
  sessionId: string | null;
  thinking: string;
  webSearchEnabled: boolean;
  serviceTier?: string;
  background?: boolean;
  attachments?: ChatAttachment[];
  hasAttachments?: boolean;
  onReject?: () => void;
}) => boolean;

let sendImpl: SendFn | null = null;

/** Wired once by `send-chat-message` on module load. */
export function registerChatSender(fn: SendFn): void {
  sendImpl = fn;
}

export interface QueuedMessage extends QueuedAttachments {
  /** Prevent automatic dispatch while the user edits this draft. */
  editing?: boolean;
  deliveryError?: boolean;
  id: string;
  text: string;
  /** Pasted spans of `text` to show collapsed once it is sent. */
  textElements?: PastedTextElement[];
  /** Turn settings captured at type-time, replayed verbatim on send. */
  thinking: string;
  webSearchEnabled: boolean;
  serviceTier?: string;
  background: boolean;
  queuedAt: number;
  /** Restored after a reload; its files are still being read back. */
  attachmentsPending?: boolean;
  /** A steer request is in flight; drain must wait for its acknowledgement. */
  injecting?: boolean;
  /** Retained verbatim until its durable acknowledgement is known. */
  steerCommand?: ExecutionCommand;
  steerError?: "unconfirmed" | "unavailable" | "too_long" | "retry" | "cancelled" | "ended";
}

/** What the composer hands over; the store stamps id + queuedAt. */
export type QueueDraft = Omit<QueuedMessage, "id" | "queuedAt">;

interface SendQueueState {
  /** sessionId → entries, in send order. */
  queues: Record<string, QueuedMessage[]>;
  enqueue: (sessionId: string, draft: QueueDraft) => string;
  remove: (sessionId: string, id: string) => void;
  beginEdit: (sessionId: string, id: string) => boolean;
  endEdit: (sessionId: string, id: string) => void;
  updateDraft: (sessionId: string, id: string, draft: QueuedAttachments & { text: string }) => boolean;
  retryDraft: (sessionId: string, id: string) => void;
  removeDraft: (sessionId: string, id: string) => boolean;
  /** Remove every row the user can remove (not one being edited or whose
   *  steer delivery is in flight) in one write, so clearing never drains a
   *  row that was about to be removed. Returns how many were removed. */
  clearDrafts: (sessionId: string) => number;
  /** Put rows in the order of `ids` (a drag in the queue card). Rows that
   *  left the queue meanwhile are skipped; rows not named keep their place
   *  after the named ones. Rows being delivered or edited never move. */
  reorder: (sessionId: string, ids: readonly string[]) => void;
  setInjecting: (sessionId: string, id: string, injecting: boolean) => void;
  setSteering: (sessionId: string, id: string, patch: Partial<Pick<QueuedMessage, "injecting" | "steerCommand" | "steerError">>) => void;
  /** Send the first sendable entry if the session is idle. No-op otherwise. */
  drain: (sessionId: string) => void;
}

const EMPTY: QueuedMessage[] = [];

// A queued message outranks an automatic Goal continuation, but the server
// cannot see this queue. Tell it when a conversation's queue fills or is
// emptied without sending; a drain's send is the server's own signal.
const draining = new Set<string>();
function hintGoalQueue(before: Record<string, QueuedMessage[]>, after: Record<string, QueuedMessage[]>): void {
  for (const sessionId of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const waiting = Boolean(after[sessionId]?.length);
    if (waiting === Boolean(before[sessionId]?.length) || (!waiting && draining.has(sessionId))) continue;
    void import("@/lib/net/api")
      .then(({ api }) => api.setGoalQueueWaiting(sessionId, waiting))
      .catch(() => {});
  }
}

let seq = 0;

/** A row that waits for the user (retry or edit) before it can go out. */
function parkedRow(row: QueuedMessage): boolean {
  return Boolean(!row.injecting && !row.steerCommand && !row.editing
    && (row.deliveryError || row.steerError === "cancelled" || row.steerError === "ended"));
}

/** Sessions stopped by this window whose turn the server still holds. */
const stopHolds = new Map<string, { executionId?: string; timer?: ReturnType<typeof setTimeout>; checks: number }>();

export const useSendQueue = create<SendQueueState>((set, get) => ({
  queues: {},

  enqueue: (sessionId, draft) => {
    // Unique across windows too: the id keys the row's stored files.
    const id = `q${++seq}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({
      queues: {
        ...s.queues,
        [sessionId]: [
          ...(s.queues[sessionId] ?? EMPTY),
          { ...draft, ...snapshotQueuedAttachments(draft), id, queuedAt: Date.now() },
        ],
      },
    }));
    return id;
  },

  reorder: (sessionId, ids) =>
    set((s) => {
      const cur = s.queues[sessionId];
      if (!cur) return {};
      const pinned = (row: QueuedMessage) => Boolean(row.injecting || row.steerCommand || row.editing);
      const byId = new Map(cur.map((row) => [row.id, row]));
      const moved = ids.map((id) => byId.get(id)).filter((row): row is QueuedMessage => !!row && !pinned(row));
      const movedIds = new Set(moved.map((row) => row.id));
      // Pinned rows hold their index; the movable rows fill the other slots.
      const rest = cur.filter((row) => !pinned(row) && !movedIds.has(row.id));
      const order = [...moved, ...rest];
      const next = cur.map((row) => (pinned(row) ? row : order.shift()!));
      if (next.every((row, i) => row === cur[i])) return {};
      return { queues: { ...s.queues, [sessionId]: next } };
    }),

  remove: (sessionId, id) =>
    set((s) => {
      const cur = s.queues[sessionId];
      if (!cur) return {};
      const next = cur.filter((q) => q.id !== id);
      const queues = { ...s.queues };
      if (next.length > 0) queues[sessionId] = next;
      else delete queues[sessionId];
      return { queues };
    }),

  beginEdit: (sessionId, id) => {
    const row = get().queues[sessionId]?.find(item => item.id === id);
    if (!row || row.editing || row.injecting || row.steerCommand || row.attachmentsPending) return false;
    set(s => ({ queues: { ...s.queues, [sessionId]: s.queues[sessionId].map(item => item.id === id ? {...item, editing:true} : item) } }));
    return true;
  },
  endEdit: (sessionId, id) => {
    const rows = get().queues[sessionId];
    if (!rows?.some(item => item.id === id && item.editing)) return;
    set(s => ({ queues: { ...s.queues, [sessionId]: rows.map(item => item.id === id ? {...item, editing:false} : item) } }));
  },
  updateDraft: (sessionId, id, draft) => {
    const rows = get().queues[sessionId];
    const row = rows?.find(item => item.id === id);
    if (!row || !row.editing || row.injecting || row.steerCommand) return false;
    if (!draft.text.trim() && !draft.images?.length && !draft.docs?.length) return false;
    set(s => ({ queues: { ...s.queues, [sessionId]: rows.map(item => item.id === id
      ? {...item, text:draft.text, ...snapshotQueuedAttachments(draft), editing:false, deliveryError:false, steerError:undefined,
        // Edited text no longer has the pasted spans where they were recorded.
        textElements: draft.text === item.text ? item.textElements : undefined} : item) } }));
    return true;
  },
  retryDraft: (sessionId, id) => {
    const rows = get().queues[sessionId];
    const row = rows?.find(item => item.id === id);
    if (!row || row.editing || row.injecting || row.steerCommand) return;
    set(s => ({ queues: { ...s.queues, [sessionId]: rows.map(item => item.id === id ? {...item, deliveryError:false, steerError:undefined} : item) } }));
    get().drain(sessionId);
  },
  removeDraft: (sessionId, id) => {
    const row = get().queues[sessionId]?.find(item => item.id === id);
    if (!row || row.injecting || row.steerCommand || row.editing) return false;
    get().remove(sessionId, id);
    get().drain(sessionId);
    return true;
  },

  clearDrafts: (sessionId) => {
    const rows = get().queues[sessionId] ?? EMPTY;
    const kept = rows.filter(row => row.injecting || row.steerCommand || row.editing);
    const removed = rows.length - kept.length;
    if (!removed) return 0;
    set(s => {
      const queues = { ...s.queues };
      if (kept.length) queues[sessionId] = kept;
      else delete queues[sessionId];
      return { queues };
    });
    return removed;
  },

  setInjecting: (sessionId, id, injecting) =>
    set((s) => {
      const cur = s.queues[sessionId];
      if (!cur?.some((message) => message.id === id)) return {};
      return {
        queues: {
          ...s.queues,
          [sessionId]: cur.map((message) =>
            message.id === id ? { ...message, injecting } : message
          ),
        },
      };
    }),

  setSteering: (sessionId, id, patch) => set((s) => {
    const entries = s.queues[sessionId];
    if (!entries?.some(item => item.id === id)) return {};
    return { queues: { ...s.queues, [sessionId]: entries.map(item => item.id === id ? { ...item, ...patch } : item) } };
  }),

  drain: (sessionId) => {
    // A row parked until the user retries or edits it does not hold back
    // the rows behind it. A row being edited or delivered into the current
    // turn does: where it ends up decides what goes first.
    const head = (get().queues[sessionId] ?? EMPTY).find((row) => !parkedRow(row));
    if (!head || !sendImpl) return;
    if (head.editing || head.injecting || head.steerCommand) return;
    // A restored row sends with its files or not at all; their read drains.
    if (head.attachmentsPending) return;
    // Still busy — the next running-task clear will call us again.
    if (useSessionStore.getState().runningTasks[sessionId]) return;
    // Stopped, but the server has not released the turn yet.
    if (stopHolds.has(sessionId)) return;
    // The previous send has not been acknowledged yet. sendChatMessage would
    // treat this one as a repeat of it and drop it, so wait: the ACK starts
    // that turn, and its clear drains again.
    if (hasPendingFirstAck(sessionId)) return;
    // The server clears its waiting hint when this turn is admitted.
    draining.add(sessionId);
    // Pop BEFORE sending: sendChatMessage re-enters the store (running
    // task, pending text) and a failed write re-queues at the head
    // below, so there is no window where the entry is both queued and
    // in flight.
    get().remove(sessionId, head.id);
    draining.delete(sessionId);
    const payload = queuedMessagePayload(head);
    const restore = (deliveryError = false) => set((s) => {
      const rows = s.queues[sessionId] ?? EMPTY;
      if (rows.some(item => item.id === head.id)) return {};
      // Back in front of the rows that were behind it; parked rows ahead of
      // it keep their place.
      const at = rows.findIndex((row) => !parkedRow(row));
      const index = at < 0 ? rows.length : at;
      return { queues: { ...s.queues, [sessionId]: [...rows.slice(0, index), {...head, deliveryError}, ...rows.slice(index)] } };
    });
    const ok = sendImpl({
      ...payload,
      ...(head.textElements?.length ? { textElements: head.textElements } : {}),
      // Text-only run_active uses requeueRejected; other pre-ACK failures
      // invoke this callback and require an explicit retry.
      onReject: () => restore(true),
      sessionId,
      thinking: head.thinking,
      webSearchEnabled: head.webSearchEnabled,
      serviceTier: head.serviceTier,
      background: head.background,
    });
    if (ok) return;
    // Socket closed mid-drain: retain the complete payload at the head.
    restore();
  },
}));

// Every write, the store's own actions and the helpers below alike, is saved
// and tells the server whether a queue waits.
useSendQueue.subscribe((state, previous) => {
  if (state.queues === previous.queues) return;
  saveQueues(state.queues);
  void syncQueuedAttachments(previous.queues, state.queues);
  hintGoalQueue(previous.queues, state.queues);
});

/** Give restored rows their files back, then let the session drain. A row
 *  whose files are gone keeps its text and waits for an explicit retry. */
export async function restorePendingAttachments(sessionId: string): Promise<void> {
  const pending = (useSendQueue.getState().queues[sessionId] ?? EMPTY).filter(row => row.attachmentsPending);
  for (const row of pending) {
    const files = await restoreQueuedAttachments(row.id);
    useSendQueue.setState((s) => {
      const rows = s.queues[sessionId];
      if (!rows?.some(item => item.id === row.id && item.attachmentsPending)) return {};
      return { queues: { ...s.queues, [sessionId]: rows.map(item => item.id !== row.id ? item
        : files ? { ...item, ...files, attachmentsPending: false }
          : { ...item, attachmentsPending: false, deliveryError: true }) } };
    });
  }
  if (pending.length) useSendQueue.getState().drain(sessionId);
}

// Rows a reload or restart left behind join this window's queues, and their
// sessions are asked for their run state: the reply confirms restored steering
// receipts and drains an idle session. Before the socket opens, its open
// handler asks for every session with a queue.
if (typeof window !== "undefined") {
  void adoptOrphanedQueues().then((adopted) => {
    const sessions = Object.keys(adopted);
    if (!sessions.length) return;
    useSendQueue.setState((s) => {
      const queues = { ...s.queues };
      for (const sid of sessions) {
        const ids = new Set((queues[sid] ?? EMPTY).map((row) => row.id));
        queues[sid] = [...(queues[sid] ?? EMPTY), ...adopted[sid].filter((row) => !ids.has(row.id))];
      }
      return { queues };
    });
    for (const sid of sessions) void restorePendingAttachments(sid);
    void import("@/lib/runtime-bridge/state").then(({ getSocket }) => {
      const socket = getSocket();
      if (socket?.readyState !== WebSocket.OPEN) return;
      for (const sid of sessions) socket.send(JSON.stringify({ action: "get_run_state", session_id: sid }));
    });
  });
}

/** Move one entry to the front without cancelling an execution. */
export function promoteToHead(sessionId: string, id: string): void {
  useSendQueue.setState((s) => {
    const cur = s.queues[sessionId];
    if (!cur) return {};
    const target = cur.find((q) => q.id === id);
    if (!target || cur[0]?.id === id) return {};
    return {
      queues: {
        ...s.queues,
        [sessionId]: [target, ...cur.filter((q) => q.id !== id)],
      },
    };
  });
}

/** Imperative enqueue for the composer (which is not a store consumer).
 *  Callers must supply the attachment snapshots when files are present. */
export function enqueueMessage(
  sessionId: string,
  draft: QueueDraft,
  attachmentCount = 0,
): string | null {
  if (attachmentCount > (draft.images?.length ?? 0) + (draft.docs?.length ?? 0)) return null;
  return useSendQueue.getState().enqueue(sessionId, draft);
}

/** A message on its way into the running turn: its steer request or the
 *  receipt it waits on is in flight and nothing has failed. The transcript
 *  shows it at the end of the running reply, not the queue card. */
export function steeringIntoTurn(row: QueuedMessage): boolean {
  return Boolean((row.injecting || row.steerCommand) && !row.steerError && !row.deliveryError && !row.editing);
}

/** The turn has written the steer this row was waiting on: the row is done. */
export function settleDeliveredSteer(sessionId: string, commandId: string): void {
  const row = queueFor(sessionId).find(item => item.steerCommand?.command_id === commandId);
  if (row) useSendQueue.getState().remove(sessionId, row.id);
}

/** Non-reactive read for imperative call sites. */
export function queueFor(sessionId: string): QueuedMessage[] {
  return useSendQueue.getState().queues[sessionId] ?? EMPTY;
}

/** Reconcile one retained queue after a WebSocket reconnect has loaded the
 *  server's authoritative session state. Active sessions wait for their real
 *  running_task_clear, so the slot takes the ids of the turn the server named:
 *  a clear only idles a slot it names. Idle sessions may retry the head
 *  immediately. A load whose run state is older than a frame already
 *  applied (`runFresh` false) still confirms steer receipts but leaves the
 *  running slot and the queue alone. */
export function reconcileAfterSessionLoad(
  sessionId: string,
  runActive: boolean,
  runTask?: { msg_id?: string; execution_id?: string; status_version?: number; op_kind?: string; op_id?: string } | null,
  runFresh = true,
): void {
  // Confirm restored receipts only after authoritative session hydration.
  if (queueFor(sessionId).some(row => row.steerCommand)) {
    void import("./steer-message").then(({steerQueuedMessage}) => {
      for (const row of queueFor(sessionId)) {
        if (row.steerCommand) void steerQueuedMessage(sessionId, row.id);
      }
    });
  }
  if (!runFresh) return;
  const sessions = useSessionStore.getState();
  const hold = stopHolds.get(sessionId);
  if (hold && runActive && (!hold.executionId || !runTask?.execution_id || runTask.execution_id === hold.executionId)) {
    // The stopped turn is still unwinding: keep the composer released and
    // ask again until the server reports the session idle.
    return;
  }
  if (hold) releaseStopHold(sessionId, false);
  if (runActive) {
    // 只在没有真实 task 时占位；已有 execution_id 的不覆盖（否则停止键
    // 只能发无 id 的 stop，服务端可能解析不到 execution）。
    const existing = sessions.runningTasks[sessionId];
    const version = typeof runTask?.status_version === "number" ? runTask.status_version : undefined;
    // A slot naming another turn is replaced too: after a branch switch
    // the selected branch's turn is a different one.
    const turnKey = (task?: { execution_id?: string; op_id?: string } | null) => task?.execution_id || task?.op_id || "";
    const otherTurn = !!existing && !!turnKey(existing) && !!turnKey(runTask) && turnKey(existing) !== turnKey(runTask);
    if (!existing || (!existing.msg_id && !existing.execution_id) || otherTurn) {
      sessions.setRunningTaskFor(sessionId, {
        session_id: sessionId,
        msg_id: runTask?.msg_id || "",
        ...(runTask?.execution_id ? { execution_id: runTask.execution_id } : {}),
        ...(runTask?.execution_id && version !== undefined ? { status_version: version } : {}),
        // An operation with no execution (/compact, a rewind) is stopped by its op_id.
        ...(runTask?.op_kind ? { op_kind: runTask.op_kind } : {}),
        ...(runTask?.op_id ? { op_id: runTask.op_id } : {}),
      });
    } else if (
      version !== undefined && runTask?.execution_id
      && existing.execution_id === runTask.execution_id
      && typeof existing.status_version !== "number"
    ) {
      // The slot knew the turn but not its version (a reconnect handed it
      // over without one): keep what the server says now.
      sessions.setRunningTaskFor(sessionId, { ...existing, status_version: version }, "never");
    }
    return;
  }
  const wasRunning = Boolean(sessions.runningTasks[sessionId]);
  sessions.setRunningTaskFor(sessionId, null);
  if (!wasRunning) {
    queueMicrotask(() => useSendQueue.getState().drain(sessionId));
  }
}

const STOP_CHECK_MS = [300, 600, 1000, 2000];
const STOP_HOLD_MAX_CHECKS = 20;

/** Stop releases the composer at once, but the server keeps the turn until
 *  its run unwinds; a queued message sent before then is refused with
 *  `run_active`. Hold the drain for `sessionId` until the server reports
 *  the session idle (a `run_state`/session load through
 *  reconcileAfterSessionLoad, or a running_task_clear through
 *  releaseStopHold), asking it on a short timer meanwhile. */
export function holdDrainAfterStop(sessionId: string, executionId?: string): void {
  const prior = stopHolds.get(sessionId);
  if (prior?.timer !== undefined) clearTimeout(prior.timer);
  const hold: { executionId?: string; timer?: ReturnType<typeof setTimeout>; checks: number } = {
    executionId: executionId || undefined, checks: 0,
  };
  stopHolds.set(sessionId, hold);
  const check = () => {
    if (stopHolds.get(sessionId) !== hold) return;
    if (hold.checks >= STOP_HOLD_MAX_CHECKS) {
      // Never hold a queue for good on a server that does not answer; a
      // text row refused with run_active is re-queued by the stream.
      releaseStopHold(sessionId);
      return;
    }
    hold.checks += 1;
    void import("@/lib/runtime-bridge/state").then(({ getSocket }) => {
      if (stopHolds.get(sessionId) !== hold) return;
      const socket = getSocket();
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: "get_run_state", session_id: sessionId }));
      }
      hold.timer = setTimeout(check, STOP_CHECK_MS[Math.min(hold.checks, STOP_CHECK_MS.length - 1)]);
    }).catch(() => releaseStopHold(sessionId));
  };
  hold.timer = setTimeout(check, STOP_CHECK_MS[0]);
}

/** The server released the stopped turn: let the queue go. */
export function releaseStopHold(sessionId: string, drain = true): void {
  const hold = stopHolds.get(sessionId);
  if (!hold) return;
  if (hold.timer !== undefined) clearTimeout(hold.timer);
  stopHolds.delete(sessionId);
  if (drain) queueMicrotask(() => useSendQueue.getState().drain(sessionId));
}

/** Whether a Stop in this window still waits for the server to release
 *  the session's turn. */
export function stopPending(sessionId: string): boolean {
  return stopHolds.has(sessionId);
}

const RUN_STATE_RECHECK_MS = 1500;
const runStateRechecks = new Set<string>();

/** A running slot with neither id (an active run reported without them, a
 *  legacy setRunning(true)) matches no running_task_clear, so the session
 *  would look busy for good. The session store calls this for every such
 *  write: ask the server again until the reply names the turn or reports
 *  the session idle. A just-sent message holds the same slot until its ack
 *  and is left alone. */
export function recheckRunState(sessionId: string): void {
  if (runStateRechecks.has(sessionId)) return;
  runStateRechecks.add(sessionId);
  setTimeout(() => {
    runStateRechecks.delete(sessionId);
    const task = useSessionStore.getState().runningTasks[sessionId];
    if (!task || task.msg_id || task.execution_id || hasPendingFirstAck(sessionId)) return;
    void import("@/lib/runtime-bridge/state").then(({ getSocket }) => {
      const socket = getSocket();
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: "get_run_state", session_id: sessionId }));
      } else recheckRunState(sessionId);
    });
  }, RUN_STATE_RECHECK_MS);
}

/** Turn settings of the last outgoing send per session, so a rejected
 *  frame can be re-queued with the settings it was sent under — the
 *  WS error frame only echoes `retry_query`, not the knobs. */
const lastSettings: Record<string, Omit<QueueDraft, "text">> = {};

export function rememberSendSettings(
  sessionId: string,
  settings: Omit<QueueDraft, "text">,
): void {
  lastSettings[sessionId] = settings;
}

/** Send `text` as the user's next message in `sessionId` from something
 *  shown in the transcript (a button in a reply's visual): it takes the
 *  queue, so it goes out now when the conversation is idle and waits its
 *  turn otherwise, with the settings of this window's last send there. */
export function sendFromTranscript(sessionId: string, text: string): string | null {
  const settings = lastSettings[sessionId] ?? {
    thinking: "medium",
    webSearchEnabled: false,
    background: false,
  };
  const id = enqueueMessage(sessionId, { ...settings, text });
  if (id) useSendQueue.getState().drain(sessionId);
  return id;
}

/** Re-queue a message the backend rejected with `run_active`, at the
 *  FRONT (it was typed before everything still waiting). The race this
 *  covers: the client believed the session was idle, the backend knew
 *  better. Silent recovery — no error bubble, no lost text. */
export function requeueRejected(sessionId: string, text: string): void {
  const settings = lastSettings[sessionId] ?? {
    thinking: "medium",
    webSearchEnabled: false,
    background: false,
  };
  useSendQueue.setState((s) => ({
    queues: {
      ...s.queues,
      [sessionId]: [
        { ...settings, id: `q${++seq}_retry`, text, queuedAt: Date.now() },
        ...(s.queues[sessionId] ?? EMPTY),
      ],
    },
  }));
}

/** Stop holds every steering attempt, including pre-admission snapshot reads.
 * Posted commands remain intact until their receipt establishes delivery. */
export function holdSteeringForStop(sessionId: string): void {
  const queue = useSendQueue.getState();
  for (const row of queueFor(sessionId)) {
    if (row.injecting || row.steerCommand) queue.setSteering(sessionId, row.id, {steerError:"cancelled"});
  }
}
