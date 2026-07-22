"use client";

import { useSessionStore } from "@/lib/session-store";

/**
 * Chat send path — owned by the React composer.
 *
 * Slice F: this used to delegate to the legacy `window.sendMessage`
 * (chat.js), which built the user bubble / assistant placeholder DOM
 * before writing the socket. Those bubbles are now rendered by the
 * React message store — the chat-stream reducer's `handleAck` builds
 * the user turn from the per-session pending-text map. So this just writes
 * the WS payload directly and flips the visible run state.
 *
 * What still rides `window.*`:
 *   - `setWelcomeVisible(false)` — hides the React <WelcomeScreen />
 *     immediately (before the ack round-trip).
 *   - `setRunning(true)` — legacy run flag (ui.js).
 *   - `_lastRunCommand` — retry helpers' fallback (chat.js retryCurrentBlock).
 *   - `_pendingChannelChoice` — first-message channel attach (channel-menu).
 *   - `_execThinkingEffort` — exec-side effort, set by the agent settings.
 */

/** One inline binary attachment delivered alongside the user message.
 *  ``data`` is raw base64 (no ``data:image/...;base64,`` prefix) so the
 *  WS payload matches what ``TurnRequest.attachments`` expects on the
 *  Python side (see ``openprogram/agent/dispatcher.py``). */
export interface ChatAttachment {
  /** "image" → inlined as an ImageContent block for the model;
   *  "document" → saved to the session workdir by the backend so the
   *  agent's file tools can read it (the path is injected into the
   *  message text). */
  type: "image" | "document";
  data: string;
  media_type: string;
  filename?: string;
}

interface SendMessageBridgeArgs {
  text: string;
  /** Real or provisional chat key. Drafts send their local_* key so the
   *  existing server protocol can acknowledge the correct tab. */
  sessionId: string | null;
  thinking: string;
  toolsEnabled: boolean;
  webSearchEnabled: boolean;
  /** Per-turn speed tier — "priority" (Fast) or undefined (provider
   *  default). Sent as ``service_tier`` so the backend forwards it to
   *  the provider request body. */
  serviceTier?: string;
  attachments?: ChatAttachment[];
}

interface SendWindow {
  ws?: WebSocket | null;
  currentSessionId?: string | null;
  _execThinkingEffort?: string;
  _lastRunCommand?: string | null;
  _pendingChannelChoice?: { channel: string | null; account_id?: string | null } | null;
  __pendingChannelChoices?: Record<
    string,
    { channel: string | null; account_id: string | null }
  >;
  setWelcomeVisible?: (show: boolean) => void;
  setRunning?: (running: boolean) => void;
  /** Pending web-originated user text, isolated by real/provisional id. */
  __pendingUserTextBySession?: Record<string, string>;
  __pendingFirstAckBySession?: Record<string, true>;
  __sessionStore?: {
    getState: () => {
      setRunningTaskFor?: (
        sessionId: string,
        task: Record<string, unknown> | null,
      ) => void;
    };
  };
}

function reservePendingChatSend(
  host: SendWindow,
  sessionId: string | null,
  text: string,
): (() => void) | null {
  if (!sessionId) return () => {};
  if (
    sessionId.startsWith("local_")
    && host.__pendingFirstAckBySession?.[sessionId]
  ) {
    return null;
  }
  const previousText = host.__pendingUserTextBySession?.[sessionId];
  const hadPreviousText = Object.prototype.hasOwnProperty.call(
    host.__pendingUserTextBySession ?? {},
    sessionId,
  );
  host.__pendingUserTextBySession = {
    ...host.__pendingUserTextBySession,
    [sessionId]: text,
  };
  if (sessionId.startsWith("local_")) {
    host.__pendingFirstAckBySession = {
      ...host.__pendingFirstAckBySession,
      [sessionId]: true,
    };
  }
  return () => {
    if (host.__pendingUserTextBySession?.[sessionId] === text) {
      const nextText = { ...host.__pendingUserTextBySession };
      if (hadPreviousText && previousText !== undefined) {
        nextText[sessionId] = previousText;
      } else {
        delete nextText[sessionId];
      }
      host.__pendingUserTextBySession = nextText;
    }
    if (sessionId.startsWith("local_")) {
      const nextFirstAck = { ...host.__pendingFirstAckBySession };
      delete nextFirstAck[sessionId];
      host.__pendingFirstAckBySession = nextFirstAck;
      host.__sessionStore?.getState().setRunningTaskFor?.(sessionId, null);
    }
  };
}

