import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export type MessageStatus =
  | "pending"
  | "streaming"
  | "running"
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
  multiline?: boolean;
  options?: string[];
  options_from?: string;
}

export interface AgenticFunction {
  name: string;
  description?: string;
  category?: string;
  workdir_mode?: "optional" | "required" | "hidden";
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
  isError?: boolean;
  status: "running" | "done" | "error";
}

export interface ChatMsg {
  id: string;                  // msg_id from server, or local generated for user msgs
  role: "user" | "assistant" | "system";
  content: string;             // final assistant text / user text
  /** Reasoning tokens streamed under a collapsible "Thinking" block. */
  thinking?: string;
  /** Tool calls made during this assistant turn, in emit order. */
  tools?: ChatToolCall[];
  status?: MessageStatus;
  function?: string;           // if this was /run
  display?: "runtime" | "normal";
  timestamp?: number;
  attempts?: { content: string; timestamp: number; tree?: TreeNode; usage?: unknown }[];
  current_attempt?: number;
  /** Server response type — "result" / "error". Drives the runtime
   *  block's error styling and the assistant bubble's error branch. */
  rawType?: string;
  /** Execution tree captured with a `/run` result, rendered inside the
   *  runtime block. */
  contextTree?: TreeNode;
  /** Provider usage for the runtime block footer. Opaque — passed
   *  straight to the legacy `formatUsageFooterLabel`. */
  usage?: unknown;
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
  };
  /** Which agent produced this turn. Same-session multi-agent: a
   *  conversation can have N agents writing branches in the same
   *  session repo; the UI uses this to colour / label / avatar
   *  each turn by author. */
  agentId?: string;
}

export interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
}

interface RunningTask {
  session_id: string;
  msg_id: string;
  func_name?: string;
  started_at?: number;
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

interface ConvState {
  /** WS status for UI. */
  wsStatus: "connecting" | "open" | "closed";
  /** Agent settings state for the topbar Chat / Exec badges. Mirror
   *  of ``window._agentSettings``; populated by legacy providers.js. */
  agentSettings: AgentSettingsState;
  setAgentSettings: (s: AgentSettingsState) => void;
  /** Branch chip display state for the current conversation. */
  branchInfo: BranchBadgeInfo;
  setBranchInfo: (b: BranchBadgeInfo) => void;
  /** Status badge label + tone for the topbar. */
  statusBadge: StatusBadgeInfo;
  setStatusBadge: (b: StatusBadgeInfo) => void;
  /** Summary for sidebar Recents list. */
  conversations: Record<string, ConvSummary>;
  /** Every message ever loaded, keyed by id. */
  messagesById: Record<string, ChatMsg>;
  /** Ordered id list per conversation. */
  messageOrder: Record<string, string[]>;
  /** Currently active conversation id. */
  currentSessionId: string | null;
  /** Currently running task (show Stop button).
   *  Deprecated single-session field — read ``runningTasks[sid]``
   *  instead. Kept here so legacy ``setRunning(false)`` keeps working
   *  while callers migrate. */
  runningTask: RunningTask | null;
  /** Per-session running task map. Drives the composer send/stop
   *  button (current session) and the sidebar breathing indicator
   *  (all sessions). */
  runningTasks: Record<string, RunningTask>;
  /** Paused flag. */
  paused: boolean;
  /** Provider info shown in header. */
  providerInfo: { provider?: string; model?: string; type?: string } | null;
  /** Latest live Context tree per conversation. */
  trees: Record<string, TreeNode>;
  setTree: (sessionId: string, tree: TreeNode) => void;

