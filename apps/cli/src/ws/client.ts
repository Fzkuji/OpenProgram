import WebSocket from 'ws';
import { backendAuthHeaders, isBackendUrl } from '../utils/backend.js';

export type ChatRequest = {
  action: 'chat';
  session_id?: string;
  agent_id?: string;
  text: string;
  thinking_effort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  permission_mode?: 'ask' | 'acceptEdits' | 'plan' | 'auto' | 'bypass';
  tools?: boolean;
  response_format?: JsonSchemaOutput | Record<string, unknown>;
};

export type JsonSchemaOutput = {
  type?: 'json_schema';
  schema: Record<string, unknown>;
  name?: string;
  description?: string;
  strict?: boolean;
  fallback?: 'auto' | 'none' | 'prompt';
  max_validation_retries?: 0 | 1;
};

export type WsRequest =
  | ChatRequest
  | { action: 'set_permission'; session_id: string; mode?: ChatRequest['permission_mode']; expected_version?: number; request_id?: string }
  | { action: 'stats' }
  | {
      action: 'execution.pause' | 'execution.continue' | 'execution.step' | 'execution.steer' | 'execution.cancel' | 'execution.fork' | 'execution.retry' | 'execution.wait.answer' | 'execution.wait.decline';
      type: 'execution.command';
      command_id: string;
      execution_id: string;
      expected_version: number;
      payload?: Record<string, unknown>;
    }
  | { action: 'set_attended'; session_id: string; attended: boolean }
  | { action: 'browser'; verb: string; args?: Record<string, unknown> }
  | { action: 'list_models' }
  | { action: 'switch_model'; model: string; provider?: string; session_id?: string }
  | { action: 'list_agents' }
  | { action: 'add_agent'; agent: Record<string, unknown> }
  | { action: 'delete_agent'; id: string }
  | { action: 'set_default_agent'; id: string }
  | { action: 'list_sessions' }
  | { action: 'list_jobs'; session_id?: string }
  | { action: 'get_job'; job_id: string }
  | { action: 'load_session'; session_id: string }
  | { action: 'delete_session'; session_id: string }
  | { action: 'search_messages'; query: string; limit?: number; agent_id?: string }
  | { action: 'list_channel_accounts' }
  | { action: 'add_channel_account'; channel: string; account_id: string; token: string }
  | { action: 'list_channel_bindings' }
  | { action: 'add_binding'; binding: Record<string, unknown> }
  | { action: 'remove_binding'; index: number }
  | { action: 'list_session_aliases' }
  | {
      action: 'attach_session';
      channel: string;
      account_id: string;
      // Mirrors server.py:attach_session — session_id + peer_kind +
      // peer_id are the fields the handler actually reads. Older
      // call sites passed `peer` / `conversation_id` which the
      // server silently dropped.
      session_id: string;
      peer_kind?: 'direct' | 'group' | 'channel';
      peer_id: string;
      // Legacy fields kept optional so unmigrated callers compile.
      peer?: string;
      conversation_id?: string;
    }
  | { action: 'detach_session'; channel: string; account_id: string; peer: string }
  | { action: 'get_settings'; session_id?: string }
  | { action: 'set_setting'; key: string; value: unknown }
  | { action: 'sandbox'; session_id?: string }
  | { action: 'context'; session_id?: string }
  | { action: 'compact'; session_id: string }
  | { action: 'rewind'; session_id: string }
  // Branch surface — mirrors webui/ws_actions/branch.py. handler.ts and
  // pickerRouter drive these from /branch and the branch picker;
  // head_msg_id omitted = the branch the session is checked out to.
  | { action: 'list_branches'; session_id: string }
  | { action: 'checkout_branch'; session_id: string; head_msg_id: string }
  | { action: 'rename_branch'; session_id: string; name: string; head_msg_id?: string }
  | { action: 'auto_name_branch'; session_id: string; head_msg_id?: string }
  | { action: 'delete_branch'; session_id: string; head_msg_id?: string }
  | { action: 'list_jobs'; session_id: string }
  | { action: 'get_job'; job_id: string }
  | { action: 'execution.replay'; execution_id: string; after_sequence: number };

