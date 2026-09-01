/**
 * Cross-module mutable runtime state.
 *
 * These were top-level `var`s in the legacy `public/js/shared/state.js`,
 * then lived on `window.*` while the migration to React was in flight.
 * The legacy scripts are gone, so this is a plain module-level singleton
 * that every consumer imports directly.
 *
 * Mutate fields on the exported `runtimeState` object; never rebind the
 * object itself — importers hold the same reference.
 */

export interface TreeEntry {
  path?: string;
  name?: string;
  [k: string]: unknown;
}

export interface AgentSettings {
  chat?: Record<string, unknown>;
  exec?: Record<string, unknown>;
  available?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ThinkingConfig {
  options?: { value: string; desc: string }[];
  default?: string;
}

export interface ProgramsMeta {
  favorites?: string[];
  folders?: Record<string, unknown>;
  icons?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RuntimeState {
  /** Shared app WebSocket, owned by `lib/net/use-ws.ts`. */
  ws: WebSocket | null;
  /** File trees for the active session. */
  trees: TreeEntry[];
  /** Currently selected code-tree node path. */
  selectedPath: string | null;
  isPaused: boolean;
  isRunning: boolean;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  /** Derived from the URL; kept in lockstep by AppShell's route effect. */
  currentSessionId: string | null;
  /** Heavy per-session conversation map (the sidebar mirrors summaries). */
  conversations: Record<string, Record<string, unknown>>;
  availableFunctions: unknown[];
  pendingResponses: Record<string, unknown>;
  sidebarOpen: boolean;
  _skipScrollToBottom: boolean;
  programsMeta: ProgramsMeta;
  _thinkingEffort: string | null;
  _execThinkingEffort: string | null;
  _thinkingConfig: ThinkingConfig | null;
  _lastChatProvider: string | null;
  _lastChatModel: string | null;
  _lastExecProvider: string | null;
  _lastExecModel: string | null;
  _agentSettings: AgentSettings;
  _elapsedTimer: ReturnType<typeof setInterval> | null;
  _hasActiveSession: boolean;
  _toolsEnabled: boolean;
  _webSearchEnabled: boolean;
  _webSearchProviderLabel: string;
  _webSearchProviderTier: string;
  /** Channel/account the next send should use. Owned by
   *  `draft-channel-choice.ts`, which proxies its host onto this field. */
  _pendingChannelChoice: {
    channel: string | null;
    account_id: string | null;
  } | null;
  /** Branch rows per conversation, filled by `fetchBranches`. */
  _branchesByConv: Record<string, unknown[]>;
  /** Sessions whose branch was just moved by retry/edit: the next turn
   *  result triggers a wholesale load_session instead of a mirror push
   *  (chat-handlers self-heal — the reload and the stream race). */
  _pendingBranchReload: Record<string, boolean>;
  /** DAG node id → lane colour, published by the DAG render pass so the
   *  branches panel can match its rows to the graph's lanes. */
  _branchLaneColorMap: Record<string, string>;
  /** Message id the transcript should scroll to after the next
   *  `load_session` that follows a branch checkout. Set by the checkout
   *  callers, read + cleared once by `renderConversation`. */
  _postCheckoutScrollTo: string | null;
  /** After returning to the parent branch: the sub-agent row to reveal.
   *  ``head`` = attach.head_id, ``anchor`` = the reply that dispatched
   *  the spawn. MessageList opens the owning strip and flashes the row. */
  _pendingExpandAttach: { head: string; anchor: string } | null;
  /** Session ids whose transcripts must be reloaded when their running tasks
   *  clear (dispatched function runs whose cards are already on disk). */
  __reloadOnTaskClear: Set<string>;
}

function initialSessionId(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/s\/([^/]+)/);
  return m ? m[1] : null;
}

function initialSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem("sidebarOpen") !== "0";
  } catch {
    return true;
  }
}

export const runtimeState: RuntimeState = {
  ws: null,
  trees: [],
  selectedPath: null,
  isPaused: false,
  isRunning: false,
  reconnectTimer: null,
  currentSessionId: initialSessionId(),
  conversations: {},
  availableFunctions: [],
  pendingResponses: {},
  sidebarOpen: initialSidebarOpen(),
  _skipScrollToBottom: false,
  programsMeta: { favorites: [], folders: {} },
  _thinkingEffort: null,
  _execThinkingEffort: null,
  _thinkingConfig: null,
  _lastChatProvider: null,
  _lastChatModel: null,
  _lastExecProvider: null,
  _lastExecModel: null,
  _agentSettings: { chat: {}, exec: {}, available: {} },
  _elapsedTimer: null,
  _hasActiveSession: false,
  // ui.ts hydrates these from localStorage on first plus-menu render;
  // `false` matches the previous `undefined` (falsy) starting point.
  _toolsEnabled: false,
  _webSearchEnabled: false,
  _webSearchProviderLabel: "",
  _webSearchProviderTier: "",
  _pendingChannelChoice: null,
  _branchesByConv: {},
  _pendingBranchReload: {},
  _branchLaneColorMap: {},
  _postCheckoutScrollTo: null,
  _pendingExpandAttach: null,
  __reloadOnTaskClear: new Set<string>(),
};

/** The shared app socket, or undefined when not connected yet. */
export function getSocket(): WebSocket | undefined {
  return runtimeState.ws ?? undefined;
}

export function setSocket(sock: WebSocket | null): void {
  runtimeState.ws = sock;
}