  /** Per-conversation token usage from the latest context_stats event.
   *  cache_create = cache write tokens (Anthropic-style); model / provider
   *  are also surfaced so the badge can render "gpt-5" / "claude-sonnet"
   *  next to the numbers. */
  tokens: Record<string, {
    input?: number;
    output?: number;
    cache_read?: number;
    cache_create?: number;
    model?: string | null;
    provider?: string | null;
  }>;
  /** Per-conversation context window size (model-dependent). */
  contextWindow: Record<string, number>;
  setContextStats: (
    sessionId: string,
    chat: {
      input?: number;
      output?: number;
      cache_read?: number;
      cache_create?: number;
      model?: string | null;
      provider?: string | null;
    } | null,
    contextWindow?: number | null,
  ) => void;

  setWsStatus: (s: ConvState["wsStatus"]) => void;
  setConversations: (list: ConvSummary[]) => void;
  upsertConversation: (c: ConvSummary) => void;
  removeConversation: (id: string) => void;
  clearConversations: () => void;
  setCurrentConv: (id: string | null) => void;
  setMessages: (sessionId: string, msgs: ChatMsg[]) => void;
  appendMessage: (sessionId: string, msg: ChatMsg) => void;
  updateMessage: (sessionId: string, msgId: string, patch: Partial<ChatMsg>) => void;
  /** Truncate messages at and after msgId. Used by retry to drop the
   *  stale reply before the new one streams in. */
  truncateFrom: (sessionId: string, msgId: string) => void;
  setRunningTask: (t: RunningTask | null) => void;
  /** Set / clear the running task for a specific session. Pass null
   *  to clear. Used by the per-session running-task WS events so two
   *  sessions can have independent run state. */
  setRunningTaskFor: (sessionId: string, t: RunningTask | null) => void;
  setPaused: (p: boolean) => void;
  setProviderInfo: (p: ConvState["providerInfo"]) => void;

  /** Welcome screen visibility — true when chat-area should show the
   *  logo / title / example buttons. Owned by React; legacy
   *  setWelcomeVisible() in helpers.js writes through here. */
  welcomeVisible: boolean;
  setWelcomeVisible: (v: boolean) => void;

  /** Controlled value of the Composer's textarea. Lifted into the
   *  store so outside callers (welcome example buttons, retry
   *  helpers, etc.) can fill the input. */
  composerInput: string;
  /** Per-session draft cache. Persisted to localStorage so unsent text
   *  survives refresh and session switching. ``composerInput`` is the
   *  live draft for the *current* session and stays mirrored here. */
  composerDrafts: Record<string, string>;
  setComposerInput: (s: string) => void;
  /** Bump to ask the Composer to call .focus() on its textarea. The
   *  Composer reacts to changes in this counter via useEffect. */
  composerFocusTick: number;
  focusComposer: () => void;

  /** When non-null, the Composer swaps its textarea for a parameter
   *  form for this function. Submit builds a `run <name> ...` command
   *  and sends it through the chat WS channel, then clears this. */
  fnFormFunction: AgenticFunction | null;
  openFnForm: (fn: AgenticFunction) => void;
  closeFnForm: () => void;
  /** True between the close click and the wrapper-height transition
   *  end — `fnFormFunction` stays non-null through the close animation
   *  (the form must stay mounted to animate), so other components that
   *  react to the form opening/closing (e.g. the welcome screen's
   *  examples row) read this to start their own transition in sync
   *  with the form shrinking, not a beat later when it unmounts. */
  fnFormClosing: boolean;
  setFnFormClosing: (v: boolean) => void;

