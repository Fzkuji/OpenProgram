// Domain types for the session store. Split out of session-store.ts
// (was a 900-line god-file mixing ~300 lines of type defs with the
// Zustand store). Re-exported from ./index so `@/lib/session-store`
// import paths are unchanged.

import type { RowKind, RowOrigin } from "../chat/transcript/wire-types.generated";
import type { FailureCause } from "../models/provider-state";

export type MessageStatus =
  | "pending"
  | "streaming"
  | "running"
  | "paused"
  | "cancelling"
  | "done"
  | "completed"
  | "error"
  | "cancelled"
  | "interrupted";

export interface FnParam {
  name: string;
  /** Friendly label shown in the function form. Falls back to `name`
   *  when omitted; backend `Agent.method_options input metadata` can set
   *  `label: "..."` to rename cryptic param names (e.g. `fn` → "function"). */
  label?: string;
  type?: string;
  required?: boolean;
  description?: string;
  default?: string;
  placeholder?: string;
  hidden?: boolean;
  /** Legacy execution-setting marker: accepted by explicit programmatic calls,
   *  excluded from all user function forms. It must not create a settings UI. */
  advanced?: boolean;
  multiline?: boolean;
  options?: string[];
  options_from?: string;
}

export interface AgenticFunction {
  continuation?: { supported: boolean; reason?: string; boundary?: string; state?: string };
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
  /** The model's provider ran this call server-side (a provider web search);
   *  the runtime only reports it. */
  providerExecuted?: boolean;
  status: "running" | "done" | "error" | "cancelled";
}

/** A "system needs a decision" request surfaced in the composer — the
 *  `data` payload of a question.asked frame (runtime.ask / confirm /
 *  tool approval). See composer modes (docs/reference/design/ui/composer/composer-interaction-modes.html). */
export interface PendingDecision {
  id: string;
  /** Owning conversation; output cards never target the active composer. */
  sessionId: string;
  /** Exact canonical wait target.  A question card never infers these from a session. */
  executionId: string;
  /** The reply this decision was asked in, when it was asked while this
   *  window watched the run: its settled card stays under that reply. */
  anchorId?: string;
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
  allowedScopes?: string[];
  /** The sub-agent that asks, when it is not the conversation's own agent. */
  askedBy?: string;
  /** form-only: flat-object field schema (field name → {type, title,
   *  description, enum, default, …}). The answer is an object (field → value).
   *  See runtime.form / docs/design/runtime/user-input-requests.md Phase 4a. */
  schema?: Record<string, FormFieldSchema>;
  /** ask_many-only: a group navigated one question at a time and submitted together.
   *  The answer is an ordered list (one
   *  per question). See runtime.ask_many. */
  questions?: AskOne[];
}

/** One question inside an ask_many group. */
export interface AskOne {
  prompt: string;
  /** Presentation metadata; option strings remain the canonical answer values. */
  question_title?: string;
  option_descriptions?: Record<string, string>;
  options: string[];
  multi: boolean;
  allow_custom: boolean;
}

/** Per-session composer settings — web search / sandbox toggles + thinking effort.
 *  Persisted per session (see composerSettingsBySession) so each chat
 *  keeps its own; "" thinking means "model default". */
export interface ComposerSettings {
  /** Stable identity of the branch whose settings are currently loaded. */
  active_branch_id?: string | null;
  /** First-turn Agent intent, scoped and persisted with this composer draft. */
  agentInvocation?: import("@/lib/agents/start-conversation").AgentConversationIntent;
  thinking: string;
  webSearch: boolean;
  fast: boolean;
  fastByModel?: Record<string, boolean>;
  /** Permission mode for this session's tool calls: ask/acceptEdits/
   *  plan/auto/bypass. "" means inherit (backend resolves session →
   *  project → ask). */
  permission_mode: string;
  /** Last effective mode echoed by the backend (chat_ack / session_loaded). */
  effective_permission?: string;
  permission_version?: number;
  /** Unattended: nobody watching → the agent's user-question tool is
   *  withheld so it never blocks on a prompt no one can answer. Web default
   *  is attended (false); toggled from the composer "+" menu, mirrored to
   *  the backend via the set_attended WS action. */
  unattended: boolean;
  /** Session Sandbox override. true/false is explicit; omitted inherits
   *  the project/global default (system default is on). */
  sandbox?: boolean;
  /** Controls the person changed in a draft (``thinking``, ``webSearch``):
   *  its first message carries only these; the rest follow the Agent. */
  draftTouched?: string[];
  /** The loaded branch's settings as the server resolves them: each
   *  value, its source (``branch``, ``agent``, ``project``, ``global``) and
   *  what it is when the branch sets nothing (``inherited``). */
  resolved?: ResolvedBranchSettings;
}

