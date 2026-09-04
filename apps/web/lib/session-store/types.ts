// Domain types for the session store. Split out of session-store.ts
// (was a 900-line god-file mixing ~300 lines of type defs with the
// Zustand store). Re-exported from ./index so `@/lib/session-store`
// import paths are unchanged.

export type MessageStatus =
  | "pending"
  | "streaming"
  | "running"
  | "cancelling"
  | "done"
  | "completed"
  | "error"
  | "cancelled"
  | "interrupted";

export interface FnParam {
  name: string;
  /** Friendly label shown in the function form. Falls back to `name`
   *  when omitted; backend `@agentic_function(input={...})` can set
   *  `label: "..."` to rename cryptic param names (e.g. `fn` → "function"). */
  label?: string;
  type?: string;
  required?: boolean;
  description?: string;
  default?: string;
  placeholder?: string;
  hidden?: boolean;
  /** User-settable but collapsed below the primary fields. Unlike hidden
   *  internal runtime parameters, advanced fields are valid direct-call
   *  arguments and appear in the FunctionForm's Advanced section. */
  advanced?: boolean;
  multiline?: boolean;
  options?: string[];
  options_from?: string;
}

export interface AgenticFunction {
  name: string;
  description?: string;
  category?: string;
  params_detail?: FnParam[];
}

/** One tool call inside an assistant turn — the React port of the
 *  legacy `.chat-tool` card. `status` drives the header badge
 *  ("running…" while live, "done"/"error" once the result lands). */
export interface ChatToolCall {
  id: string;                  // tool_call_id (server) or local fallback
  tool: string;                // tool name
  input: string;               // raw args string
  result?: string;             // result text, once tool_result arrives
  truncated?: boolean;
  totalBytes?: number;
  messageId?: string;
  nodeId?: string;
  isError?: boolean;
  status: "running" | "done" | "error";
}

/** A "system needs a decision" request surfaced in the composer — the
 *  `data` payload of a question.asked frame (runtime.ask / confirm /
 *  tool approval). See composer modes (docs/design/ui/composer-interaction-modes.md). */
export interface PendingDecision {
  id: string;
  /** 这条提问属于哪个会话 —— 卡片只在该会话的输入框里显示（输入框状态跟
   *  会话走，切到别的会话不该看到、更不该误答到别的会话上）。 */
  sessionId: string;
  /** Exact canonical wait target.  A question card never infers these from a session. */
  executionId: string;
  waitGeneration: number;
  expectedVersion: number;
  kind: "ask" | "confirm" | "approval" | "form" | "ask_many";
  prompt: string;
  options: string[];
  multi: boolean;
  allow_custom: boolean;
  detail?: string;
  /** approval-only: the tool being gated + its args, for the danger summary. */
  tool?: string;
  args?: Record<string, unknown>;
  /** approval-only: danger level for card highlighting. */
  risk_level?: "low" | "medium" | "high";
  /** form-only: flat-object field schema (field name → {type, title,
   *  description, enum, default, …}). The answer is an object (field → value).
   *  See runtime.form / docs/design/runtime/user-input-requests.md Phase 4a. */
  schema?: Record<string, FormFieldSchema>;
  /** ask_many-only: a packed group of questions answered one screen with
   *  prev/next switching, submitted together. The answer is a list (one
   *  per question). See runtime.ask_many. */
  questions?: AskOne[];
}

/** One question inside an ask_many group. */
export interface AskOne {
  prompt: string;
  options: string[];
  multi: boolean;
  allow_custom: boolean;
}

/** Per-session composer settings — tool toggles + thinking effort.
 *  Persisted per session (see composerSettingsBySession) so each chat
 *  keeps its own; "" thinking means "model default". */