  /** Right sidebar dock state. `open` = expanded (icons + content
   *  visible); when false, only the icon rail shows (collapsed).
   *  `view` selects which child of `.right-view-host` is visible
   *  (matches the legacy `data-view` attribute: "history" | "detail").
   *  Persisted to localStorage under `rightSidebarOpen` /
   *  `rightSidebarView` so the legacy keys keep working — that's the
   *  same shape the old right-dock.js wrote. */
  rightDock: { open: boolean; view: string };
  setRightDockOpen: (open: boolean) => void;
  setRightDockView: (view: string) => void;
}

const RIGHT_LS_OPEN = "rightSidebarOpen";
const RIGHT_LS_VIEW = "rightSidebarView";
const VALID_VIEWS = new Set(["history", "context"]);

function readRightDock(): { open: boolean; view: string } {
  if (typeof window === "undefined") return { open: false, view: "history" };
  let open = false;
  try {
    const o = localStorage.getItem(RIGHT_LS_OPEN);
    if (o === "1") open = true;
    else if (o === "0") open = false;
  } catch {
    /* ignore */
  }
  // Persist history / commits (Context tab) across reload — both have
  // content that is meaningful as soon as the panel mounts. "detail" is
  // intentionally excluded: it needs a node selection that doesn't
  // survive a page reload, so restoring it would land on a blank
  // "No execution selected" panel. Anything we don't recognise (legacy
  // value, future tab) collapses back to history.
  let view = "history";
  try {
    const v = localStorage.getItem(RIGHT_LS_VIEW);
    if (v && VALID_VIEWS.has(v)) view = v;
  } catch {
    /* ignore */
  }
  return { open, view };
}

function persistRightDock(state: { open: boolean; view: string }) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(RIGHT_LS_OPEN, state.open ? "1" : "0");
    if (VALID_VIEWS.has(state.view)) {
      localStorage.setItem(RIGHT_LS_VIEW, state.view);
    }
  } catch {
    /* ignore */
  }
}

// Composer drafts — persist per-session unsent input across refresh and
// session switch. Keyed by sessionId; the "new" pseudo-key holds the draft
// for the not-yet-created next session (before the user has any chats).
// One JSON blob in localStorage so we don't litter keys per session.
const COMPOSER_DRAFTS_KEY = "composerDrafts";
const COMPOSER_DRAFTS_VERSION = 1;
const COMPOSER_NEW_KEY = "__new__";

function readComposerDrafts(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(COMPOSER_DRAFTS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && parsed.v === COMPOSER_DRAFTS_VERSION
        && parsed.drafts && typeof parsed.drafts === "object") {
      return parsed.drafts as Record<string, string>;
    }
  } catch {
    /* ignore */
  }
  return {};
}

function persistComposerDrafts(drafts: Record<string, string>) {
  if (typeof window === "undefined") return;
  try {
    // Drop empty entries so the blob doesn't grow unboundedly.
    const compact: Record<string, string> = {};
    for (const k in drafts) {
      if (drafts[k]) compact[k] = drafts[k];
    }
    localStorage.setItem(
      COMPOSER_DRAFTS_KEY,
      JSON.stringify({ v: COMPOSER_DRAFTS_VERSION, drafts: compact }),
    );
  } catch {
    /* ignore */
  }
}

