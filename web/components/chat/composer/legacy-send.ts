"use client";

/**
 * Chat send path — owned by the React composer.
 *
 * Slice F: this used to delegate to the legacy `window.sendMessage`
 * (chat.js), which built the user bubble / assistant placeholder DOM
 * before writing the socket. Those bubbles are now rendered by the
 * React message store — the chat-stream reducer's `handleAck` builds
 * the user turn from `window.__pendingUserText`. So this just writes
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
  type: "image";
  data: string;
  media_type: string;
  filename?: string;
}

interface SendMessageBridgeArgs {
  text: string;
  thinking: string;
  toolsEnabled: boolean;
  webSearchEnabled: boolean;
  attachments?: ChatAttachment[];
}

interface SendWindow {
  ws?: WebSocket | null;
  currentSessionId?: string | null;
  _execThinkingEffort?: string;
  _lastRunCommand?: string | null;
  _pendingChannelChoice?: { channel: string | null; account_id?: string | null } | null;
  setWelcomeVisible?: (show: boolean) => void;
  setRunning?: (running: boolean) => void;
  /** Stashed for the chat-stream reducer: the server never echoes a
   *  web-originated user turn back, so `handleAck` reads this to build
   *  the user bubble once `chat_ack` assigns the msg_id. */
  __pendingUserText?: string;
}

/**
 * Write a `chat` turn to the WebSocket. Returns `true` if the socket
 * was open and the payload was sent; `false` otherwise (caller keeps
 * the user's text so it isn't lost).
 */
export function sendChatMessage({
  text,
  thinking,
  toolsEnabled,
  webSearchEnabled,
  attachments,
}: SendMessageBridgeArgs): boolean {
  const w = window as unknown as SendWindow;
  const ws = w.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;

  const sessionId = w.currentSessionId ?? null;

  const isRun = /^run\s/i.test(text);
  if (isRun) w._lastRunCommand = text;

  // Hide the welcome panel right away — before the ack round-trip.
  w.setWelcomeVisible?.(false);

  const payload: Record<string, unknown> = {
    action: "chat",
    text,
    session_id: sessionId,
    thinking_effort: thinking,
    // For a `/run`, the LLM work happens in the function's exec
    // runtime — so the effort the user picked in the composer drives
    // exec, not the chat side. (A plain chat keeps the agent-settings
    // exec effort.) Without this the run ignored the picker and fell
    // back to the provider default — xhigh for codex.
    exec_thinking_effort: isRun ? thinking : w._execThinkingEffort,
    tools: toolsEnabled,
    web_search: webSearchEnabled,
  };
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
  if (!sessionId && w._pendingChannelChoice?.channel) {
    payload.channel = w._pendingChannelChoice.channel;
    payload.account_id = w._pendingChannelChoice.account_id || "";
  }

  // The reducer's `handleAck` builds the user bubble from this once
  // the server assigns a msg_id.
  w.__pendingUserText = text;
  ws.send(JSON.stringify(payload));
  w.setRunning?.(true);
  return true;
}
