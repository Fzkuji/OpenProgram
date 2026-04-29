import WebSocket from 'ws';

export type ChatRequest = {
  action: 'chat';
  conv_id?: string;
  agent_id?: string;
  text: string;
  thinking_effort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  permission_mode?: 'ask' | 'auto' | 'bypass';
  tools?: boolean;
};

export type WsRequest =
  | ChatRequest
  | { action: 'sync' }
  | { action: 'stats' }
  | { action: 'stop'; conv_id: string }
  | { action: 'browser'; verb: string; args?: Record<string, unknown> }
  | { action: 'list_models' }
  | { action: 'switch_model'; model: string; provider?: string; conv_id?: string }
  | { action: 'list_agents' }
  | { action: 'add_agent'; agent: Record<string, unknown> }
  | { action: 'delete_agent'; id: string }
  | { action: 'set_default_agent'; id: string }
  | { action: 'list_conversations' }
  | { action: 'load_conversation'; conv_id: string }
  | { action: 'delete_conversation'; id: string }
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
  | { action: 'detach_session'; channel: string; account_id: string; peer: string };

export interface ChatAck {
  type: 'chat_ack';
  data: { conv_id: string; msg_id: string };
}

export interface ChatResponse {
  type: 'chat_response';
  data: {
    type: 'status' | 'stream_event' | 'result' | 'error' | 'follow_up_question' | 'cancelled' | 'tree_update' | 'context_stats' | string;
    content?: string;
    conv_id?: string;
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

export interface ConversationsListEnvelope {
  type: 'conversations_list';
  data: Array<{ id: string; title?: string; agent_id?: string; updated_at?: number; [k: string]: unknown }>;
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

export interface ConversationLoadedEnvelope {
  type: 'conversation_loaded';
  data: {
    id: string;
    messages: Array<{ role: string; content: string; [k: string]: unknown }>;
    settings?: {
      tools_enabled?: boolean | null;
      tools_override?: string[] | null;
      thinking_effort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | null;
      permission_mode?: 'ask' | 'auto' | 'bypass' | null;
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

export interface HistoryListEnvelope {
  type: 'history_list';
  data: Array<{ id?: string; title?: string; created_at?: number; agent_id?: string }>;
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

/**
 * A complete inbound channel turn (user message + assistant reply) just
 * landed for some session. Emitted by the channels worker after it
 * persists the turn so any TUI / web client viewing that conv_id can
 * append both messages to its transcript live, no /resume refresh.
 */
export interface ChannelTurnEnvelope {
  type: 'channel_turn';
  data: {
    conv_id: string;
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

export type WsEnvelope =
  | ChatAck
  | ChatResponse
  | EventEnvelope
  | AgentsListEnvelope
  | ConversationsListEnvelope
  | ConversationLoadedEnvelope
  | StatsEnvelope
  | ModelsListEnvelope
  | ModelSwitchedEnvelope
  | HistoryListEnvelope
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
    this.ws = new WebSocket(this.url);
    this.ws.on('open', () => {
      this.setState('connected');
      this.retry = 0;
      const q = this.queue.splice(0);
      for (const a of q) this.send(a);
    });
    this.ws.on('message', (raw) => {
      try {
        const parsed = JSON.parse(String(raw));
        if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
          for (const l of this.listeners) l(parsed as WsEnvelope);
        }
      } catch {
        // ignore
      }
    });
    this.ws.on('close', () => {
      this.setState('disconnected');
      const delay = Math.min(5000, 200 * Math.pow(2, this.retry++));
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
