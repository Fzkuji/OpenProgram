import type { BackendClient } from '../../ws/client.js';

export interface REPLProps {
  client: BackendClient;
  initialAgent?: string;
  initialConversation?: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  model?: string | { provider?: string; id?: string };
  thinking_effort?: ThinkingEffort;
  default?: boolean;
}

export type ThinkingEffort =
  | 'off'
  | 'minimal'
  | 'low'
  | 'medium'
  | 'high'
  | 'xhigh';

export interface Activity {
  /** Verb shown next to the spinner — "Thinking", "Calling Bash", etc. */
  verb: string;
  /** Optional inline detail — usually the truncated tool input. */
  detail?: string;
  /** Wall clock when this turn started (used for elapsed display). */
  startedAt: number;
  /** Cumulative characters received from text deltas. */
  streamedChars?: number;
  /** Wall clock when streaming first started (text first delta). */
  streamStartedAt?: number;
}

/**
 * Discriminator for the legacy picker switch in REPL — whichever value
 * is set, the corresponding branch in PickerRouter renders. ``null``
 * means no picker is open and PromptInput owns the bottom row.
 *
 * The list grew alongside channel-binding work:
 *   - channel_action: after picking channel+account, choose binding
 *     mode (catch-all this conv / per-peer / list).
 *   - channel_peer_input: prompt for peer_id (e.g. wxid_xxx).
 *   - channel_qr_wait: show ASCII QR + status while wechat login is
 *     in progress.
 *   - channel_overwrite_confirm: a previous alias would be replaced
 *     — make the user explicitly opt in instead of silently overwriting.
 */
export type PickerKind =
  | null
  | 'model' | 'resume' | 'agent' | 'channel' | 'channel_account' | 'theme' | 'effort'
  | 'context_search' | 'context_search_results'
  | 'register_account_id' | 'register_token'
  | 'channel_action' | 'channel_peer_input' | 'channel_qr_wait'
  | 'channel_overwrite_confirm';

/** Pending attach payload paused in front of an overwrite confirm. */
export interface PendingAttach {
  channel: string;
  account_id: string;
  peer_kind: 'direct' | 'group' | 'channel';
  peer_id: string;
  session_id: string;
  existingSessionId: string;
  /**
   * Done-message lines pushSystem'd after attach lands. Captured at
   * confirm-time so the surface text matches whatever flow opened
   * the confirm (catch-all / peer / etc.).
   */
  successMessage: string;
}

/** Cached row from session_aliases.json — server returns these verbatim. */
export interface SessionAliasRow {
  channel?: string;
  account_id?: string;
  peer?: { kind?: string; id?: string };
  agent_id?: string;
  session_id?: string;
}

/** Channel-account row — `list_channel_accounts` envelope payload. */
export interface ChannelAccountRow {
  channel?: string;
  account_id?: string;
  configured?: boolean;
}

/** Past conversation row — `conversations_list` envelope payload. */
export interface PastConversation {
  id?: string;
  title?: string;
  created_at?: number;
  /** Channel name for channel-bound sessions ("wechat", "telegram", …). */
  source?: string;
  /** Display name for the bound peer (e.g. WeChat nickname). */
  peer_display?: string;
}

export interface SearchResultRow {
  session_id: string;
  session_title?: string;
  session_source?: string;
  message_id: string;
  role: string;
  preview: string;
  content?: string;
  timestamp?: number;
}

/** Two-step token register form (channel + account_id, then token). */
export interface RegisterForm {
  channel?: string;
  accountId?: string;
}

/**
 * Live activity for a conv the TUI is NOT currently focused on — used
 * to surface inbound channel turns (wechat / telegram / ...) without
 * polluting the main transcript. Each entry tracks the latest user
 * message + the assistant reply as it streams, so the bottom feed can
 * show e.g. `wechat:bot42 → main: streaming…` in real time.
 */
export interface ChannelActivity {
  convId: string;
  /** Channel platform ("wechat", "telegram", ...) when known. */
  source?: string;
  /** Display name for the bound peer (e.g. WeChat nickname). */
  peerDisplay?: string;
  /** Last user message text seen on this conv. */
  userText?: string;
  /** Buffered assistant text deltas — grows as `text_delta` events arrive. */
  streamingText: string;
  /** Final assistant text once the turn ends (`result` event). */
  finalText?: string;
  /** True between the first stream_event and the result event. */
  streaming: boolean;
  /** Last update wall-clock — used to age out idle entries. */
  lastUpdate: number;
}