/** ``resolved`` of a branch-settings read (``branch_settings.resolved_settings``). */
export interface ResolvedBranchSettings {
  values?: Record<string, unknown>;
  sources?: Record<string, string>;
  inherited?: Record<string, unknown>;
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

/** One finished agent in a background-agent notice. */
export interface NoticeItem {
  /** report: a sub-agent's report is in context; reply: a branch
   *  answered a message; idle: a watched branch went idle; stalled: a
   *  sub-agent has had no activity for a while and is still running. */
  kind: "report" | "reply" | "idle" | "stalled";
  name: string;
  failed: boolean;
  address: string;
  /** The line that starts this agent's part of the notice text. */
  line: string;
}

/** The reply moved to another of the provider's credentials before it
 *  started (a rate limit, a spent quota, a refused key): which one it left,
 *  which one it uses, and why. Shown as one muted line over the reply. */
export interface CredentialNotice {
  to: string;
  from: string;
  /** rate_limit | quota | auth | model */
  cause?: string;
}

/** An automatic retry the reply is waiting on (chat-turn-visual-spec.html §1). */
export interface RetryStatus {
  attempt: number;
  maxAttempts?: number;
  /** transport | network_wait | output_limit | incomplete_response |
   *  credential_wait | a structured-output issue code */
  reason?: string;
  /** credential_wait: the credential the request waits for. */
  waitingFor?: string;
  /** Wait before the next attempt, counted from `receivedAt`. */
  delayMs?: number;
  /** What failed, one short line. */
  error?: string;
  /** When the client received the notice (ms since epoch). */
  receivedAt?: number;
}

export interface ChatMsg {
  id: string;                  // msg_id from server, or local generated for user msgs
  role: "user" | "assistant" | "system";
  content: string;             // final assistant text / user text
  /** User turns: pasted spans shown collapsed (lib/chat/send/paste-elements.ts). */
  pastedElements?: import("../chat/send/paste-elements.ts").PastedTextElement[];
  /** An always-on report posted as a message: its id, kind, where it came
   *  from and title. Older reports have no title. */
  reportId?: string;
  reportKind?: string;
  reportSource?: string;
  reportTitle?: string;
  /** Event-divider rows (compaction / snip). Other system rows omit this. */
  kind?: "compaction" | "snip" | "event";
  /** ``card`` = fold summary at the covered segment; ``event`` = execution marker. */
  slot?: "card" | "event";
  /** Covered-message count on reconstructed compaction rows. */
  summarisedCount?: number;
  tokensBefore?: number;
  tokensAfter?: number;
  /** Reasoning tokens streamed under a collapsible "Thinking" block. */
  thinking?: string;
  /** Live status for an automatic retry within this assistant turn. */
  retryStatus?: RetryStatus;
  /** Credential switches this reply made before it started: live from the
   *  stream, then stored with the reply (``credential_notices``). */
  credentialNotices?: CredentialNotice[];
  /** Tool calls made during this assistant turn, in emit order. */
  tools?: ChatToolCall[];
  status?: MessageStatus;
  function?: string;           // if this is a direct function run
  display?: "runtime" | "normal";
  /** Transient request from a newly completed foreground reply; history never sets it. */
  autoPreviewFiles?: boolean;
  goalVerification?: import("../chat/decisions/goal-verification").GoalVerification;
  /** Pass-through of metadata.source from the server so the client
   *  can distinguish "real user typed" vs internal synthetic turns
   *  (job_followup, merge_turn, agent_spawn). */
  source?: string;
  /** On a job_followup notice: one entry per agent it reports
   *  (agent/job/notices.py ``notice_items``). */
  notice?: NoticeItem[];
  /** What started a runtime turn: "restart" for the turn that carries on
   *  work a worker restart cut off (turn_runtime/prep.py). */
  trigger?: "restart";
  /** User rows: the message that set a Goal (``/goal <objective>``); the
   *  bubble labels it Goal. Its content is the objective alone. */
  startsGoal?: boolean;
  /** On a reply a restart cut off: the reply that continues it, which
   *  draws both (lib/chat/transcript/restart-continuation.ts). */
  continuedBy?: string;
  /** The person who sent a channel message (a group holds several). */
  speaker?: { id?: string; display?: string };
  /** User message injected into an already-running agent turn. */
  steering?: boolean;
  /** Rendered at an ordered boundary inside this reply, not twice. */
  steeringReplyId?: string;
  /** Which branch of the row dispatch this row takes: the server's word on
   *  its rows, the local writer's on a local row. Every row carries one. */
  rowKind: RowKind;
  /** Server rows, on a card or child of a reply: the block that owns it, null when none does. */
  blockId?: string | null;
  /** Server rows: the compaction card that folds this row. */
  coveredBy?: string;
  /** Server rows, on user rows: who wrote it. */
  origin?: RowOrigin;
  /** The send this row belongs to: a local row names its own, a server row the one it answers. */
  clientRequestId?: string;
  /** Local rows only: where the row is drawn, since it belongs to no turn. */
  localAnchor?: "tail" | { afterRowId: string };
  /** Server rows, on a continuation: the replies a restart cut, oldest first. */
  continues?: string[];
  /** Pass-through of the wire ``predecessor`` (conversation-chain
   *  edge) so we can correlate internal-source msgs (e.g.
   *  job_followup) and runtime/attach rows with the turn they hang
   *  off. Runtime + attach rows are written with ``predecessor``
   *  only and no ``caller`` (see turn_runtime/slash_actions.py), so this really is
   *  the right edge here despite the historical field name. */
  calledBy?: string;
  /** When this user msg is the root of a spawned sub-branch, points
   *  back at the main-lane turn (LLM reply) that called task() to
   *  create it. Frontend renders a "Spawned from" card at the top
   *  of the sub branch so the user can switch back. */
  spawnedFrom?: { callerId: string; label?: string };
  timestamp?: number;
  /** When a reply ended, in seconds, however it ended. `timestamp` is when
   *  the row was made, which for a reply is its start. */
  finishedAt?: number;
  attempts?: { content: string; timestamp: number; tree?: TreeNode; usage?: unknown }[];
  current_attempt?: number;
  /** Server response type — "result" / "error". Drives the runtime
   *  block's error styling and the assistant bubble's error branch. */
  rawType?: string;
  /** Structured error taxonomy (on a failed turn) — categorical reason,
   *  whether it's retryable, and a server retry hint. Lets the bubble
   *  render an actionable error. See
   *  docs/design/providers/reliability/error-taxonomy-propagation.md. */
  /** Failure detail is separate from the partial reply text. */
  errorDetail?: string;
  errorReason?: string;
  errorRetryable?: boolean;
  errorRetryAfterS?: number;
  /** Which limit or refusal stopped the reply (`errors.failure_cause`):
   *  a spent quota names its provider, limit, reset and the key's other
   *  limits, and draws the spent-quota card. */
  errorCause?: FailureCause | null;
  /** Stream position (`seq` of the reply's stream events) this row's
   *  content reflects; later events apply on top, earlier ones are dropped. */
  streamSeq?: number;
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
  /** On a user message: when its turn was pinned as a chapter (epoch
   *  seconds, from the node's ``metadata.chapter.pinned_at``). Unset when
   *  the turn is not pinned. The message rail tints a pinned turn's tick
   *  in the theme accent. */
  chapterPinnedAt?: number;
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
    /** The tool call that started this agent, when its writer recorded it. */
    tool_call_id?: string;
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
    /** What the finished agent used, with the agents it started: seconds,
     *  tool calls, tokens and cost in USD (null when a price is unknown). */
    usage?: {
      duration_s?: number | null;
      tool_calls?: number | null;
      tokens?: number | null;
      cost_usd?: string | null;
    };
    /** How the agent ended, in the result block's words, and why a limit
     *  or the person stopped it. */
    result_status?: "completed" | "failed" | "stopped" | "stalled";
    result_reason?: string;
    /** What an agent with its own worktree changed there. */
    files?: { count: number; branch?: string; dirty?: boolean };
  };
  /** Which agent produced this turn. Same-session multi-agent: a
   *  conversation can have N agents writing branches in the same
   *  session repo; the UI uses this to colour / label / avatar
   *  each turn by author. */
  agentId?: string;
  /** Runtime-block child rows belonging to this assistant turn (LLM-
   *  issued Agent method calls — gui_agent).
   *  The server projection nests them (transcript/children.py) so the
   *  parent assistant bubble renders them INSIDE its own card instead
   *  of as standalone top-level rows. Top-level MessageList skips any row
   *  that lives here. Only assistant rows ever populate this. */
  runtimeChildren?: ChatMsg[];
  /** A runtime child's tool call, when its record names one. */
  toolCallId?: string;
  /** Spawned/attach cards anchored to this assistant turn（在哪调用就
   *  画在哪）: each row records a task() spawn made DURING this turn.
   *  The bubble renders each card right after the tool block that
   *  spawned it (the block its blockId names), i.e. thinking →
   *  tool call → Spawned card → … → final text. The server projection
   *  nests them; rows living here are skipped at the top level. */
  attachCards?: ChatMsg[];
  /** caller 链折出来的调用树（agent 调函数的层级，TNode 形状）：
   *  时间线的 FunctionStep 按工具名 FIFO 认领并递归渲染。 */
  callRoots?: Array<Record<string, unknown>>;
  /** Ordered LLM blocks (thinking / text / tool) in the order the
   *  model emitted them. When present, the assistant bubble renders
   *  block-by-block so tool cards / RuntimeBlocks land at the exact
   *  position in the LLM output where the tool was called, instead
   *  of all stacked at the bottom. Tool blocks with an agentic name
   *  (gui_agent) claim entries from
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
  /** Set by the chat, never the server: the list includes files from a part
   *  of a restart-joined reply other than the last. Undo and the server's
   *  file list work on one turn, so neither can stand for this list. */
  spansTurns?: boolean;
}