export interface JobResource {
  admission_id?: string | null;
  resource_state: string;
  queue_wait?: {
    state: string;
    reason_code?: string | null;
    since?: number | null;
    position?: number | null;
  } | null;
  resource_lease_generation?: number | null;
  owner_instance_id?: string | null;
  limits: Record<string, unknown>;
  usage: {
    scope?: string;
    tokens?: { actual: number | null; reserved: number | null; limit: number | null };
    cost_usd?: {
      actual: string | null;
      reserved: string | null;
      limit: string | null;
      known: boolean | null;
      unknown_events: number | null;
    };
    runtime_seconds?: { used: number | null; limit: number | null };
    idle_seconds?: { used: number | null; limit: number | null };
    shared_remaining?: {
      tokens: number | null;
      cost_usd: string | null;
      cost_unknown_events: number | null;
    };
  };
  reservation?: Record<string, unknown> | null;
}

export interface JobResourceView {
  job_id: string;
  execution_id?: string;
  project_id?: string;
  session_id?: string;
  parent_execution_id?: string | null;
  status_version?: number;
  capabilities?: {
    pause: boolean; step: boolean; steer: boolean; fork: boolean; retry: boolean;
    safe_point_kinds: string[]; state_schema_version: number | null;
  };
  checkpoint_head_id?: string | null;
  event_cursor?: { execution_id: string; next_sequence: number; snapshot_status_version: number };
  execution?: Record<string, unknown>;
  resource?: JobResource | null;
  status: string;
}

export interface JobRow {
  id: string;
  execution_id?: string;
  status: string;
  status_version?: number;
  subject?: string;
  parent_session_id?: string;
  resource?: JobResourceView;
  [key: string]: unknown;
}

export interface ChatAck {
  type: 'chat_ack';
  data: {
    session_id: string;
    msg_id: string;
    execution_id?: string;
    status_version?: number;
  };
}

export interface ChatResponse {
  type: 'chat_response';
  data: {
    type: 'status' | 'stream_event' | 'result' | 'error' | 'follow_up_question' | 'cancelled' | 'tree_update' | 'context_stats' | string;
    content?: string;
    structured_output?: unknown;
    structured_output_mode?: 'native' | 'tool' | 'prompt';
    attempt?: number;
    code?: string;
    attempts?: number;
    issues?: Array<{ code: string; path?: string; schema_path?: string; message?: string }>;
    session_id?: string;
    execution_id?: string;
    wait_generation?: number;
    expected_version?: number;
    msg_id?: string;
    [k: string]: unknown;
  };
}

export interface EventEnvelope {
  type: 'event';
  event: string;
  data: Record<string, unknown>;
}

export interface AgentsListEnvelope {
  type: 'agents_list';
  data: Array<{ id: string; name: string; model?: string; default?: boolean; [k: string]: unknown }>;
}

export interface SessionsListEnvelope {
  type: 'sessions_list';
  data: Array<{
    id: string; title?: string; agent_id?: string; created_at?: number;
    source?: string; peer_display?: string; [k: string]: unknown;
  }>;
}

export interface ChannelBindingsEnvelope {
  type: 'channel_bindings';
  data: Array<{
    agent_id?: string;
    match?: { channel?: string; account_id?: string; peer?: string };
  }>;
}

export interface SessionAliasesEnvelope {
  type: 'session_aliases';
  // Server returns rows verbatim from session_aliases.json — peer is
  // the nested ``{kind, id}`` object, not a flat string. Older code
  // assumed a string and string-coerced the dict to "[object Object]"
  // when rendering, which silently broke the alias listing. Type the
  // shape the server actually emits so the TUI matches reality.
  data: Array<{
    channel?: string;
    account_id?: string;
    peer?: { kind?: string; id?: string };
    agent_id?: string;
    session_id?: string;
    created_at?: number;
  }>;
}

export interface SessionAliasRow {
  channel?: string;
  account_id?: string;
  peer?: { kind?: string; id?: string };
  agent_id?: string;
  session_id?: string;
  created_at?: number;
}

/**
 * Server-pushed notification that a session alias mutation just
 * landed (attach / detach). ``replaced`` carries the previous row
 * when ``attach_session`` overwrites an existing (channel, account,
 * peer) binding — letting the TUI tell the user "you just replaced
 * X" instead of treating attach as a silent destructive op.
 */