export interface ComposerSettings {
  thinking: string;
  tools: boolean;
  webSearch: boolean;
  fast: boolean;
  /** What Enter does while this session already has a running turn. */
  runningMessageMode: "queue" | "steer";
  /** Permission mode for this session's tool calls: ask/acceptEdits/
   *  plan/auto/bypass. "" means inherit (backend resolves session →
   *  project → ask). */
  permission_mode: string;
  /** Last effective mode echoed by the backend (chat_ack / session_loaded). */
  effective_permission?: string;
  /** Unattended: nobody watching → the agent's user-question tool is
   *  withheld so it never blocks on a prompt no one can answer. Web default
   *  is attended (false); toggled from the composer "+" menu, mirrored to
   *  the backend via the set_attended WS action. */
  unattended: boolean;
  /** Session Sandbox override. true/false is explicit; omitted inherits
   *  the project/global default (system default is on). */
  sandbox?: boolean;
}

/** One field in a runtime.form schema (MCP-elicitation flat object). */
export interface FormFieldSchema {
  type?: "string" | "integer" | "number" | "boolean";
  title?: string;
  description?: string;
  enum?: string[];
  default?: string | number | boolean;
  minimum?: number;
  maximum?: number;
}

export interface ChatMsg {
  id: string;                  // msg_id from server, or local generated for user msgs
  role: "user" | "assistant" | "system";
  content: string;             // final assistant text / user text
  /** Event-divider rows (compaction / snip). Other system rows omit this. */
  kind?: "compaction" | "snip" | "event";
  /** ``card`` = fold summary at the covered segment; ``event`` = execution marker. */
  slot?: "card" | "event";
  /** Covered-message count on reconstructed compaction rows. */
  summarisedCount?: number;
  coversIds?: string[];
  tokensBefore?: number;
  tokensAfter?: number;
  /** Reasoning tokens streamed under a collapsible "Thinking" block. */
  thinking?: string;
  /** Tool calls made during this assistant turn, in emit order. */
  tools?: ChatToolCall[];
  status?: MessageStatus;
  function?: string;           // if this is a direct function run
  display?: "runtime" | "normal";
  /** Pass-through of metadata.source from the server so the client
   *  can distinguish "real user typed" vs internal synthetic turns
   *  (job_followup, merge_turn, agent_spawn). */
  source?: string;
  /** User message injected into an already-running agent turn. */
  steering?: boolean;
  /** Pass-through of the wire ``predecessor`` (conversation-chain
   *  edge) so we can correlate internal-source msgs (e.g.
   *  job_followup) and runtime/attach rows with the turn they hang
   *  off. Runtime + attach rows are written with ``predecessor``
   *  only and no ``caller`` (see webui/_execute), so this really is
   *  the right edge here despite the historical field name. */
  calledBy?: string;
  /** When this user msg is the root of a spawned sub-branch, points
   *  back at the main-lane turn (LLM reply) that called task() to
   *  create it. Frontend renders a "Spawned from" card at the top
   *  of the sub branch so the user can switch back. */
  spawnedFrom?: { callerId: string; label?: string };
  timestamp?: number;
  attempts?: { content: string; timestamp: number; tree?: TreeNode; usage?: unknown }[];
  current_attempt?: number;
  /** Server response type — "result" / "error". Drives the runtime
   *  block's error styling and the assistant bubble's error branch. */
  rawType?: string;
  /** Structured error taxonomy (on a failed turn) — categorical reason,
   *  whether it's retryable, and a server retry hint. Lets the bubble
   *  render an actionable error. See
   *  docs/design/providers/reliability/error-taxonomy-propagation.md. */
  errorReason?: string;
  errorRetryable?: boolean;
  errorRetryAfterS?: number;
  /** Execution tree captured with a function result, rendered inside the
   *  runtime block. */
  contextTree?: TreeNode;
  /** Provider usage for the runtime block footer. Opaque — passed
   *  straight to the legacy `formatUsageFooterLabel`. */
  usage?: unknown;
  /** Exact committed file-mutation summary embedded on the assistant node.
   *  Historical transcripts render the file card from this field without
   *  issuing one WS request per message. */
  turnFiles?: TurnFileSummary;
  /** Persisted single-turn history state; enables Redo after reload. */
  reverted?: boolean;
  /** Sibling-version navigator state (the `< N/M >` strip). Populated
   *  from a loaded conversation when the turn has been retried/edited;
   *  the prev/next ids are what `/api/chat/checkout` targets. */
  siblingIndex?: number;
  siblingTotal?: number;
  prevSiblingId?: string;
  nextSiblingId?: string;
  /** Peer-session attach pointer — present on assistant rows whose
   *  ``function === "attach"``. Drives the AttachCard rendering +
   *  drawer open behavior. */
  attach?: {
    session_id?: string;
    head_id?: string;
    commit_id?: string;
    label?: string;
    prompt?: string;
    /** True for user-triggered attaches (right-rail Branches → Attach
     *  to). False/undefined for auto-attaches written by /task spawns.
     *  The card surfaces this to explain "I'm a staged reference, not
     *  an executed call". */
    manual?: boolean;
    /** Pinned source ContextCommit id (written when the attach was
     *  created). Frontend uses it to label the embed ("commit XX").
     *  Absent on legacy attach rows from before the expansion refactor. */
    source_commit_id?: string;
    /** How many ContextItems the source commit holds (the count the
     *  attach would expand into). Computed server-side so the card
     *  can render "EMBEDS N messages" without a follow-up round trip. */
    embed_count?: number;
    /** Sum of source commit's item tokens — companion to embed_count
     *  for the same preview line. */
    embed_tokens?: number;
    /** Async-job lifecycle status. Set when the attach was written
     *  by a /task --async / JobRunner spawn — moves through pending
     *  → running → completed / errored / cancelled. Drives the
     *  status pill in the attach card so the user can see whether
     *  the embedded content is finalised. */
    status?: "pending" | "queued" | "running" | "cancelling" | "completed"
      | "errored" | "cancelled";
    /** Cross-reference to the Job entity that owns this attach. */
    job_id?: string;
    /** Canonical execution identity and optimistic-concurrency version. */
    execution_id?: string;
    status_version?: number;
  };
  /** Which agent produced this turn. Same-session multi-agent: a
   *  conversation can have N agents writing branches in the same
   *  session repo; the UI uses this to colour / label / avatar
   *  each turn by author. */
  agentId?: string;
  /** Runtime-block child rows belonging to this assistant turn (LLM-
   *  issued @agentic_function calls — gui_agent / research_agent /
   *  wiki_agent). Populated at the data-loading layer (conv-mapper for
   *  history, chat-stream for live runs) so the parent assistant
   *  bubble can render them INSIDE its own card instead of as
   *  standalone top-level rows. Top-level MessageList skips any row
   *  that lives here. Only assistant rows ever populate this. */
  runtimeChildren?: ChatMsg[];
  /** Spawned/attach cards anchored to this assistant turn（在哪调用就
   *  画在哪）: each row records a task() spawn made DURING this turn.
   *  The bubble renders each card right after the tool block that
   *  spawned it (FIFO over blocks with tool==="agent"), i.e. thinking →
   *  tool call → Spawned card → … → final text. Populated by
   *  conv-mapper; rows living here are skipped at the top level. */
  attachCards?: ChatMsg[];
  /** caller 链折出来的调用树（agent 调函数的层级，TNode 形状）：
   *  时间线的 FunctionStep 按工具名 FIFO 认领并递归渲染。 */
  callRoots?: Array<Record<string, unknown>>;
  /** Ordered LLM blocks (thinking / text / tool) in the order the
   *  model emitted them. When present, the assistant bubble renders
   *  block-by-block so tool cards / RuntimeBlocks land at the exact
   *  position in the LLM output where the tool was called, instead
   *  of all stacked at the bottom. Tool blocks with an agentic name
   *  (gui_agent / research_agent / wiki_agent) claim entries from
   *  ``runtimeChildren`` in order — the runtime rows carry no
   *  ``tool_call_id`` back-reference. */
  blocks?: AssistantBlock[];
}