/**
 * Write a `chat` turn to the WebSocket. Returns `true` if the socket
 * was open and the payload was sent; `false` otherwise (caller keeps
 * the user's text so it isn't lost).
 */
export function sendChatMessage({
  text,
  sessionId,
  thinking,
  toolsEnabled,
  webSearchEnabled,
  serviceTier,
  attachments,
}: SendMessageBridgeArgs): boolean {
  const w = window as unknown as SendWindow;
  const ws = w.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  const rollbackPendingSend = reservePendingChatSend(w, sessionId, text);
  if (!rollbackPendingSend) {
    // A second browser event before the first ACK is already represented by
    // the in-flight provisional send. Treat it as handled without writing a
    // second turn or replacing the text paired with that ACK.
    return true;
  }

  // Hide the welcome panel right away — before the ack round-trip.
  w.setWelcomeVisible?.(false);

  // The legacy `run ...` typed-command path is gone (Track A removed
  // the backend parser; fn-form submits now POST /api/function/{name}
  // directly). So a `run gui_agent ...` typed in the textarea is just
  // ordinary chat text to the LLM now — no special `exec_thinking_effort`
  // override here.
  // Project picked on a not-yet-created chat: ride the first message so
  // the backend can create the session repo INSIDE the project directory
  // (the post-ack set_session_project arrives after the repo already
  // exists at the home root, too late to relocate it). Not consumed
  // here — wsHandleChatAck still sends set_session_project as the
  // idempotent meta/reverse-index bind and clears the pending entry.
  const pendingProjectId = sessionId
    ? useSessionStore.getState().pendingProjectsByChat[sessionId]
    : null;

  const payload: Record<string, unknown> = {
    action: "chat",
    text,
    session_id: sessionId,
    thinking_effort: thinking,
    exec_thinking_effort: w._execThinkingEffort,
    tools: toolsEnabled,
    web_search: webSearchEnabled,
  };
  if (serviceTier) {
    payload.service_tier = serviceTier;
  }
  if (pendingProjectId) {
    payload.project_id = pendingProjectId;
  }
  if (attachments && attachments.length > 0) {
    // Backend (ws_actions/chat.py) reads ``attachments`` and dispatcher
    // (TurnRequest.attachments) folds them into the user message as
    // ImageContent blocks. Strip any data-URL prefix the caller might
    // have left behind — backend expects pure base64.
    payload.attachments = attachments.map((a) => ({
      type: a.type,
      data: a.data.replace(/^data:[^;]+;base64,/, ""),
      media_type: a.media_type,
      ...(a.filename ? { filename: a.filename } : {}),
    }));
  }
  // First message of a brand-new conversation: attach the channel
  // choice from the welcome-screen picker, if any. Ignored by the
  // backend for existing convs.
  const channelChoice =
    sessionId
      ? (w.__pendingChannelChoices?.[sessionId] ?? null)
      : (w._pendingChannelChoice ?? null);
  const pendingFirstTurn = sessionId
    ? sessionId !== w.currentSessionId
    : !w.currentSessionId;
  if (pendingFirstTurn && channelChoice?.channel) {
    payload.channel = channelChoice.channel;
    payload.account_id = channelChoice.account_id || "";
  }

  try {
    ws.send(JSON.stringify(payload));
    if (ws.readyState !== WebSocket.OPEN) {
      rollbackPendingSend();
      return false;
    }
  } catch (error) {
    rollbackPendingSend();
    console.error("[sendChatMessage] WebSocket send failed:", error);
    return false;
  }
  if (sessionId?.startsWith("local_")) {
    w.__sessionStore?.getState().setRunningTaskFor?.(sessionId, {
      session_id: sessionId,
      msg_id: "",
      started_at: Date.now() / 1000,
    });
  }
  w.setRunning?.(true);
  return true;
}