export interface SessionAliasChangedEnvelope {
  type: 'session_alias_changed';
  data: {
    action: 'attached' | 'detached';
    alias: SessionAliasRow;
    replaced?: SessionAliasRow | null;
  };
}

export interface ChannelAccountsEnvelope {
  type: 'channel_accounts';
  data: Array<{ channel?: string; id?: string; [k: string]: unknown }>;
}

export interface ChannelAccountAddedEnvelope {
  type: 'channel_account_added';
  data: { ok?: boolean; channel?: string; account_id?: string; error?: string };
}

export interface BrowserResultEnvelope {
  type: 'browser_result';
  data: { verb: string; result: string };
}

export interface SessionLoadedEnvelope {
  type: 'session_loaded';
  data: {
    id: string;
    messages: Array<{ role: string; content: string; [k: string]: unknown }>;
    settings?: {
      tools_enabled?: boolean | null;
      tools_override?: string[] | null;
      thinking_effort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | null;
      permission_mode?: 'ask' | 'acceptEdits' | 'plan' | 'auto' | 'bypass' | null;
    };
    [k: string]: unknown;
  };
}

export interface ModelsListEnvelope {
  type: 'models_list';
  data: { provider?: string; current?: string; models?: string[] };
}

export interface ModelSwitchedEnvelope {
  type: 'model_switched';
  data: { provider?: string; model?: string };
}

export interface StatsEnvelope {
  type: 'stats';
  data: {
    agent?: { id?: string; name?: string; model?: string } | null;
    // Counts — every tile in Welcome's 8-grid wants one of these.
    // Welcome falls back to top_*.length when the count is missing,
    // so it's resilient to old servers, but fresh servers send all
    // of these explicitly so 0 renders as '0' instead of '—'.
    agents_count?: number;
    programs_count?: number;
    skills_count?: number;
    conversations_count?: number;
    functions_count?: number;
    applications_count?: number;
    tools_count?: number;
    providers_count?: number;
    channels_count?: number;
    // Top-N preview lists — one entry per Welcome tile.
    top_programs?: Array<{ name?: string; category?: string }>;
    top_functions?: Array<{ name?: string; category?: string }>;
    top_applications?: Array<{ name?: string; category?: string }>;
    top_skills?: Array<{ name?: string; slug?: string }>;
    top_agents?: Array<{ id?: string; name?: string }>;
    top_sessions?: Array<{ id?: string; title?: string }>;
    top_tools?: string[];
    top_providers?: string[];
    top_channels?: Array<{ channel?: string; id?: string }>;
  };
}

export interface ErrorEnvelope {
  type: 'error';
  data?: { message?: string };
}

export interface OperationErrorEnvelope {
  type: 'operation_error';
  data?: {
    action?: unknown;
    code?: unknown;
    request_id?: unknown;
    session_id?: unknown;
    retryable?: unknown;
    message?: unknown;
  };
}

export interface LegacyActionErrorEnvelope {
  type: 'action_error';
  data?: OperationErrorEnvelope['data'];
}

/**
 * A complete inbound channel turn (user message + assistant reply) just
 * landed for some session. Emitted by the channels worker after it
 * persists the turn so any TUI / web client viewing that session_id can
 * append both messages to its transcript live, no /resume refresh.
 */
export interface ChannelTurnEnvelope {
  type: 'channel_turn';
  data: {
    session_id: string;
    agent_id?: string;
    user: { id?: string; text?: string; peer_display?: string; source?: string };
    assistant: { id?: string; text?: string; source?: string };
  };
}

/**
 * QR-login state-machine envelope. Server pushes these on every
 * phase of a wechat (and future QR-based) login: qr_ready /
 * scanned / confirmed / done / expired / error. The TUI renders
 * the ASCII QR + status text in a non-interactive picker until
 * ``done`` arrives.
 */
export interface QrLoginEnvelope {
  type: 'qr_login';
  data: {
    channel?: string;
    account_id?: string;
    phase: 'qr_ready' | 'scanned' | 'confirmed' | 'done' | 'expired' | 'error';
    url?: string;
    ascii?: string;
    message?: string;
    credentials?: Record<string, unknown>;
    already_configured?: boolean;
  };
}

/**
 * SessionDB FTS5 search results. Sent by the server in response to a
 * ``search_messages`` action; the TUI's /search command consumes these
 * to render a picker of matched messages with their session context.
 */