/** A source the provider cited for a span of a text block. `start`/`end`
 *  are character offsets into the block's text, null when unknown. */
export interface Citation {
  url: string;
  title?: string | null;
  start?: number | null;
  end?: number | null;
}

export interface AssistantBlock {
  /** "restart" marks where a worker restart cut the reply off. */
  type: "thinking" | "text" | "tool" | "steering" | "restart";
  /** Server rows: this block's stable id and its stored position. */
  id?: string;
  index?: number;
  timestamp?: number;
  /** thinking / text payload */
  text?: string;
  /** tool block fields */
  tool?: string;
  tool_call_id?: string;
  input?: string;
  result?: string;
  truncated?: boolean;
  total_bytes?: number;
  /** Tool block: the agent loop bounded the result for the model; the card
   *  shows the head of the tool's own output and this says how long the
   *  whole output is. "View full output" reads the saved file. */
  full_output?: { total_lines?: number; total_chars?: number; shown_chars?: number };
  message_id?: string;
  node_id?: string;
  is_error?: boolean;
  /** Tool block: the provider ran this call itself (provider web search). */
  provider_executed?: boolean;
  /** Text block: sources the provider cited for this text. */
  citations?: Citation[];
  outcome?: "not_started" | "failed" | "cancelled" | "completed" | "unknown" | "waiting" | "recovered";
  /** The task and step this block was done for, stamped by the runtime from
   *  its position in the reply (lib/chat/transcript/task-blocks.ts). */
  task?: string;
  step?: string;
}

export interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
  /** Last-activity timestamp — the sidebar's recency-sort key. */
  updated_at?: number;
  /** Sidebar placement (see sessions-list helpers' LegacyConv). */
  sort_at?: number;
  sort_since?: number;
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
  /** The reply row the server writes for this turn, as the server names it. */
  reply_id?: string;
  func_name?: string;
  started_at?: number;
  execution_id?: string;
  status_version?: number;
  cancelling?: boolean;
  /** What the branch's foreground operation is: a reply, `compact`, a
   *  `rewind`, a `retry` or `edit`, a `function` run. */
  op_kind?: string;
  /** The operation's id; Stop names it for an operation with no execution. */
  op_id?: string;
  /** The id the page gave the request that started this run (a function
   *  run, a retry); Stop names the run by it before the server's answer
   *  names its execution. */
  client_request_id?: string;
  /** A step the running turn is in that the run indicator names
   *  (`compact` while the turn compacts its context). */
  phase?: string;
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
 * ``rowsByView[viewKey]`` holds one view's rows, keyed by id.
 * ``messageOrder[viewKey]`` holds that view's ordered id list. Split this
 * way so a streaming delta only touches one row and leaves
 * ``messageOrder`` untouched —
 * components that subscribe to the id list (e.g. the scroll container)
 * don't re-render per token, only bubbles subscribed to *their own*
 * id do. Matches the pattern Claude.ai / ChatGPT webapps use.
 *
 * Cross-conversation cleanup: removing a conversation drops its views
 * from both maps.
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
  fast_capability?: { status: string; source: string };
  /** The model's effort levels and default, as GET /api/agent_settings sends them. */
  thinking?: { options?: { value: string; desc?: string }[]; default?: string };
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
