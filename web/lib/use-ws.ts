"use client";

/**
 * Chat WebSocket lifecycle — React owner.
 *
 * Slice A of the WS-layer migration: the socket's open / reconnect /
 * keepalive / teardown used to live at the bottom of the legacy
 * `init.js`. They move here so the connection is tied to React mount
 * and future slices can dispatch straight into the store.
 *
 * The message DISPATCH is still the legacy `window.handleMessage`
 * (init.js) for now — slice E rewrites that. So this hook just owns
 * the socket and pumps each frame into the existing dispatcher; it
 * also keeps `window.ws` assigned so the not-yet-migrated legacy code
 * (and the React `wsSend` helpers) keep working unchanged.
 */
import { useEffect } from "react";

import {
  handleAttemptSwitched,
  loadSessionData,
  onBranchCheckedOut,
  onBranchesListMessage,
  onChannelAccountsMessage,
} from "./legacy/conversations";
import {
  handleRunningTask,
  handleRunningTaskClear,
  handleSessionsList,
  initChatPage,
  wsHandleChatAck,
  wsHandleChatResponse,
  wsHandleStatus,
} from "./legacy/chat-handlers";

interface WsWindow {
  ws?: WebSocket | null;
  handleMessage?: (msg: unknown) => void;
  updateStatus?: (s: string) => void;
  loadAgentSettings?: () => void;
  currentSessionId?: string | null;
  // Legacy handlers the hook dispatch calls until each is migrated.
  _handleRunningTask?: (data: unknown) => void;
  updateProviderBadge?: (data: unknown) => void;
  loadProviders?: () => void;
  addSystemMessage?: (text: string) => void;
  formatProviderLabel?: (data: unknown) => string;
  updateAgentBadges?: () => void;
  _agentSettings?: { chat?: Record<string, unknown>; exec?: Record<string, unknown> };
  trees?: unknown;
  availableFunctions?: unknown;
  conversations?: Record<string, Record<string, unknown>>;
  updateTreeData?: (data: unknown) => void;
  loadProgramsMeta?: () => Promise<unknown>;
  renderFunctions?: () => void;
  renderSessions?: () => void;
  handleAttemptSwitched?: (data: unknown) => void;
  _onChannelAccountsMessage?: (data: unknown) => void;
  _onBranchesListMessage?: (data: unknown) => void;
  _onBranchCheckedOut?: (data: unknown) => void;
  loadSessionData?: (data: unknown) => void;
  _handleSessionsList?: (data: unknown) => void;
  refreshStatusSource?: () => void;
  refreshChannelBadge?: () => void;
  __applyChatWsMessage?: (msg: unknown) => void;
  _wsHandleChatAck?: (data: unknown) => void;
  _wsHandleChatResponse?: (data: unknown) => void;
  _wsHandleStatus?: (msg: unknown) => void;
}

