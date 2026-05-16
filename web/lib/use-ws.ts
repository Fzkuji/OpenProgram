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

interface WsWindow {
  ws?: WebSocket | null;
  handleMessage?: (msg: unknown) => void;
  updateStatus?: (s: string) => void;
  loadAgentSettings?: () => void;
  currentSessionId?: string | null;
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
      data?: { session_id?: string };
    }): boolean {
      switch (msg.type) {
        case "pong":
          return true;
        case "session_reload": {
          const sid = msg.data?.session_id;
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
          const sid = msg.data?.session_id;
          if (sid) {
            socket?.send(
              JSON.stringify({ action: "list_branches", session_id: sid }),
            );
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

    // The dispatcher (`window.handleMessage`) is defined by the legacy
    // `init.js` page script, injected asynchronously by PageShell.
    // Poll briefly until it exists, then open the socket.
    function start(): void {
      if (stopped) return;
      if (typeof w.handleMessage === "function") connect();
      else setTimeout(start, 50);
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