export interface TurnFileSummaryFile {
  path: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
  recoverability?: string;
  unavailable_reason?: string | null;
}

export interface TurnFileSummary {
  version: number;
  files: TurnFileSummaryFile[];
  file_count: number;
  added: number | null;
  removed: number | null;
  recoverability?: "exact" | "unavailable";
}

export interface AssistantBlock {
  type: "thinking" | "text" | "tool";
  /** thinking / text payload */
  text?: string;
  /** tool block fields */
  tool?: string;
  tool_call_id?: string;
  input?: string;
  result?: string;
  truncated?: boolean;
  total_bytes?: number;
  message_id?: string;
  node_id?: string;
  is_error?: boolean;
}

export interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
  /** Last-activity timestamp — the sidebar's recency-sort key. */
  updated_at?: number;
  agent_id?: string;
  source?: string;
  peer_display?: string;
  channel?: string;
  account_id?: string;
  peer?: string;
  preview?: string | null;
  pinned?: boolean;
  archived?: boolean;
  group?: string;
  status?: string;
  unread?: boolean;
  project?: string;
  workspace_alignment?: {
    status?: "aligned" | "mismatch";
    reason?: string;
    source_head_id?: string | null;
    target_head_id?: string | null;
    decision?: string | null;
  };
}

export interface RunningTask {
  session_id: string;
  msg_id: string;
  func_name?: string;
  started_at?: number;
  execution_id?: string;
  status_version?: number;
  cancelling?: boolean;
}

