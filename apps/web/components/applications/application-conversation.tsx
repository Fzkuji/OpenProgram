"use client";
/**
 * The owner's composer inside an application window (capability `chat`).
 *
 * The window keeps conversations of its own (Auto Research: one per project
 * and task, `state.conversation` on the server) and draws their messages
 * itself; the composer is the real one, drawn here over the place the window
 * leaves for it (`chat.place`), so typing, attachments, the model and the
 * permission controls are the chat's own. What the window says the next
 * message is about rides each message (`lib/chat/send/application-notes`).
 *
 * The window hears two events through its port: `chat`, the conversation's
 * messages in plain form and whether a reply is running, and `composer`, the
 * composer's height so the window can keep its own content clear of it.
 */
import { useEffect, useRef, useSyncExternalStore } from "react";
import { Composer } from "@/components/chat/composer";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { selectViewRows, useSessionStore } from "@/lib/session-store";
import type { ChatMsg } from "@/lib/session-store/types";

/** Windows that hold a conversation of their own: the right sidebar steps
 *  aside while one is in front, since the window is the conversation. */
const ownChat = new Set<string>();
const ownChatListeners = new Set<() => void>();
let ownChatVersion = 0;
export function setOwnConversation(instanceId: string, on: boolean): void {
  if (ownChat.has(instanceId) === on) return;
  if (on) ownChat.add(instanceId); else ownChat.delete(instanceId);
  ownChatVersion++;
  ownChatListeners.forEach((listener) => listener());
}
export function useHoldsOwnConversation(instanceId: string | null | undefined): boolean {
  useSyncExternalStore(
    (listener) => { ownChatListeners.add(listener); return () => { ownChatListeners.delete(listener); }; },
    () => ownChatVersion, () => ownChatVersion,
  );
  return Boolean(instanceId && ownChat.has(instanceId));
}

export interface ComposerPlace { left: number; right: number; bottom: number }

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  status?: string;
  tools: { tool: string; status: string; input: string }[];
}

const FEED_LIMIT = 40;
/** User rows nobody typed (ChatMsg.source). */
const SYNTHETIC = new Set(["job_followup", "merge_turn", "agent_spawn"]);

function toolInput(raw: string): string {
  try {
    const args = JSON.parse(raw);
    if (args && typeof args === "object") {
      const first = args.path ?? args.file_path ?? args.command ?? args.pattern ?? args.query ?? args.url;
      if (typeof first === "string") return first.slice(0, 160);
    }
  } catch { /* not JSON: shown as sent */ }
  return raw.slice(0, 160);
}

/** The conversation as the window draws it: its last messages, plain. */
export function conversationFeed(sessionId: string): { messages: ConversationMessage[]; running: boolean } {
  const state = useSessionStore.getState();
  const rows = selectViewRows(sessionId)(state);
  const order = state.messageOrder[sessionId] ?? [];
  const messages: ConversationMessage[] = [];
  for (const id of order.slice(-FEED_LIMIT * 2)) {
    const row: ChatMsg | undefined = rows[id];
    if (!row || (row.role !== "user" && row.role !== "assistant") || row.display === "runtime") continue;
    if (row.role === "user" && row.source && SYNTHETIC.has(row.source)) continue;
    messages.push({
      id: row.id,
      role: row.role,
      text: row.content || "",
      status: row.status,
      tools: (row.tools ?? []).map((call) => ({ tool: call.tool, status: call.status, input: toolInput(call.input || "") })),
    });
  }
  return { messages: messages.slice(-FEED_LIMIT), running: Boolean(state.runningTasks[sessionId]) };
}

/** Tells the window each change of the conversation, at most once a frame. */
export function useConversationFeed(sessionId: string | null, post: (event: string, value: unknown) => void) {
  const postRef = useRef(post);
  postRef.current = post;
  useEffect(() => {
    if (!sessionId) return;
    let frame = 0;
    let last: unknown[] | null = null;
    const send = () => {
      frame = 0;
      postRef.current("chat", { session_id: sessionId, ...conversationFeed(sessionId) });
    };
    const watch = () => {
      const s = useSessionStore.getState();
      const now = [s.messageOrder[sessionId], s.rowsByView[sessionId], s.overlaysByView?.[sessionId], s.runningTasks[sessionId]];
      if (last && now.every((value, index) => value === last![index])) return;
      last = now;
      if (!frame) frame = requestAnimationFrame(send);
    };
    watch();
    const stop = useSessionStore.subscribe(watch);
    return () => { stop(); if (frame) cancelAnimationFrame(frame); };
  }, [sessionId]);
}

/** The composer over the window's place for it; its height goes back to the window. */
export function ApplicationComposer({ sessionId, place, hint, onHeight }: {
  sessionId: string; place: ComposerPlace; hint?: string; onHeight: (height: number) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const heightRef = useRef(onHeight);
  heightRef.current = onHeight;
  useEffect(() => {
    // The composer floats at the bottom of its container, as it does over a chat,
    // so the host is a zero-height line at the place's bottom and the composer's own
    // box is what is measured.
    const element = host.current?.querySelector<HTMLElement>("[data-composer-input-area]");
    if (!element) return;
    let last = -1;
    const observer = new ResizeObserver(() => {
      const height = Math.round(element.getBoundingClientRect().height);
      if (height !== last) { last = height; heightRef.current(height); }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [sessionId]);
  return (
    <div
      ref={host}
      className="application-composer-host"
      style={{
        position: "absolute", left: place.left, width: Math.max(0, place.right - place.left),
        top: place.bottom, height: 0, zIndex: 5,
        // The application sizes the place, so the composer fills it instead of
        // stopping at the chat column's cap.
        ["--chat-column-gutter" as string]: "24px",
        ["--chat-column-max" as string]: "100vw",
      }}
    >
      <SessionScopeProvider sid={sessionId}>
        <Composer sessionId={sessionId} placeholder={hint || undefined} />
      </SessionScopeProvider>
    </div>
  );
}
