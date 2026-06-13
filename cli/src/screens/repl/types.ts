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
  | 'branch'
  | 'settings'
  | 'commands'
  | 'context_search' | 'context_search_results'
  | 'register_account_id' | 'register_token'
  | 'channel_action' | 'channel_peer_input' | 'channel_qr_wait'
  | 'channel_overwrite_confirm'
  | 'acct_list' | 'acct_action' | 'acct_rename' | 'acct_add_code'
  | 'acct_login_name' | 'acct_login_method' | 'acct_login'
  // runtime.ask / confirm / approval occupies the input slot — same role
  // as the web composer's question/approval mode. Driven by the
  // pendingDecisions queue, not a user slash command.
  | 'question';

/**
 * A "system needs a decision" request surfaced in the input slot — the
 * `data` payload of a question.asked frame (runtime.ask / confirm / tool
 * approval). Identical to the web composer's PendingDecision
 * (web/lib/session-store.ts) so both surfaces share one backend
 * contract. A FIFO queue holds these; the head occupies the input slot
 * as the `question` picker. See
 * docs/design/ui/composer-interaction-modes.md.
 */
export interface PendingDecision {
  id: string;
  kind: 'ask' | 'confirm' | 'approval' | 'form';
  prompt: string;
  options: string[];
  multi: boolean;
  allow_custom: boolean;
  detail?: string;
  /** approval-only: the gated tool + its args, for the danger summary. */
  tool?: string;
  args?: Record<string, unknown>;
  /** form-only: flat-object field schema (field name → field def). The
   *  answer is an object (field → value). runtime.form / Phase 4a. */
  schema?: Record<string, FormFieldSchema>;
}

/** One field in a runtime.form schema (MCP-elicitation flat object). */
export interface FormFieldSchema {
  type?: 'string' | 'integer' | 'number' | 'boolean';
  title?: string;
  description?: string;
  enum?: string[];
  default?: string | number | boolean;
  minimum?: number;
  maximum?: number;
}

/** One branch as returned by ws `list_branches`. */
export interface BranchRow {
  head_msg_id: string;
  name: string;
  is_named?: boolean;
  active?: boolean;
  created_at?: number;
}

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