export function useWS(): void {
  useEffect(() => {
    const w = window as unknown as WsWindow;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    /** React-side dispatch for migrated message types. Returns true if
     *  the message was handled here — false means fall through to the
     *  legacy `window.handleMessage`. Slice E migrates types into here
     *  one batch at a time until the legacy dispatcher is empty. */
    function dispatch(msg: {
      type?: string;
      data?: Record<string, unknown>;
    }): boolean {
      const d = msg.data;
      switch (msg.type) {
        case "pong":
          return true;
        case "chat_ack":
          // Mirror into the React message store, then the
          // session/badge bookkeeping.
          try {
            w.__applyChatWsMessage?.(msg);
          } catch (err) {
            console.error("[useWS] reducer error:", err);
          }
          wsHandleChatAck((d ?? {}) as never);
          return true;
        case "chat_response":
          try {
            w.__applyChatWsMessage?.(msg);
          } catch (err) {
            console.error("[useWS] reducer error:", err);
          }
          wsHandleChatResponse((d ?? {}) as never);
          return true;
        case "status":
          wsHandleStatus(msg as never);
          return true;
        case "session_reload": {
          const sid = d?.session_id as string | undefined;
          if (sid && sid === w.currentSessionId) {
            socket?.send(
              JSON.stringify({ action: "load_session", session_id: sid }),
            );
          }
          return true;
        }
        case "branch_renamed":
        case "branch_name_deleted":
        case "branch_deleted": {
          const sid = d?.session_id as string | undefined;
          if (sid) {
            socket?.send(
              JSON.stringify({ action: "list_branches", session_id: sid }),
            );
          }
          return true;
        }
        case "running_task":
          handleRunningTask(d);
          return true;
        case "running_task_clear":
          handleRunningTaskClear(
            (d as { session_id?: string } | undefined)?.session_id,
          );
          return true;
        case "provider_info":
        case "provider_changed":
          w.updateProviderBadge?.(d);
          w.loadProviders?.();
          if (msg.type === "provider_changed") {
            w.addSystemMessage?.(
              "Switched to " + (w.formatProviderLabel?.(d) ?? ""),
            );
          }
          return true;
        case "agent_settings_changed": {
          const as = w._agentSettings;
          if (as) {
            if (d?.chat) as.chat = d.chat as Record<string, unknown>;
            if (d?.exec) as.exec = d.exec as Record<string, unknown>;
          }
          w.updateAgentBadges?.();
          w.loadAgentSettings?.();
          return true;
        }
        case "chat_session_update":
          if (d?.session_id && w._agentSettings?.chat) {
            w._agentSettings.chat.session_id = d.session_id;
            w.updateAgentBadges?.();
          }
          return true;
        case "full_tree":
          // Tree data rides the store via `tree_update` now — the
          // legacy `trees` global is no longer read by the UI.
          return true;
        case "event":
          return true;
        case "functions_list":
          w.availableFunctions = d || [];
          w.loadProgramsMeta?.().then(() => w.renderFunctions?.());
          return true;
        case "history_list": {
          const list = (d as unknown as { id: string; title?: string }[]) || [];
          const convs = w.conversations;
          if (convs) {
            for (const c of list) {
              if (!convs[c.id]) {
                convs[c.id] = { id: c.id, title: c.title, messages: [] };
              }
            }
          }
          w.renderSessions?.();
          return true;
        }
        case "attempt_switched":
          handleAttemptSwitched(d as never);
          return true;
        case "channel_accounts":
          onChannelAccountsMessage(d as never);
          return true;
        case "branches_list":
          onBranchesListMessage(d as never);
          return true;
        case "branch_checked_out":
          onBranchCheckedOut(d as never);
          return true;
        case "session_loaded":
          loadSessionData(d as never);
          return true;
        case "sessions_list":
          handleSessionsList((d ?? []) as never);
          return true;
        case "session_channel_updated": {
          const sid = d?.session_id as string | undefined;
          const conv = sid ? w.conversations?.[sid] : undefined;
          if (d?.ok && conv) {
            conv.channel = (d.channel as string) || null;
            conv.account_id = (d.account_id as string) || null;
            conv.peer = (d.peer as string) || null;
            w.renderSessions?.();
            if (sid === w.currentSessionId) {
              w.refreshStatusSource?.();
              w.refreshChannelBadge?.();
            }
          }
          return true;
        }
        default:
          return false;
      }
    }

    function connect(): void {
      if (stopped) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(proto + "//" + location.host + "/ws");
      w.ws = socket;

      socket.onopen = () => {
        w.updateStatus?.("connected");
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        // currentSessionId is derived from the URL by state.js / the
        // app-shell route effect — send agent_settings + the initial
        // session load so badges + transcript reflect the right conv.
        w.loadAgentSettings?.();
        socket?.send(JSON.stringify({ action: "list_sessions" }));
        if (w.currentSessionId) {
          socket?.send(
            JSON.stringify({
              action: "load_session",
              session_id: w.currentSessionId,
            }),
          );
        }
      };

      socket.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data) as {
            type?: string;
            data?: { session_id?: string };
          };
          if (!dispatch(msg)) {
            // Not yet migrated — hand to the legacy dispatcher.
            w.handleMessage?.(msg);
          }
        } catch (err) {
          console.error("[useWS] onmessage parse error:", err);
        }
      };

      socket.onclose = () => {
        w.updateStatus?.("disconnected");
        if (!stopped) reconnectTimer = setTimeout(connect, 2000);
      };

      socket.onerror = () => socket?.close();
    }

    // The shared legacy scripts (state.js / helpers.js / ui.js /
    // providers.js) are fetched + injected asynchronously by AppShell.
    // state.js runs `var ws = null` at the global scope, so connecting
    // before it loads would have the socket reference clobbered right
    // after assignment. initChatPage() also depends on shared-script
    // globals (loadProviders / setWelcomeVisible). So: wait for
    // AppShell to publish `__sharedScriptsReady`, await it, then init.
    async function start(): Promise<void> {
      const sw = window as unknown as { __sharedScriptsReady?: Promise<void> };
      while (!stopped && !sw.__sharedScriptsReady) {
        await new Promise((r) => setTimeout(r, 20));
      }
      if (stopped) return;
      try {
        await sw.__sharedScriptsReady;
      } catch {
        /* ignore — connect anyway */
      }
      if (stopped) return;
      initChatPage();
      connect();
    }
    start();

    const keepalive = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
    }, 30000);

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(keepalive);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      if (w.ws === socket) w.ws = null;
    };
  }, []);
}