export interface SearchResultsEnvelope {
  type: 'search_results';
  data: {
    query: string;
    total: number;
    results: Array<{
      session_id: string;
      session_title?: string;
      session_source?: string;
      message_id: string;
      role: string;
      preview: string;
      content?: string;
      timestamp?: number;
    }>;
  };
}

/**
 * runtime.ask / confirm / tool-approval — the backend paused a function
 * and needs the user to decide. `question.asked` carries the request;
 * `question.replied` / `question.rejected` retract it (answered here,
 * elsewhere, or stopped). Same `data` shape as the web composer's
 * PendingDecision (apps/web/lib/session-store.ts) so the TUI reuses the
 * contract verbatim. Rendered in the input slot as a `question` picker.
 */
export interface QuestionAskedEnvelope {
  type: 'question.asked';
  data: {
    id: string;
    kind?: 'ask' | 'confirm' | 'approval' | 'form' | 'ask_many';
    prompt?: string;
    options?: string[];
    multi?: boolean;
    allow_custom?: boolean;
    detail?: string;
    /** approval-only: the gated tool + its args, for the danger summary. */
    tool?: string;
    args?: Record<string, unknown>;
    /** form-only: flat-object field schema (field name → field def). */
    schema?: Record<string, unknown>;
    /** ask_many-only: a packed group of questions. */
    questions?: unknown[];
    session_id?: string;
    [k: string]: unknown;
  };
}

export interface QuestionClosedEnvelope {
  type: 'question.replied' | 'question.rejected';
  data: { id: string; [k: string]: unknown };
}

export type PermissionChangedEnvelope = {
  type: 'permission_changed';
  data: { session_id: string; mode?: NonNullable<ChatRequest['permission_mode']>; version?: number; error?: string; request_id?: string };
};

export type WsEnvelope =
  | PermissionChangedEnvelope
  | ChatAck
  | ChatResponse
  | EventEnvelope
  | QuestionAskedEnvelope
  | QuestionClosedEnvelope
  | AgentsListEnvelope
  | SessionsListEnvelope
  | SessionLoadedEnvelope
  | StatsEnvelope
  | ModelsListEnvelope
  | ModelSwitchedEnvelope
  | ChannelBindingsEnvelope
  | SessionAliasesEnvelope
  | SessionAliasChangedEnvelope
  | ChannelAccountsEnvelope
  | ChannelAccountAddedEnvelope
  | BrowserResultEnvelope
  | ChannelTurnEnvelope
  | QrLoginEnvelope
  | SearchResultsEnvelope
  | ErrorEnvelope
  | OperationErrorEnvelope
  | LegacyActionErrorEnvelope
  | { type: 'jobs_list'; data: { session_id?: string | null; jobs: JobRow[] } }
  | { type: 'job'; data: { job: JobRow | null; error?: string } }
  | {
      type: 'job_status';
      data: {
        job_id: string;
        session_id?: string;
        status: string;
        resource?: JobResourceView;
      };
    }
  | {
      type: 'execution.replay';
      data: {
        execution_id: string;
        snapshot?: Record<string, unknown>;
        events?: Array<Record<string, unknown>>;
        event_cursor?: { execution_id: string; next_sequence: number; snapshot_status_version: number };
        error?: string;
      };
    }
  | { type: 'settings'; data: unknown[] }
  | {
      type: 'setting_result';
      data: { key: string; applied?: string; value?: unknown; note?: string; error?: string };
    }
  | { type: 'attended_changed'; data: { session_id: string; attended: boolean } }
  | {
      type: 'running_task';
      data: {
        session_id: string;
        msg_id?: string;
        func_name?: string;
        execution_id?: string;
        status_version?: number;
      };
    }
  | { type: 'running_task_clear'; data: { session_id: string } }
  | {
      type: 'execution.updated';
      execution: {
        execution_id: string;
        session_id?: string;
        status?: string;
        reason_code?: string;
        status_version?: number;
      };
    }
  | {
      type: 'execution.command.updated';
      command?: {
        command_id?: string;
        execution_id?: string;
        status?: string;
        latest_snapshot?: { execution_id?: string; status_version?: number };
      };
      execution?: Record<string, unknown>;
      event_cursor?: { execution_id?: string; next_sequence?: number; snapshot_status_version?: number };
      data?: {
        command?: {
          command_id?: string;
          execution_id?: string;
          status?: string;
          latest_snapshot?: { execution_id?: string; status_version?: number };
        };
        execution?: Record<string, unknown>;
        event_cursor?: { execution_id?: string; next_sequence?: number; snapshot_status_version?: number };
      };
    }
  | { type: 'spawn_job_result'; data: Record<string, unknown> }
  // Branch frames (webui/ws_actions/branch.py) — the list reply plus
  // the structural-change broadcasts useWsEvents re-fetches on.
  | { type: 'branches_list'; data: { session_id: string; branches?: unknown[]; active?: string } }
  | { type: 'branch_renamed'; data: { session_id: string; head_msg_id?: string; name?: string } }
  | { type: 'branch_name_deleted'; data: { session_id: string; head_msg_id?: string } }
  | { type: 'branch_deleted'; data: { session_id: string; head_msg_id?: string } }
  | { type: 'branch_checked_out'; data: { session_id: string; head_msg_id?: string } }
  | { type: 'pong' };

