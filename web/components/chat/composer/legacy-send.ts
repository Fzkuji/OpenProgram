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

interface SendMessageBridgeArgs {
  text: string;
  thinking: string;
  toolsEnabled: boolean;
  webSearchEnabled: boolean;
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
}: SendMessageBridgeArgs): boolean {
  const w = window as unknown as SendWindow;
  const ws = w.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;

  const sessionId = w.currentSessionId ?? null;

  if (/^run\s/i.test(text)) w._lastRunCommand = text;

  // Hide the welcome panel right away — before the ack round-trip.
  w.setWelcomeVisible?.(false);

  const payload: Record<string, unknown> = {
    action: "chat",
    text,
    session_id: sessionId,
    thinking_effort: thinking,
    exec_thinking_effort: w._execThinkingEffort,
    tools: toolsEnabled,
    web_search: webSearchEnabled,
  };
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
