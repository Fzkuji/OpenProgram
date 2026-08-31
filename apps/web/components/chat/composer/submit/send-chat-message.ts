"use client";

import { useSessionStore } from "@/lib/session-store";
import { surfaceRefForChat } from "@/lib/desktop-bridge";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { setWelcomeVisible } from "@/lib/runtime-bridge/helpers";
import { setRunning } from "@/lib/runtime-bridge/ui";
import {
  registerChatSender,
  rememberSendSettings,
} from "@/lib/state/send-queue";
import {
  draftChannelChoiceFor,
  draftChannelChoiceHost,
} from "@/lib/runtime-bridge/draft-channel-choice";
import {
  clearPendingFirstAck,
  clearPendingUserText,
  getPendingUserTimestamp,
  getPendingUserText,
  hasPendingFirstAck,
  hasPendingUserText,
  setPendingFirstAck,
  setPendingUserText,
} from "@/lib/pending-user-text";

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
 *   - `setRunning(true)` — legacy run flag (runtime-bridge/ui.ts).
 *
 * The ack-pairing reservations live in `lib/pending-user-text` (also
 * read by `lib/net/chat-stream.ts` and `lib/runtime-bridge/chat-handlers.ts`),
 * and the first-message channel attach reads `draftChannelChoiceHost`
 * (lib/runtime-bridge/draft-channel-choice) — neither rides `window`.
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
  /** Internal provenance used by the web backend's path-marker rewrite. */
  source_path?: string;
}

interface SendMessageBridgeArgs {
  text: string;
  /** Real or provisional chat key. Drafts send their local_* key so the
   *  existing server protocol can acknowledge the correct tab. */
  sessionId: string | null;
  thinking: string;
  toolsEnabled: boolean;
  toolsProfile?: string;
  webSearchEnabled: boolean;
  /** Per-turn speed tier — "priority" (Fast) or undefined (provider
   *  default). Sent as ``service_tier`` so the backend forwards it to
   *  the provider request body. */
  serviceTier?: string;
  attachments?: ChatAttachment[];
  /** Set when the turn targets a session that is NOT the focused one
   *  (a split-view peer pane). The WS payload is identical — only the
   *  focused-shell side effects are skipped, since `setWelcomeVisible`
   *  and `setRunning` are singletons belonging to the focused chat and
   *  flipping them from a background send would corrupt its UI. */
  background?: boolean;
}