export const useSessionStore = create<ConvState>((set) => ({
  wsStatus: "connecting",
  agentSettings: {},
  setAgentSettings: (s) => set({ agentSettings: s }),
  branchInfo: { visible: false, name: "main", count: 0 },
  setBranchInfo: (b) => set({ branchInfo: b }),
  statusBadge: {
    label: "connecting…",
    tone: "connecting",
    paused: false,
    title: "Connecting…",
  },
  setStatusBadge: (b) => set({ statusBadge: b }),
  conversations: {},
  messagesById: {},
  messageOrder: {},
  currentSessionId: null,
  runningTask: null,
  runningTasks: {},
  paused: false,
  providerInfo: null,
  trees: {},
  setTree: (sessionId, tree) =>
    set((s) => ({ trees: { ...s.trees, [sessionId]: tree } })),

  tokens: {},
  contextWindow: {},
  setContextStats: (sessionId, chat, ctxWindow) =>
    set((s) => {
      const next: Partial<ConvState> = {};
      if (chat) {
        next.tokens = {
          ...s.tokens,
          [sessionId]: {
            input: chat.input,
            output: chat.output,
            cache_read: chat.cache_read,
            cache_create: chat.cache_create,
            model: chat.model,
            provider: chat.provider,
          },
        };
      }
      if (typeof ctxWindow === "number" && ctxWindow > 0) {
        next.contextWindow = { ...s.contextWindow, [sessionId]: ctxWindow };
      }
      return next;
    }),

  setWsStatus: (s) => set({ wsStatus: s }),

  setConversations: (list) =>
    set({
      conversations: Object.fromEntries(list.map((c) => [c.id, c])),
    }),

  upsertConversation: (c) =>
    set((s) => ({ conversations: { ...s.conversations, [c.id]: c } })),

  removeConversation: (id) =>
    set((s) => {
      const rest = { ...s.conversations };
      delete rest[id];
      const order = { ...s.messageOrder };
      const doomed = order[id] ?? [];
      delete order[id];
      const byId = { ...s.messagesById };
      for (const mid of doomed) delete byId[mid];
      // Prune the deleted session's composer draft so the draft blob
      // doesn't grow unboundedly over the lifetime of the tab. Paste
      // entries referenced only by this draft are GC'd by the composer
      // (it watches ``composerDrafts`` and retains only still-referenced
      // ids in pasteStore).
      const nextDrafts = { ...s.composerDrafts };
      delete nextDrafts[id];
      persistComposerDrafts(nextDrafts);
      return {
        conversations: rest,
        messageOrder: order,
        messagesById: byId,
        currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
        composerDrafts: nextDrafts,
      };
    }),

  clearConversations: () =>
    set((s) => {
      // Wipe every per-session draft too, but keep the "__new__" draft
      // (the textarea content for the not-yet-created next session) —
      // the user is still looking at the composer and would lose what
      // they're typing otherwise.
      const newDraft = s.composerDrafts[COMPOSER_NEW_KEY];
      const nextDrafts: Record<string, string> = newDraft
        ? { [COMPOSER_NEW_KEY]: newDraft }
        : {};
      persistComposerDrafts(nextDrafts);
      return {
        conversations: {},
        messagesById: {},
        messageOrder: {},
        currentSessionId: null,
        composerDrafts: nextDrafts,
      };
    }),

  setCurrentConv: (id) =>
    set((s) => {
      // Stash the currently-visible draft under its owning session
      // (or under the "new" placeholder if no session was active),
      // then load the incoming session's draft.
      const oldSid = s.currentSessionId ?? COMPOSER_NEW_KEY;
      const drafts = { ...s.composerDrafts, [oldSid]: s.composerInput };
      const nextSid = id ?? COMPOSER_NEW_KEY;
      const nextInput = drafts[nextSid] ?? "";
      persistComposerDrafts(drafts);
      return {
        currentSessionId: id,
        // Keep the legacy single-task mirror pointed at whatever the
        // newly-active session's task is. This is what makes the
        // composer flip between send/stop instantly on session switch.
        runningTask: id ? (s.runningTasks[id] ?? null) : null,
        composerInput: nextInput,
        composerDrafts: drafts,
      };
    }),

  setMessages: (sessionId, msgs) =>
    set((s) => {
      // Drop any old ids for this conv so stale entries don't leak.
      const byId = { ...s.messagesById };
      for (const oldId of s.messageOrder[sessionId] ?? []) delete byId[oldId];
      for (const m of msgs) byId[m.id] = m;
      return {
        messagesById: byId,
        messageOrder: { ...s.messageOrder, [sessionId]: msgs.map((m) => m.id) },
      };
    }),

  appendMessage: (sessionId, msg) =>
    set((s) => ({
      messagesById: { ...s.messagesById, [msg.id]: msg },
      messageOrder: {
        ...s.messageOrder,
        [sessionId]: [...(s.messageOrder[sessionId] ?? []), msg.id],
      },
    })),

  updateMessage: (_sessionId, msgId, patch) =>
    set((s) => {
      const cur = s.messagesById[msgId];
      if (!cur) return {};
      return {
        messagesById: { ...s.messagesById, [msgId]: { ...cur, ...patch } },
      };
    }),

  truncateFrom: (sessionId, msgId) =>
    set((s) => {
      const order = s.messageOrder[sessionId];
      if (!order) return {};
      const idx = order.indexOf(msgId);
      if (idx < 0) return {};
      const dropped = order.slice(idx);
      const nextOrder = order.slice(0, idx);
      const byId = { ...s.messagesById };
      for (const d of dropped) delete byId[d];
      return {
        messagesById: byId,
        messageOrder: { ...s.messageOrder, [sessionId]: nextOrder },
      };
    }),

  setRunningTask: (t) => set({ runningTask: t }),
  setRunningTaskFor: (sessionId, t) =>
    set((s) => {
      const next = { ...s.runningTasks };
      if (t) next[sessionId] = t;
      else delete next[sessionId];
      // Mirror to the legacy single-task field if this is the active
      // session, so anything still reading ``runningTask`` (e.g. older
      // call sites) keeps in sync with the composer's view.
      const isCurrent = s.currentSessionId === sessionId;
      return {
        runningTasks: next,
        runningTask: isCurrent ? t : s.runningTask,
      };
    }),
  setPaused: (p) => set({ paused: p }),
  setProviderInfo: (p) => set({ providerInfo: p }),

  welcomeVisible: false,
  setWelcomeVisible: (v) => set({ welcomeVisible: v }),

  // Hydrate the live draft for the "new session" placeholder at module
  // load — same pattern as ``rightDock`` above. SSR sees an empty
  // string (readComposerDrafts returns {} on the server); the client
  // shadows it with whatever survived in localStorage.
  composerInput: readComposerDrafts()[COMPOSER_NEW_KEY] ?? "",
  composerDrafts: readComposerDrafts(),
  setComposerInput: (s) =>
    set((state) => {
      const sid = state.currentSessionId ?? COMPOSER_NEW_KEY;
      const drafts = { ...state.composerDrafts, [sid]: s };
      // Persist on every keystroke. Cheap (one JSON.stringify per
      // session-count) and matches the "right dock" pattern above.
      persistComposerDrafts(drafts);
      return { composerInput: s, composerDrafts: drafts };
    }),
  composerFocusTick: 0,
  focusComposer: () =>
    set((state) => ({ composerFocusTick: state.composerFocusTick + 1 })),

  fnFormFunction: null,
  openFnForm: (fn) => set({ fnFormFunction: fn, fnFormClosing: false }),
  closeFnForm: () => set({ fnFormFunction: null, fnFormClosing: false }),
  fnFormClosing: false,
  setFnFormClosing: (v) => set({ fnFormClosing: v }),

  rightDock: readRightDock(),
  setRightDockOpen: (open) =>
    set((s) => {
      const next = { ...s.rightDock, open };
      persistRightDock(next);
      return { rightDock: next };
    }),
  setRightDockView: (view) =>
    set((s) => {
      const next = { ...s.rightDock, view };
      persistRightDock(next);
      return { rightDock: next };
    }),
}));


/**
 * Subscribe to the id list for a conversation. Returns a stable array
 * reference as long as the id sequence hasn't changed — a streaming
 * content update on an existing message will NOT re-render consumers
 * of this hook.
 */
export function useMessageIds(sessionId: string | null): string[] {
  return useSessionStore(
    useShallow((s) =>
      sessionId ? s.messageOrder[sessionId] ?? EMPTY_IDS : EMPTY_IDS
    )
  );
}

/**
 * Subscribe to one message. Re-renders only when that specific
 * message's entry changes — other messages streaming, ids being
 * added/removed etc. don't affect this hook's consumer.
 */
export function useMessageById(msgId: string): ChatMsg | undefined {
  return useSessionStore((s) => s.messagesById[msgId]);
}

const EMPTY_IDS: string[] = [];
