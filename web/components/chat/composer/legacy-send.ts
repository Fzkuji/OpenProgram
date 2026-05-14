"use client";

/**
 * Bridge from the React composer to the legacy `sendMessage(text)` in
 * `web/public/js/chat/chat.js`.
 *
 * Why we don't just `ws.send(...)`: the legacy chat.js owns the full
 * "user clicks send" pipeline — it appends the user message bubble
 * (`addUserMessage`), hides the welcome screen (`setWelcomeVisible(false)`),
 * flips the running flag (`setRunning(true)`), adds the assistant
 * placeholder bubble, and *then* writes to the WebSocket. Bypassing
 * it leaves the React side without any of those visible updates —
 * the user's text disappears into the void until the model reply
 * arrives.
 *
 * Legacy `sendMessage` reads `_thinkingEffort`, `_toolsEnabled`,
 * `_webSearchEnabled` off the window for the WS payload, so we mirror
 * the React-side values onto window before calling.
 */

interface SendMessageBridgeArgs {
  text: string;
  thinking: string;
  toolsEnabled: boolean;
  webSearchEnabled: boolean;
}

interface LegacyChatGlobals {
  sendMessage?: (text: string) => void;
  _thinkingEffort?: string;
  _toolsEnabled?: boolean;
  _webSearchEnabled?: boolean;
}

/**
 * Hand the typed text off to `window.sendMessage`. Returns `true` if
 * the legacy entry point was available and called; `false` otherwise
 * (caller decides on the fallback — typically a direct `ws.send`).
 */
export function sendChatMessage({
  text,
  thinking,
  toolsEnabled,
  webSearchEnabled,
}: SendMessageBridgeArgs): boolean {
  const w = window as unknown as LegacyChatGlobals;
  w._thinkingEffort = thinking;
  w._toolsEnabled = toolsEnabled;
  w._webSearchEnabled = webSearchEnabled;
  if (typeof w.sendMessage !== "function") return false;
  w.sendMessage(text);
  return true;
}