export interface TreeNode {
  id?: string;
  type?: string;
  name?: string;
  status?: string;
  inputs?: Record<string, unknown>;
  outputs?: unknown;
  elapsed_ms?: number;
  children?: TreeNode[];
  node_type?: string;
  _in_progress?: boolean;
  [k: string]: unknown;
}

/**
 * Normalized shape.
 *
 * ``messagesById`` holds every message ever observed, keyed by its id.
 * ``messageOrder[sessionId]`` holds the ordered id list for one
 * conversation. Split this way so a streaming delta only touches one
 * entry in ``messagesById`` and leaves ``messageOrder`` untouched —
 * components that subscribe to the id list (e.g. the scroll container)
 * don't re-render per token, only bubbles subscribed to *their own*
 * id do. Matches the pattern Claude.ai / ChatGPT webapps use.
 *
 * Cross-conversation cleanup: removing a conversation drops its ids
 * from the order map AND removes the referenced messages from
 * ``messagesById`` (no dangling entries).
 */
/** Per-agent settings state, mirrors ``window._agentSettings``
 *  shape. The TopBar reads this to render the Chat / Exec badges; legacy
 *  ``loadAgentSettings`` in providers.js pushes through to ``setAgentSettings``
 *  in the same place it used to call ``updateAgentBadges``. Only the fields
 *  the React TopBar needs are typed here — the legacy payload has more. */
export interface AgentBadgeInfo {
  provider?: string;
  model?: string;
  session_id?: string;
  locked?: boolean;
  /** 当前模型有无 Fast（service_tier）档；false/缺省 → composer 隐藏
   *  "高速"开关（与 thinking_levels 为空隐藏思考菜单同一模式）。 */
  fast?: boolean;
}
export interface AgentSettingsState {
  chat?: AgentBadgeInfo;
  exec?: AgentBadgeInfo;
}

/** Branch chip state for the current conversation. ``visible`` is false
 *  when there's no session or the session has no branches. ``count`` is
 *  the branch tally shown in the label suffix. */
export interface BranchBadgeInfo {
  visible: boolean;
  name: string;
  count: number;
}

/** Status badge text + tone. ``tone`` maps to the legacy CSS class
 *  modifiers (status-badge / .connecting / .disconnected / .paused) and
 *  to the inner dot's color. ``label`` is the short text shown next to
 *  the dot — channel name, "connecting", "connected · Local", etc. */
export type StatusTone = "connecting" | "ok" | "warn" | "err";
export interface StatusBadgeInfo {
  label: string;
  tone: StatusTone;
  /** True when the chat is currently paused. Drives the "paused" class
   *  so the badge takes the warning hue without touching the dot. */
  paused?: boolean;
  /** Title attribute / hover tooltip. */
  title?: string;
}
