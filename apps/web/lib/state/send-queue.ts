"use client";

/**
 * Client-side send queue — "type while it runs".
 *
 * A message typed while the session's turn is still running is parked
 * here instead of being dropped (the backend rejects a concurrent
 * `chat` with `code:"run_active"`). The entries render as dimmed
 * "queued" bubbles under the transcript and drain one at a time: when
 * a session's running task clears, the head entry is sent as an
 * ordinary turn.
 *
 * Deliberately client-memory only — no persistence, no server round
 * trip. A reload drops the queue, which is the honest reading of
 * "these were never sent". Keyed by session so switching tabs or
 * running two panes never crosses streams.
 */

import { create } from "zustand";

// ponytail: `send-chat-message` imports back into this module (it records the
// per-session turn settings), so the send function is reached through a
// registered callback rather than a static import. One indirection beats
// an import cycle between a store and the socket writer.
import { useSessionStore } from "@/lib/session-store";

type SendFn = (args: {
  text: string;
  sessionId: string | null;
  thinking: string;
  toolsEnabled: boolean;
  toolsProfile?: string;
  webSearchEnabled: boolean;
  serviceTier?: string;
  background?: boolean;
}) => boolean;

let sendImpl: SendFn | null = null;

/** Wired once by `send-chat-message` on module load. */
export function registerChatSender(fn: SendFn): void {
  sendImpl = fn;
}

export interface QueuedMessage {
  id: string;
  text: string;
  /** Turn settings captured at type-time, replayed verbatim on send. */
  thinking: string;
  toolsEnabled: boolean;
  toolsProfile?: string;
  webSearchEnabled: boolean;
  serviceTier?: string;
  background: boolean;
  queuedAt: number;
  /** A steer request is in flight; drain must wait for its acknowledgement. */
  injecting?: boolean;
}

/** What the composer hands over; the store stamps id + queuedAt. */
export type QueueDraft = Omit<QueuedMessage, "id" | "queuedAt">;

interface SendQueueState {
  /** sessionId → entries, in send order. */
  queues: Record<string, QueuedMessage[]>;
  enqueue: (sessionId: string, draft: QueueDraft) => string;
  remove: (sessionId: string, id: string) => void;
  setInjecting: (sessionId: string, id: string, injecting: boolean) => void;
  /** Send the head entry if the session is idle. No-op otherwise. */
  drain: (sessionId: string) => void;
}

const EMPTY: QueuedMessage[] = [];

let seq = 0;

export const useSendQueue = create<SendQueueState>((set, get) => ({
  queues: {},

  enqueue: (sessionId, draft) => {
    const id = `q${++seq}_${Date.now().toString(36)}`;
    set((s) => ({
      queues: {
        ...s.queues,
        [sessionId]: [
          ...(s.queues[sessionId] ?? EMPTY),
          { ...draft, id, queuedAt: Date.now() },
        ],
      },
    }));
    return id;
  },

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

  drain: (sessionId) => {
    const head = (get().queues[sessionId] ?? EMPTY)[0];
    if (!head || !sendImpl) return;
    if (head.injecting) return;
    // Still busy — the next running-task clear will call us again.
    if (useSessionStore.getState().runningTasks[sessionId]) return;
    // Pop BEFORE sending: sendChatMessage re-enters the store (running
    // task, pending text) and a failed write re-queues at the head
    // below, so there is no window where the entry is both queued and
    // in flight.
    get().remove(sessionId, head.id);
    const ok = sendImpl({
      text: head.text,
      sessionId,
      thinking: head.thinking,
      toolsEnabled: head.toolsEnabled,
      toolsProfile: head.toolsProfile ?? "__agent__",
      webSearchEnabled: head.webSearchEnabled,
      serviceTier: head.serviceTier,
      background: head.background,
    });
    if (ok) return;
    // Socket closed mid-drain — put it back at the FRONT so order
    // survives the reconnect.
    set((s) => ({
      queues: {
        ...s.queues,
        [sessionId]: [head, ...(s.queues[sessionId] ?? EMPTY)],
      },
    }));
  },
}));

/** Move one entry to the front, keeping the others in order behind it.
 *  Backs the queued row's "stop current and send now": the entry becomes
 *  what the next drain ships, without discarding the ones before it. */
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
 *  The queue intentionally carries plain text only. Returning null keeps an
 *  attached draft intact instead of separating its caption from its files. */
export function enqueueMessage(
  sessionId: string,
  draft: QueueDraft,
  attachmentCount = 0,
): string | null {
  if (attachmentCount > 0) return null;
  return useSendQueue.getState().enqueue(sessionId, draft);
}

/** Non-reactive read for imperative call sites. */
export function queueFor(sessionId: string): QueuedMessage[] {
  return useSendQueue.getState().queues[sessionId] ?? EMPTY;
}

/** Reconcile one retained queue after a WebSocket reconnect has loaded the
 *  server's authoritative session state. Active sessions wait for their real
 *  running_task_clear; idle sessions may retry the head immediately. */
export function reconcileAfterSessionLoad(
  sessionId: string,
  runActive: boolean,
): void {
  const sessions = useSessionStore.getState();
  if (runActive) {
    // 只在没有真实 task 时占位；已有 execution_id 的不覆盖（否则停止键
    // 只能发无 id 的 stop，服务端可能解析不到 execution）。
    const existing = sessions.runningTasks[sessionId];
    if (!existing || (!existing.msg_id && !existing.execution_id)) {
      sessions.setRunningTaskFor(sessionId, { session_id: sessionId, msg_id: "" });
    }
    return;
  }
  const wasRunning = Boolean(sessions.runningTasks[sessionId]);
  sessions.setRunningTaskFor(sessionId, null);
  if (!wasRunning) {
    queueMicrotask(() => useSendQueue.getState().drain(sessionId));
  }
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

/** Re-queue a message the backend rejected with `run_active`, at the
 *  FRONT (it was typed before everything still waiting). The race this
 *  covers: the client believed the session was idle, the backend knew
 *  better. Silent recovery — no error bubble, no lost text. */
export function requeueRejected(sessionId: string, text: string): void {
  const settings = lastSettings[sessionId] ?? {
    thinking: "medium",
    toolsEnabled: true,
    toolsProfile: "__agent__",
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