export type WsListener = (ev: WsEnvelope) => void;
export type ConnectionState = 'connecting' | 'connected' | 'disconnected';
export type StateListener = (state: ConnectionState) => void;

export class BackendClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<WsListener>();
  private stateListeners = new Set<StateListener>();
  private url: string;
  private retry = 0;
  private state: ConnectionState = 'connecting';
  private queue: WsRequest[] = [];
  private hasConnected = false;
  private executionCursors = new Map<string, number>();

  constructor(url: string) {
    this.url = url;
  }

  private setState(next: ConnectionState): void {
    if (this.state === next) return;
    this.state = next;
    for (const l of this.stateListeners) l(next);
  }

  getState(): ConnectionState {
    return this.state;
  }

  connect(): void {
    this.setState('connecting');
    // The owner token rides in the Authorization header, never in the URL
    // query, and only when the URL is our own backend.
    this.ws = new WebSocket(
      this.url,
      isBackendUrl(this.url) ? { headers: backendAuthHeaders() } : undefined,
    );
    this.ws.on('open', () => {
      this.setState('connected');
      this.hasConnected = true;
      this.retry = 0;
      const q = this.queue.splice(0);
      for (const a of q) this.send(a);
      for (const [execution_id, after_sequence] of this.executionCursors) {
        this.send({ action: 'execution.replay', execution_id, after_sequence });
      }
    });
    this.ws.on('message', (raw) => {
      try {
        const parsed = JSON.parse(String(raw));
        if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
          this.trackExecutionCursor(parsed as Record<string, unknown>);
          for (const l of this.listeners) l(parsed as WsEnvelope);
        }
      } catch {
        // ignore
      }
    });
    this.ws.on('close', () => {
      this.setState('disconnected');
      const baseDelay = this.hasConnected ? 200 : 50;
      const delay = Math.min(5000, baseDelay * Math.pow(2, this.retry++));
      setTimeout(() => this.connect(), delay);
    });
    this.ws.on('error', () => {
      // close handler will reconnect
    });
  }

  send(req: WsRequest): void {
    if (this.state !== 'connected' || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.queue.push(req);
      return;
    }
    this.ws.send(JSON.stringify(req));
  }

  private trackExecutionCursor(frame: Record<string, unknown>): void {
    const data = (frame.data && typeof frame.data === 'object' ? frame.data : frame) as Record<string, unknown>;
    const raw = data.event_cursor;
    if (!raw || typeof raw !== 'object') return;
    const cursor = raw as { execution_id?: unknown; next_sequence?: unknown };
    if (typeof cursor.execution_id !== 'string' || !Number.isSafeInteger(cursor.next_sequence)) return;
    const next = Number(cursor.next_sequence);
    const previous = this.executionCursors.get(cursor.execution_id);
    this.executionCursors.set(cursor.execution_id, next - 1);
    if (previous !== undefined && next > previous + 2) {
      this.send({ action: 'execution.replay', execution_id: cursor.execution_id, after_sequence: previous });
    }
  }

  on(listener: WsListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  close(): void {
    this.ws?.removeAllListeners('close');
    this.ws?.close();
  }
}
