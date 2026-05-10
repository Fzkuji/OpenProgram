"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useSessionStore, type ChatMsg, type ConvSummary } from "./session-store";

interface WSContextValue {
  send: (payload: unknown) => boolean;
}

const WSContext = createContext<WSContextValue | null>(null);

export function useWS() {
  const ctx = useContext(WSContext);
  if (!ctx) throw new Error("useWS must be used inside <WSProvider>");
  return ctx;
}

interface WSMessage {
  type: string;
  data?: unknown;
}

interface ChatResponseData {
  type: string;
  content?: string;
  session_id?: string;
  msg_id?: string;
  function?: string;
  display?: "runtime" | "normal";
  cancelled?: boolean;
  delta?: string;
  text?: string;
  title?: string;
}

interface ConversationLoaded {
  id: string;
  title: string;
  messages?: Array<{
    role: "user" | "assistant" | "system";
    id?: string;
    content?: string;
    function?: string;
    display?: "runtime" | "normal";
    timestamp?: number;
    attempts?: { content: string; timestamp: number }[];
    current_attempt?: number;
  }>;
  provider_info?: { provider?: string; model?: string; type?: string };
}

/**
 * Single WebSocket to /ws, shared across the whole app.
 * Auto-reconnects on close. Pushes all server events into session-store.
 */
export function WSProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    setWsStatus,
    setConversations,
    upsertConversation,
    removeConversation,
    clearConversations,
    setMessages,
    appendMessage,
    updateMessage,
    setRunningTask,
    setPaused,
    setProviderInfo,
  } = useSessionStore.getState();

  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;
      setWsStatus("connecting");

      ws.onopen = () => setWsStatus("open");
      ws.onclose = () => {
        setWsStatus("closed");
        if (!cancelled) reconnectTimer.current = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (ev) => {
        let msg: WSMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        handleMessage(msg);
      };
    }

    function handleMessage(msg: WSMessage) {
      const store = useSessionStore.getState();

      switch (msg.type) {
        case "history_list": {
          const list = (msg.data as ConvSummary[]) ?? [];
          setConversations(list);
          break;
        }
        case "sessions_list": {
          const list = (msg.data as ConvSummary[]) ?? [];
          setConversations(list);
          break;
        }
        case "provider_info": {
          setProviderInfo(msg.data as ConvState_provider);
          break;
        }
        case "running_task": {
          const d = msg.data as {
            session_id: string;
            msg_id: string;
            func_name?: string;
            started_at?: number;
            paused?: boolean;
          };
          setRunningTask({
            session_id: d.session_id,
            msg_id: d.msg_id,
            func_name: d.func_name,
            started_at: d.started_at,
          });
          if (typeof d.paused === "boolean") setPaused(d.paused);
          break;
        }
        case "chat_ack": {
          const d = msg.data as { session_id: string; msg_id: string };
          if (!store.currentSessionId) store.setCurrentConv(d.session_id);
          // ensure conversation exists in list
          if (!store.conversations[d.session_id]) {
            upsertConversation({
              id: d.session_id,
              title: "Untitled",
              created_at: Date.now() / 1000,
            });
          }
          // prepare empty assistant placeholder
          appendMessage(d.session_id, {
            id: d.msg_id + "_reply",
            role: "assistant",
            content: "",
            status: "streaming",
          });
          setRunningTask({ session_id: d.session_id, msg_id: d.msg_id });
          break;
        }
        case "chat_response": {
          const d = msg.data as ChatResponseData;
          if (!d || !d.session_id || !d.msg_id) return;
          const sessionId = d.session_id;
          const replyId = d.msg_id + "_reply";

          if (d.type === "status") {
            updateMessage(sessionId, replyId, {
              role: "system",
              content: d.content ?? "",
              status: "pending",
            });
          } else if (d.type === "stream_event") {
            const delta = d.delta ?? d.content ?? "";
            if (delta) {
              const cur =
                useSessionStore.getState().messagesById[replyId]?.content ?? "";
              updateMessage(sessionId, replyId, {
                role: "assistant",
                content: cur + delta,
                status: "streaming",
              });
            }
          } else if (d.type === "result") {
            updateMessage(sessionId, replyId, {
              role: "assistant",
              content: d.content ?? "",
              status: d.cancelled ? "cancelled" : "done",
              function: d.function,
              display: d.display,
            });
            setRunningTask(null);
            setPaused(false);
          } else if (d.type === "error") {
            updateMessage(sessionId, replyId, {
              role: "assistant",
              content: d.content ?? "",
              status: "error",
              function: d.function,
              display: d.display,
            });
            setRunningTask(null);
            setPaused(false);
          } else if (d.type === "cancelled") {
            updateMessage(sessionId, replyId, { status: "cancelled" });
            setRunningTask(null);
            setPaused(false);
          } else if (d.type === "conversation_title") {
            if (d.title) upsertConversation({ id: sessionId, title: d.title });
          } else if (d.type === "tree_update") {
            const td = msg.data as { session_id?: string; tree?: unknown };
            if (td.session_id && td.tree) {
              useSessionStore
                .getState()
                .setTree(td.session_id, td.tree as never);
            }
          } else if (d.type === "context_stats") {
            const cs = msg.data as {
              session_id?: string;
              chat?: {
                input_tokens?: number;
                output_tokens?: number;
                cache_read?: number;
              };
              context_window?: number | null;
            };
            if (cs.session_id) {
              useSessionStore.getState().setContextStats(
                cs.session_id,
                cs.chat
                  ? {
                      input: cs.chat.input_tokens,
                      output: cs.chat.output_tokens,
                      cache_read: cs.chat.cache_read,
                    }
                  : null,
                cs.context_window ?? undefined,
              );
            }
          }
          break;
        }
        case "session_loaded": {
          const d = msg.data as ConversationLoaded;
          upsertConversation({ id: d.id, title: d.title });
          const msgs: ChatMsg[] = (d.messages ?? []).map((m, i) => ({
            id: m.id ?? `msg-${i}`,
            role: m.role,
            content: m.content ?? "",
            status: "done",
            function: m.function,
            display: m.display,
            timestamp: m.timestamp,
            attempts: m.attempts,
            current_attempt: m.current_attempt,
          }));
          setMessages(d.id, msgs);
          if (d.provider_info) setProviderInfo(d.provider_info);
          break;
        }
        case "conversation_deleted": {
          const d = msg.data as { session_id: string };
          if (d?.session_id) removeConversation(d.session_id);
          break;
        }
        case "conversations_cleared": {
          clearConversations();
          break;
        }
        case "pause_state": {
          const d = msg.data as { paused: boolean };
          setPaused(!!d?.paused);
          break;
        }
      }
    }

    connect();
    const interval = setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 20_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = useCallback((payload: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      return true;
    }
    return false;
  }, []);

  return <WSContext.Provider value={{ send }}>{children}</WSContext.Provider>;
}

type ConvState_provider = { provider?: string; model?: string; type?: string };