function reservePendingChatSend(
  sessionId: string | null,
  text: string,
): (() => void) | null {
  if (!sessionId) return () => {};
  if (sessionId.startsWith("local_") && hasPendingFirstAck(sessionId)) {
    return null;
  }
  const previousText = getPendingUserText(sessionId);
  const previousTimestamp = getPendingUserTimestamp(sessionId);
  const hadPreviousText = hasPendingUserText(sessionId);
  setPendingUserText(sessionId, text);
  if (sessionId.startsWith("local_")) {
    setPendingFirstAck(sessionId);
  }
  return () => {
    if (getPendingUserText(sessionId) === text) {
      if (hadPreviousText && previousText !== undefined) {
        setPendingUserText(sessionId, previousText, previousTimestamp ?? Date.now());
      } else {
        clearPendingUserText(sessionId);
      }
    }
    if (sessionId.startsWith("local_")) {
      clearPendingFirstAck(sessionId);
      useSessionStore.getState().setRunningTaskFor(sessionId, null);
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
  toolsProfile = "__agent__",
  webSearchEnabled,
  serviceTier,
  attachments,
  background = false,
}: SendMessageBridgeArgs): boolean {
  const ws = getSocket();
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  // Remember the knobs this turn rode out under, so a `run_active`
  // rejection (which echoes only the text back) can be re-queued with
  // the same settings instead of guessed defaults.
  if (sessionId) {
    rememberSendSettings(sessionId, {
      thinking, toolsEnabled, toolsProfile, webSearchEnabled, serviceTier, background,
    });
  }
  const rollbackPendingSend = reservePendingChatSend(sessionId, text);
  if (!rollbackPendingSend) {
    // A second browser event before the first ACK is already represented by
    // the in-flight provisional send. Treat it as handled without writing a
    // second turn or replacing the text paired with that ACK.
    return true;
  }

  // Hide the welcome panel right away — before the ack round-trip.
  // Skipped for background sends: the welcome panel belongs to the
  // focused chat shell, not to the peer session being written to.
  if (!background) setWelcomeVisible(false);

  // Exact registered `name(...)` input is intercepted by useChatSubmit and
  // never reaches this chat bridge. The removed legacy `run ...` syntax is
  // ordinary chat text; there is no execution override here.
  // Project picked on a not-yet-created chat: ride the first message so
  // the backend can create the session repo INSIDE the project directory
  // (the post-ack set_session_project arrives after the repo already
  // exists at the home root, too late to relocate it). Not consumed
  // here — wsHandleChatAck still sends set_session_project as the
  // idempotent meta/reverse-index bind and clears the pending entry.
  const pendingProjectId = sessionId
    ? useSessionStore.getState().pendingProjectsByChat[sessionId]
    : null;

  const storedMode =
    sessionId
      ? useSessionStore.getState().composerSettingsBySession[sessionId]
        ?.permission_mode
      : "";
  const permissionMode =
    storedMode === "ask"
    || storedMode === "acceptEdits"
    || storedMode === "plan"
    || storedMode === "auto"
    || storedMode === "bypass"
      ? storedMode
      : "inherit";

  const payload: Record<string, unknown> = {
    action: "chat",
    text,
    session_id: sessionId,
    thinking_effort: thinking,
    exec_thinking_effort: runtimeState._execThinkingEffort ?? undefined,
    tools: toolsEnabled,
    web_search: webSearchEnabled,
    permission_mode: permissionMode,
  };
  const sandbox = useSessionStore.getState().composerSettingsBySession[
    sessionId ?? "__new__"
  ]?.sandbox;
  if (typeof sandbox === "boolean") {
    payload.sandbox_enabled = sandbox;
  }
  if (toolsProfile && toolsProfile !== "__agent__") {
    payload.tools_profile = toolsProfile;
  }
  const surface = surfaceRefForChat(sessionId, toolsEnabled);
  if (surface) payload.surface = surface;
  if (serviceTier) {
    payload.service_tier = serviceTier;
  }
  if (pendingProjectId) {
    payload.project_id = pendingProjectId;
  }
  // Additional working directories picked before the session exists ride
  // the chat frame (backend persists them via save_session_run_config on
  // session create). Harmless on existing sessions — same list the
  // backend already has. Keyed by the same chat key as pendingProjects:
  // drafts use their local_* key, which the backend adopts as the id.
  const additionalWorkingDirs = sessionId
    ? useSessionStore.getState().additionalWorkingDirsBySession[sessionId]
    : null;
  if (additionalWorkingDirs && additionalWorkingDirs.length > 0) {
    payload.additional_working_dirs = additionalWorkingDirs;
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
      ...(a.source_path ? { source_path: a.source_path } : {}),
    }));
  }
  // First message of a brand-new conversation: attach the channel
  // choice from the welcome-screen picker, if any. Ignored by the
  // backend for existing convs.
  const channelChoice = sessionId
    ? draftChannelChoiceFor(draftChannelChoiceHost, sessionId)
    : (draftChannelChoiceHost._pendingChannelChoice ?? null);
  const pendingFirstTurn = sessionId
    ? sessionId !== runtimeState.currentSessionId
    : !runtimeState.currentSessionId;
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
  const acceptedAt = Date.now();
  if (sessionId) setPendingUserText(sessionId, text, acceptedAt);
  // Close the clear→ACK race for every session, not only provisional
  // drafts. A queued send is already in flight once the socket accepted
  // the frame; marking that session busy now prevents a repeated
  // running_task_clear from draining the next queue entry before chat_ack.
  if (sessionId) {
    // 占位只在槽位为空时写入。run_active 竞态下旧回合还在跑，覆盖会
    // 丢掉它的 execution_id，之后停止发不出 execution.cancel。
    const store = useSessionStore.getState();
    const existing = store.runningTasks[sessionId];
    if (!existing || (!existing.msg_id && !existing.execution_id)) {
      store.setRunningTaskFor(sessionId, {
        session_id: sessionId,
        msg_id: "",
        started_at: acceptedAt / 1000,
      });
    }
  }
  // `setRunning` is the focused shell's global run flag (drives its send/stop
  // button). A background turn must not flip it — the focused chat isn't the
  // one running. Per-session run state still lands via the store's
  // `setRunningTaskFor` on chat_ack, which is what the peer pane reads.
  if (!background) setRunning(true);
  return true;
}

// The send queue drains through this exact function; registering the
// reference here (rather than importing it there) keeps the store free
// of a dependency on the composer tree.
registerChatSender(sendChatMessage);
