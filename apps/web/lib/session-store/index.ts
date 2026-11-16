import { useShallow } from "zustand/react/shallow";
import { createWithEqualityFn } from "zustand/traditional";

import {
  decideExecutionUpdateOrder,
  removeExecutionUpdateOrders,
  type ExecutionUpdateOrder,
} from "@/lib/net/execution/execution-update-order";
import {
  readSessionDraftState,
  replaceSessionDraftState,
  updateSessionDraftState,
} from "@/lib/chat/send/session-draft-persistence";
import {
  draftChannelChoiceHost,
  type DraftChannelChoiceHost,
} from "../runtime-bridge/actions/draft-channel-choice";
import type { SessionTransferSnapshot } from "../tabs/transfer/tab-transfer-journal";
import { forgetTakeLatestSession } from "@/lib/chat/history/chat-scroll";
import { setRunningConversations } from "@/lib/browser/control/browser-control";
import { noteMessageArrival, rekeyMessageArrival } from "./messages/message-arrivals";
import {
  dropSessionStore,
  installScopeWriteThrough,
  pushToSessionStore,
} from "./session-scope-registry";


export type {
  AgentBadgeInfo,
  AgentSettingsState,
  AgenticFunction,
  AskOne,
  AssistantBlock,
  BranchBadgeInfo,
  ChatMsg,
  ChatToolCall,
  Citation,
  ComposerSettings,
  ConvSummary,
  FnParam,
  FormFieldSchema,
  MessageStatus,
  NoticeItem,
  PendingDecision,
  RetryStatus,
  RunningTask,
  StatusBadgeInfo,
  StatusTone,
  TreeNode,
} from "./types";
import type {
  AgentSettingsState,
  AgenticFunction,
  BranchBadgeInfo,
  ChatMsg,
  ComposerSettings,
  ConvSummary,
  PendingDecision,
  RunningTask,
  StatusBadgeInfo,
  TreeNode,
} from "./types";
import {
  withMessageTimestamp,
} from "./messages/message-timestamp";

import { settlePendingSends } from './messages/pending-sends';
import { ownsSubAgentChatKey } from '@/lib/chat/transcript/sub-agent-keys';
import {
  applyLocalChange as applyLocalChangeToView,
  applyReadRows,
  withFrameOnlyFields,
  standingOverlays,
  withOverlays,
  type LocalChange,
  type OverlayEvent,
  type RowOverlay,
  type TextFrame,
  type ViewRows,
} from '@/lib/chat/transcript/apply-transcript-update';
import { getPendingUserMessageId } from '@/lib/chat/send/pending-user-text';

/** Every transcript row gets its time at first materialization. Persisted
 * rows keep their authoritative value; live/legacy rows missing one use the
 * local arrival time. Later patches spread over the stored row and therefore
 * cannot replace this value unless they explicitly carry a timestamp. */

interface ConvState {
  /** WS status for UI. */
  wsStatus: "connecting" | "open" | "closed";
  /** Agent settings state for the topbar Chat / Exec badges. Mirror
   *  of ``window._agentSettings``; populated by legacy providers.js. */
  agentSettings: AgentSettingsState;
  /** Per side: an object replaces, ``null`` CLEARS (the chip hides),
   *  ``undefined`` keeps the previous value (partial update). Clearing
   *  matters: when the selected model is disabled in Settings the
   *  refetched settings come back empty, and keep-previous semantics
   *  would pin the stale model on the top-bar chip forever. */
  setAgentSettings: (s: {
    chat?: AgentSettingsState["chat"] | null;
    exec?: AgentSettingsState["exec"] | null;
  }) => void;
  /** Model settings per conversation (keyed like the composer settings:
   *  session id or draft key). Split panes each read their own; the global
   *  `agentSettings` above stays the focused conversation's. */
  agentSettingsBySession: Record<string, AgentSettingsState>;
  setSessionAgentSettings: (key: string, s: AgentSettingsState) => void;
  /** Moves when some conversation's model settings changed and the news
   *  does not say which (``agent_settings_changed``). Every shown pane
   *  reads its own entry again and keeps showing it until the read lands. */
  agentSettingsEpoch: number;
  markSessionAgentSettingsStale: () => void;
  /** The Agent a conversation's first turn bound, known from its ACK until
   *  the conversation list carries `agent_id`. */
  boundAgentBySession: Record<string, string>;
  setBoundAgent: (sessionId: string, agentId: string) => void;
  /** Branch chip display state for the current conversation. */
  branchInfo: BranchBadgeInfo;
  setBranchInfo: (b: BranchBadgeInfo) => void;
  /** Worker connection / run state shown on the environment chip. */
  statusBadge: StatusBadgeInfo;
  setStatusBadge: (b: StatusBadgeInfo) => void;
  /** Health of the focused conversation's bound channel account, from
   *  the channel status poll. Kept apart from the worker connection so
   *  a channel outage never paints the environment chip. */
  channelHealth: "ok" | "warn" | "err" | "unknown";
  setChannelHealth: (h: "ok" | "warn" | "err" | "unknown") => void;
  /** Summary for sidebar Recents list. */
  conversations: Record<string, ConvSummary>;
  /** The row storage: each view's rows by id. A view is a conversation, or
   *  one sub-agent's transcript under its own key, so the same id can sit in
   *  two views as two rows. Read it through `selectRow`. */
  rowsByView: Record<string, Record<string, ChatMsg>>;
  /** Fields each view shows ahead of the server, by row. `selectRow` merges
   *  them; the row itself keeps what the server said. */
  overlaysByView: Record<string, Readonly<Record<string, readonly RowOverlay[]>>>;
  /** Text frames that came ahead of the block before them, per view. */
  heldFramesByView: Record<string, Readonly<Record<string, readonly TextFrame[]>>>;
  /** Canonical execution sequence and terminal fence, released only when
   * the corresponding transcript state is removed. */
  executionUpdateOrders: Record<string, ExecutionUpdateOrder>;
  /** Ordered id list per conversation. */
  messageOrder: Record<string, string[]>;
  /** Currently active conversation id. */
  currentSessionId: string | null;
  /** The conversation that was current last; survives the New tab page
   *  clearing currentSessionId, so a window opened there has an owner. */
  lastSessionId: string | null;
  /** Active chat surface key. Real sessions use their id; unsent tabs use
   *  a provisional local_* id while currentSessionId stays null. */
  activeChatKey: string | null;
  /** Project selected for an unsent chat, keyed by its provisional
   *  activeChatKey. The entry is consumed once chat_ack confirms that
   *  the provisional session exists on the backend. */
  pendingProjectsByChat: Record<string, string>;
  /** Per-session running task map. Drives the composer send/stop
   *  button (via each session's scope store) and the sidebar breathing
   *  indicator (all sessions). */
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
    /** 最后一次 API 调用的 prompt 体积（input+cache_read）≈ 当前上下文占用。 */
    context?: number;
    /** 服务端算好的"此刻占用" —— 圆环和 /context 面板同读这一个数，
     *  两处永远一致。真实请求刚完成时 = 实测，图变了（压缩/切模型/
     *  切分支）时 = 按当前图重估。 */
    total_used?: number;
    /** "measured" | "estimated"，标明 total_used 的来源。 */
    basis?: string | null;
    model?: string | null;
    provider?: string | null;
  }>;
  /** Per-conversation context window size (model-dependent). */
  contextWindow: Record<string, number>;
  /** Per-conversation active DAG head (selected branch tip).切分支时更新，
   *  让 /context 等按分支取上下文的读取方能订阅式感知当前分支。 */
  heads: Record<string, string | null>;
  setHead: (sessionId: string, headId: string | null) => void;
  setContextStats: (
    sessionId: string,
    chat: {
      input?: number;
      output?: number;
      cache_read?: number;
      cache_create?: number;
      context?: number;
      total_used?: number;
      basis?: string | null;
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
  setCurrentDraft: (key: string) => void;
  dropChatDraft: (key: string) => void;
  setPendingProject: (chatKey: string, projectId: string) => void;
  takePendingProject: (chatKey: string) => string | null;
  /** Additional working directories per session (server-persisted session
   *  data, NOT a composer preference — never mirrored to localStorage).
   *  Keyed by session id; drafts use their provisional local_* key, which
   *  the backend adopts as the real session id on first send. Written from
   *  three sources: `session_loaded.data.settings`, the `working_dirs`
   *  broadcast frame, and optimistic UI updates from the project menu. */
  additionalWorkingDirsBySession: Record<string, string[]>;
  setAdditionalWorkingDirs: (sessionKey: string, dirs: string[]) => void;
  /** Apply a read to a view: local rows survive it, and one a read answers by
   *  request id goes. `whole` says it replaced the window. Returns the request
   *  ids the read's rows carry. */
  applyRead: (view: string, rows: ChatMsg[], whole?: boolean) => string[];
  /** Apply one typed change only this browser knows about. */
  applyLocalChange: (view: string, change: LocalChange) => void;
  /** Store what the transcript reducer returned for a view. */
  writeView: (view: string, next: ViewRows) => void;
  acceptExecutionUpdate: (
    executionId: string,
    eventSequence: unknown,
    status: unknown,
    sessionId?: unknown,
    messageIds?: Iterable<unknown>,
  ) => boolean;
  /** Set / clear the running task for a specific session. Pass null
   *  to clear. `always` drains the send queue even if the session
   *  already looked idle (stop at 0ms, or a server clear after stop).
   *  `never` is for updates that must not drain. */
  setRunningTaskFor: (
    sessionId: string,
    t: RunningTask | null,
    drain?: "transition" | "always" | "never",
  ) => void;
  setPaused: (p: boolean) => void;
  setProviderInfo: (p: ConvState["providerInfo"]) => void;

  /** Welcome screen visibility — true when chat-area should show the
   *  logo / title / example buttons. Owned by React; legacy
   *  setWelcomeVisible() in helpers.js writes through here. */
  welcomeVisible: boolean;
  /** The welcome screen hid because this chat sent its first message, so
   *  its code rain stops and fades; any other hide (another chat opening)
   *  removes it at once. */
  welcomeFade: boolean;
  setWelcomeVisible: (v: boolean, fade?: boolean) => void;

  /** Session id whose transcript is still in flight (load_session sent,
   *  no full capture cached). MessageList shows a skeleton for it
   *  instead of flashing the welcome screen. Cleared by
   *  ``loadSessionData`` when the capture lands. */
  transcriptReadStatus: Record<string, 'loading' | 'ready' | 'error' | 'disconnected'>;
  setTranscriptReadStatus: (id: string, status: 'loading' | 'ready' | 'error' | 'disconnected') => void;
  transcriptLoadingId: string | null;
  setTranscriptLoading: (id: string | null) => void;

  /** Per-session draft cache, keyed by sessionId (or "__new__" for the
   *  not-yet-created next chat). Persisted to localStorage so unsent text
   *  survives refresh and session switching. Components read their own
   *  session's draft through `useSessionScope`; this map is the durable
   *  backing store those scope instances seed from and write through to. */
  composerDrafts: Record<string, string>;
  /** Write the focused chat's draft. Outside callers with no scope (legacy
   *  bridges, slash helpers) use it; scoped components go through
   *  `useSessionScope`. */
  setComposerInput: (s: string) => void;
  /** Update a captured chat owner's draft without changing whichever
   *  chat is visible when an async operation completes. */
  setComposerInputFor: (chatKey: string | null, s: string) => void;
  /** Per-session composer settings (tool toggles + thinking effort).
   *  Like composerDrafts: keyed by sessionId (or "__new__"), persisted to
   *  localStorage so they survive refresh AND stay isolated per session. */
  composerSettingsBySession: Record<string, ComposerSettings>;
  /** Patch a session's composer settings (cache + persist). Omit `chatKey`
   *  to target the focused session. Scoped components pass their own key
   *  via `useSessionScope().patchSettings`. */
  setComposerSettings: (
    patch: Partial<ComposerSettings>,
    chatKey?: string | null,
  ) => void;
  /** Bump to ask the Composer to call .focus() on its textarea. The
   *  Composer reacts to changes in this counter via useEffect. */
  composerFocusTick: number;
  focusComposer: () => void;

  /** When non-null, the Composer swaps its textarea for a parameter form.
   *  Form submit and exact typed calls share the direct function POST. */
  fnFormFunction: AgenticFunction | null;
  openFnForm: (
    fn: AgenticFunction,
    prefill?: Record<string, string> | null,
    submitAction?: ConvState["fnFormSubmitAction"],
  ) => void;
  /** 手动运行的"修改"入口：预填上次的参数，提交时以 fork_of_node
   *  为锚点作为兄弟分支重跑（旧运行保留在 ◀ N/M ▶ 里）。 */
  openFnFormEdit: (
    fn: AgenticFunction,
    prefill: Record<string, string>,
    forkOfNode: string,
  ) => void;
  fnFormPrefill: Record<string, string> | null;
  fnFormForkOf: string | null;
  /** Optional local submission, e.g. preparing a draft through the same parameter form. */
  fnFormSubmitAction: {
    label: string;
    submit: (kwargs: Record<string, unknown>) => { error: string; errorParam?: string } | void;
  } | null;
  closeFnForm: () => void;
  /** True between the close click and the wrapper-height transition
   *  end — `fnFormFunction` stays non-null through the close animation
   *  (the form must stay mounted to animate), so other components that
   *  react to the form opening/closing (e.g. the welcome screen's
   *  examples row) read this to start their own transition in sync
   *  with the form shrinking, not a beat later when it unmounts. */
  fnFormClosing: boolean;
  setFnFormClosing: (v: boolean) => void;

  /** Pending "system needs a decision" requests — runtime.ask / confirm /
   *  (later) tool approval. A FIFO queue: the head occupies the composer as
   *  a question/approval mode; answering it pops the head and the next one
   *  surfaces. Each item is the question.asked envelope's `data`. Driven by
   *  use-ws (enqueue on question.asked, dequeue on question.replied/rejected).
   *  Design: docs/reference/design/ui/composer/composer-interaction-modes.html. */
  pendingDecisions: PendingDecision[];
  enqueueDecision: (d: PendingDecision) => void;
  /** Remove a resolved/closed decision by id (answered elsewhere / stop). */
  dequeueDecision: (id: string) => void;

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

  /** Node currently shown in the right-rail detail panel. */
  detailNode: DetailNode | null;
  /** Populate the Details view with ``node``. Switches the right rail
   *  to Details unless ``keepView`` — the DAG passes it so clicking a
   *  node in the History view loads the details without yanking the
   *  user off the graph they are navigating. */
  showDetail: (node: DetailNode, keepView?: boolean) => void;
  /** Fill Details WITHOUT opening the dock — the DAG page's node click
   *  uses this; its inspector popover is the visible response there. */
  populateDetail: (node: DetailNode) => void;
  closeDetail: () => void;
  /** "A DAG node is selected" — true for BOTH selection paths: React
   *  callers via showDetail, and the legacy runtime-bridge showDetail
   *  that paints #detailBody itself. Gates the Detail/Context switch. */
  nodeSelected: boolean;
  setNodeSelected: (selected: boolean) => void;
  /** Expanded nested-LLM blocks, keyed by node_id + block/attempt id. */
  /** The reader's open or closed choice for each step, per view. */
  stepExpansion: Record<string, Record<string, boolean>>;
  setStepExpansion: (view: string, key: string, open: boolean) => void;
  llmContentOpen: Record<string, boolean>;
  setLlmContentOpen: (key: string, open: boolean) => void;
}

export interface DetailNode {
  path: string;
  name: string;
  status: string;
  /** Conversation that owns this call; never replaced by the focused session. */
  session_id?: string;
  params?: Record<string, unknown>;
  output?: string;
  error?: string;
  duration_ms?: number;
  prompt?: string;
  node_type?: string;
  raw_reply?: string;
  /** Visible reasoning_summary only — never opaque signatures. */
  thinking?: string;
  render?: string;
  compress?: boolean;
  attempts?: unknown[];
  /** Shared execution tree for resolving nested LLM tool_ref rows. */
  tree_root?: unknown;
}

const RIGHT_LS_OPEN = "rightSidebarOpen";
const RIGHT_LS_VIEW = "rightSidebarView";
// "bookmarks" moved to a center tab, "history" (the session DAG) moved
// to a center perspective, and "worktrees" was removed outright, so
// none of them is a sidebar view any more. A stale persisted value
// falls back to the default view.
const VALID_VIEWS = new Set(["context", "detail", "files", "running", "resources"]);

function readRightDock(): { open: boolean; view: string } {
  if (typeof window === "undefined") return { open: false, view: "files" };
  let open = false;
  try {
    const o = localStorage.getItem(RIGHT_LS_OPEN);
    if (o === "1") open = true;
    else if (o === "0") open = false;
  } catch {
    /* ignore */
  }
  // Persist the chosen view across reload. "detail" needs a node
  // selection that doesn't survive a reload, but restoring it is
  // harmless (empty-state panel). Anything we don't recognise (legacy
  // value, future tab) collapses back to files — the default view.
  let view = "files";
  try {
    const v = localStorage.getItem(RIGHT_LS_VIEW);
    if (v === "pages") view = "resources";
    else if (v && VALID_VIEWS.has(v)) view = v;
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

const LLM_CONTENT_OPEN_LS = "nestedLlmContentOpen";

function readLlmContentOpen(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(LLM_CONTENT_OPEN_LS);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const result: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "boolean") result[key] = value;
    }
    return result;
  } catch {
    return {};
  }
}

function persistLlmContentOpen(state: Record<string, boolean>) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LLM_CONTENT_OPEN_LS, JSON.stringify(state));
  } catch {
    /* ignore unavailable storage */
  }
}

// Composer drafts — persist per-session unsent input across refresh and
// session switch. Keyed by sessionId; the "new" pseudo-key holds the draft
// for the not-yet-created next session (before the user has any chats).
// The keyed maps share one per-window JSON value with pending project/channel.
const COMPOSER_NEW_KEY = "__new__";

function persistComposerDrafts(drafts: Record<string, string>) {
  updateSessionDraftState((state) => ({ ...state, composerDrafts: drafts }));
}

// Per-session composer settings (tool toggles + thinking effort) — same
// persistence shape as composerDrafts: one versioned blob in localStorage,
// keyed by sessionId (or "__new__"). These used to be GLOBAL localStorage
// keys shared by every session; now each session keeps its own.
const DEFAULT_COMPOSER_SETTINGS: ComposerSettings = {
  thinking: "",
  webSearch: true,
  fast: false,
  unattended: false,  // web default: attended (a human is watching, may be asked)
  permission_mode: "",  // "" → send inherit; backend uses session/project/auto
  effective_permission: "",
  sandbox: false,  // default off; user can still enable in the Plus menu
};
const initialSessionDraftState = readSessionDraftState();

function persistComposerSettingsMap(map: Record<string, ComposerSettings>) {
  updateSessionDraftState((state) => ({
    ...state,
    composerSettingsBySession: map,
  }));
}

function switchChat(
  state: ConvState,
  nextKey: string,
  currentSessionId: string | null,
): Partial<ConvState> {
  // Drafts and settings already live in the keyed maps — each session's
  // scope store writes through on every keystroke — so switching focus no
  // longer has to save the outgoing session or load the incoming one. All
  // that is left is promoting the "__new__" placeholder to a real key when
  // an unsent chat gets an id.
  const oldKey = state.activeChatKey ?? state.currentSessionId ?? COMPOSER_NEW_KEY;
  const promoting = oldKey === COMPOSER_NEW_KEY && nextKey !== COMPOSER_NEW_KEY;

  const drafts = { ...state.composerDrafts };
  if (promoting) {
    if (!(nextKey in drafts) && drafts[COMPOSER_NEW_KEY] !== undefined) {
      drafts[nextKey] = drafts[COMPOSER_NEW_KEY];
    }
    delete drafts[COMPOSER_NEW_KEY];
  }
  persistComposerDrafts(drafts);

  const settingsMap = { ...state.composerSettingsBySession };
  if (promoting) {
    if (!(nextKey in settingsMap) && settingsMap[COMPOSER_NEW_KEY]) {
      settingsMap[nextKey] = settingsMap[COMPOSER_NEW_KEY];
    }
    delete settingsMap[COMPOSER_NEW_KEY];
  }
  persistComposerSettingsMap(settingsMap);

  return {
    currentSessionId,
    lastSessionId: currentSessionId ?? state.lastSessionId,
    activeChatKey: nextKey === COMPOSER_NEW_KEY ? null : nextKey,
    composerDrafts: drafts,
    composerSettingsBySession: settingsMap,
    fnFormFunction: null,
    fnFormClosing: false,
    fnFormPrefill: null,
    fnFormForkOf: null,
    fnFormSubmitAction: null,
    welcomeVisible: currentSessionId === null,
    welcomeFade: false,
  };
}

const NO_ROWS: Readonly<Record<string, ChatMsg>> = Object.freeze({});
type RowsByView = ConvState["rowsByView"];

/** A view's rows by id, for readers that walk the whole view. */
export function selectViewRows(viewKey: string | null | undefined) {
  return (state: RowState): Readonly<Record<string, ChatMsg>> => {
    if (!viewKey) return NO_ROWS;
    const rows = state.rowsByView[viewKey];
    const overlays = state.overlaysByView?.[viewKey];
    return rows && overlays ? drawnViewRows(rows, overlays) : rows ?? NO_ROWS;
  };
}

/** A view's rows as the server last said, under any overlay. */
export function selectServerRows(viewKey: string | null | undefined) {
  return (state: Pick<ConvState, "rowsByView">): Readonly<Record<string, ChatMsg>> =>
    (viewKey ? state.rowsByView[viewKey] : undefined) ?? NO_ROWS;
}

/** One row as one view holds it. */
export function selectRow(viewKey: string | null | undefined, id: string | null | undefined) {
  return (state: RowState): ChatMsg | undefined => {
    if (!viewKey || !id) return undefined;
    const row = state.rowsByView[viewKey]?.[id];
    const overlays = state.overlaysByView?.[viewKey]?.[id];
    return row && overlays ? drawnWith(row, overlays) : row;
  };
}

type RowState = Pick<ConvState, "rowsByView"> & Partial<Pick<ConvState, "overlaysByView">>;
type ViewOverlays = Readonly<Record<string, readonly RowOverlay[]>>;

// The same row under the same overlays draws as one object, so subscribers re-render only on a change.
const drawnRows = new WeakMap<ChatMsg, WeakMap<readonly RowOverlay[], ChatMsg>>();
const drawnViews = new WeakMap<object, WeakMap<ViewOverlays, Readonly<Record<string, ChatMsg>>>>();

function drawnWith(row: ChatMsg, overlays: readonly RowOverlay[]): ChatMsg {
  let byOverlays = drawnRows.get(row);
  if (!byOverlays) drawnRows.set(row, byOverlays = new WeakMap());
  let drawn = byOverlays.get(overlays);
  if (!drawn) byOverlays.set(overlays, drawn = withOverlays(row, overlays));
  return drawn;
}

function drawnViewRows(rows: Readonly<Record<string, ChatMsg>>, overlays: ViewOverlays): Readonly<Record<string, ChatMsg>> {
  let byOverlays = drawnViews.get(rows);
  if (!byOverlays) drawnViews.set(rows, byOverlays = new WeakMap());
  let drawn = byOverlays.get(overlays);
  if (!drawn) {
    const merged = { ...rows };
    for (const id in overlays) if (merged[id]) merged[id] = drawnWith(merged[id], overlays[id]);
    byOverlays.set(overlays, drawn = merged);
  }
  return drawn;
}

type OverlaysByView = ConvState["overlaysByView"];

/** The view's overlays after a change to its rows, and the rows they touched. */
function settledOverlays(
  overlaysByView: OverlaysByView, view: string, rows: Readonly<Record<string, ChatMsg>>, event?: OverlayEvent,
): { overlaysByView: OverlaysByView; touched: string[] } {
  const before = overlaysByView[view];
  if (!before) return { overlaysByView, touched: [] };
  const after = standingOverlays(before, rows, event);
  return { overlaysByView: withViewOverlays(overlaysByView, view, after), touched: Object.keys(before) };
}

function withViewOverlays(overlaysByView: OverlaysByView, view: string, overlays: ViewRows["overlays"]): OverlaysByView {
  if (overlays === overlaysByView[view]) return overlaysByView;
  const next = { ...overlaysByView };
  if (overlays && Object.keys(overlays).length) next[view] = overlays;
  else delete next[view];
  return next;
}

/** The view a writer's row is in: the one it named, or, because callers could
 *  pass anything until rows were kept per view, the only one holding the row.
 *  With several holders and none named nothing can be chosen safely. */
function writableView(rowsByView: RowsByView, named: string, id: string): string | null {
  if (rowsByView[named]?.[id]) return named;
  const holders: string[] = [];
  for (const view in rowsByView) {
    if (rowsByView[view][id]) holders.push(view);
  }
  if (holders.length === 1) return holders[0];
  if (holders.length > 1) {
    console.error(`[session-store] write to row ${id} named view ${named}, which does not hold it; held by ${holders.join(", ")}`);
  }
  return null;
}

/** A view replaced by these rows: overlays are checked against them. */
function replacedView(s: ConvState, sessionId: string, rows: ChatMsg[], event?: OverlayEvent): Partial<ConvState> {
  // A transcript write settles optimistic sends against what it holds.
  const msgs = settlePendingSends(rows, getPendingUserMessageId(sessionId));
  // This view's rows are replaced; every other view keeps its own. A row
  // with no time of its own keeps the one it was shown with.
  const held = s.rowsByView[sessionId] ?? NO_ROWS;
  const timedMessages = msgs.map((msg) => withMessageTimestamp(msg, held[msg.id]?.timestamp));
  const viewRows: Record<string, ChatMsg> = {};
  for (const m of timedMessages) viewRows[m.id] = withFrameOnlyFields(m, held[m.id]);
  const removedIds = (s.messageOrder[sessionId] ?? []).filter((oldId) => !viewRows[oldId]);
  const rowsByView = { ...s.rowsByView, [sessionId]: viewRows };
  const { overlaysByView } = settledOverlays(s.overlaysByView, sessionId, viewRows, event);
  return {
    rowsByView,
    overlaysByView,
    messageOrder: {
      ...s.messageOrder,
      [sessionId]: timedMessages.map((m) => m.id),
    },
    executionUpdateOrders: removeExecutionUpdateOrders(
      s.executionUpdateOrders,
      removedIds,
    ),
  };
}

/** What the reducer returned, stored; rows it did not change keep their objects. */
function writtenView(s: ConvState, view: string, next: ViewRows): Partial<ConvState> {
  const held = s.rowsByView[view] ?? NO_ROWS;
  const viewRows: Record<string, ChatMsg> = {};
  for (const id of next.order) {
    const row = next.rows[id];
    if (!row) continue;
    viewRows[id] = row === held[id] ? row : withMessageTimestamp(row, held[id]?.timestamp);
  }
  const removedIds = (s.messageOrder[view] ?? []).filter((id) => !viewRows[id]);
  const rowsByView = { ...s.rowsByView, [view]: viewRows };
  const overlaysByView = withViewOverlays(s.overlaysByView, view, next.overlays);
  const heldFramesByView = { ...s.heldFramesByView };
  if (next.heldFrames && Object.keys(next.heldFrames).length) heldFramesByView[view] = next.heldFrames;
  else delete heldFramesByView[view];
  return {
    rowsByView,
    overlaysByView,
    heldFramesByView,
    messageOrder: { ...s.messageOrder, [view]: next.order.filter((id) => viewRows[id]) },
    executionUpdateOrders: removeExecutionUpdateOrders(s.executionUpdateOrders, removedIds),
  };
}

/** Forget the step choices of a removed session and its sub-agent views. */
function withoutSessionViews<T>(views: Record<string, T>, session: string): Record<string, T> {
  return Object.fromEntries(Object.entries(views).filter(([view]) => view !== session && !ownsSubAgentChatKey(session, view)));
}

export const useSessionStore = createWithEqualityFn<ConvState>((set) => ({
  wsStatus: "connecting",
  agentSettings: {},
  setAgentSettings: (s) =>
    set((prev) => ({
      agentSettings: {
        // null = clear, undefined = keep previous (see ConvState docs).
        chat: s.chat === null ? undefined : (s.chat ?? prev.agentSettings.chat),
        exec: s.exec === null ? undefined : (s.exec ?? prev.agentSettings.exec),
      },
    })),
  agentSettingsBySession: {},
  setSessionAgentSettings: (key, settings) =>
    set((prev) => ({ agentSettingsBySession: { ...prev.agentSettingsBySession, [key]: settings } })),
  agentSettingsEpoch: 0,
  markSessionAgentSettingsStale: () => set((prev) => ({ agentSettingsEpoch: prev.agentSettingsEpoch + 1 })),
  boundAgentBySession: {},
  setBoundAgent: (sessionId, agentId) =>
    set((prev) => prev.boundAgentBySession[sessionId] === agentId ? {}
      : { boundAgentBySession: { ...prev.boundAgentBySession, [sessionId]: agentId } }),
  branchInfo: { visible: false, name: "main", count: 0 },
  setBranchInfo: (b) => set({ branchInfo: b }),
  statusBadge: {
    label: "connecting…",
    tone: "connecting",
    paused: false,
    title: "Connecting…",
  },
  // Derived again on every run frame; the same badge keeps its object, so
  // the environment chip redraws only when the badge changes.
  setStatusBadge: (b) => set((s) => {
    const held = s.statusBadge;
    return held.label === b.label && held.tone === b.tone && held.paused === b.paused && held.title === b.title
      ? s : { statusBadge: b };
  }),
  channelHealth: "unknown",
  setChannelHealth: (h) => set({ channelHealth: h }),
  conversations: {},
  rowsByView: {},
  overlaysByView: {},
  heldFramesByView: {},
  executionUpdateOrders: {},
  messageOrder: {},
  currentSessionId: null,
  lastSessionId: null,
  activeChatKey: null,
  pendingProjectsByChat: initialSessionDraftState.pendingProjectsByChat,
  runningTasks: {},
  paused: false,
  providerInfo: null,
  trees: {},
  setTree: (sessionId, tree) =>
    set((s) => ({ trees: { ...s.trees, [sessionId]: tree } })),

  tokens: {},
  contextWindow: {},
  heads: {},
  setHead: (sessionId, headId) =>
    set((s) =>
      s.heads[sessionId] === headId
        ? s
        : { heads: { ...s.heads, [sessionId]: headId } },
    ),
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
            context: chat.context,
            total_used: chat.total_used,
            basis: chat.basis,
            model: chat.model,
            provider: chat.provider,
          },
        };
      }
      if (typeof ctxWindow === "number" && ctxWindow > 0) {
        next.contextWindow = { ...s.contextWindow, [sessionId]: ctxWindow };
      } else if (ctxWindow === 0 && sessionId in s.contextWindow) {
        // The server resolved no window for the model: drop the old one
        // instead of showing it against a different model.
        const { [sessionId]: _dropped, ...rest } = s.contextWindow;
        next.contextWindow = rest;
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
      forgetTakeLatestSession(id);
      const rest = { ...s.conversations };
      delete rest[id];
      const order = { ...s.messageOrder };
      // The conversation's sub-agent transcripts go with it.
      const doomed = Object.keys(order)
        .filter((key) => key === id || ownsSubAgentChatKey(id, key))
        .flatMap((key) => {
          const ids = order[key] ?? [];
          if (key !== id) delete order[key];
          return ids;
        });
      const doomedExecutionIds = [
        ...doomed,
        ...Object.entries(s.executionUpdateOrders)
          .filter(([, execution]) => execution.sessionId === id)
          .map(([executionId]) => executionId),
      ];
      delete order[id];
      const rowsByView = withoutSessionViews(s.rowsByView, id);
      // Prune the deleted session's composer draft so the draft blob
      // doesn't grow unboundedly over the lifetime of the tab. Paste
      // entries referenced only by this draft are GC'd by the composer
      // (it watches ``composerDrafts`` and retains only still-referenced
      // ids in pasteStore).
      const nextDrafts = { ...s.composerDrafts };
      delete nextDrafts[id];
      dropSessionStore(id);
      const nextPendingProjects = { ...s.pendingProjectsByChat };
      delete nextPendingProjects[id];
      persistComposerDrafts(nextDrafts);
      updateSessionDraftState((state) => ({
        ...state,
        pendingProjectsByChat: nextPendingProjects,
      }));
      return {
        conversations: rest,
        messageOrder: order,
        rowsByView,
        overlaysByView: withoutSessionViews(s.overlaysByView, id),
        heldFramesByView: withoutSessionViews(s.heldFramesByView, id),
        stepExpansion: withoutSessionViews(s.stepExpansion, id),
        executionUpdateOrders: removeExecutionUpdateOrders(
          s.executionUpdateOrders,
          doomedExecutionIds,
        ),
        currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
        activeChatKey: s.activeChatKey === id ? null : s.activeChatKey,
        composerDrafts: nextDrafts,
        pendingProjectsByChat: nextPendingProjects,
      };
    }),

  clearConversations: () =>
    set((s) => {
      for (const id of Object.keys(s.messageOrder)) forgetTakeLatestSession(id);
      // Clear persisted conversations without deleting independent unsent tabs.
      const nextDrafts = Object.fromEntries(
        Object.entries(s.composerDrafts).filter(([key]) =>
          key === COMPOSER_NEW_KEY || key.startsWith("local_"),
        ),
      );
      const nextPendingProjects = Object.fromEntries(
        Object.entries(s.pendingProjectsByChat).filter(([key]) =>
          key.startsWith("local_"),
        ),
      );
      persistComposerDrafts(nextDrafts);
      updateSessionDraftState((state) => ({
        ...state,
        pendingProjectsByChat: nextPendingProjects,
      }));
      return {
        conversations: {},
        rowsByView: {},
        overlaysByView: {},
        heldFramesByView: {},
              executionUpdateOrders: {},
        messageOrder: {},
        currentSessionId: null,
        activeChatKey:
          s.activeChatKey?.startsWith("local_") ? s.activeChatKey : null,
        composerDrafts: nextDrafts,
        pendingProjectsByChat: nextPendingProjects,
      };
    }),

  setCurrentConv: (id) =>
    set((s) => switchChat(s, id ?? COMPOSER_NEW_KEY, id)),

  setCurrentDraft: (key) =>
    set((s) => s.activeChatKey === key && s.currentSessionId === null
      ? {}
      : switchChat(s, key, null)),

  dropChatDraft: (key) =>
    set((s) => {
      forgetTakeLatestSession(key);
      const drafts = { ...s.composerDrafts };
      const settings = { ...s.composerSettingsBySession };
      const pendingProjects = { ...s.pendingProjectsByChat };
      delete drafts[key];
      delete settings[key];
      delete pendingProjects[key];
      dropSessionStore(key);
      persistComposerDrafts(drafts);
      persistComposerSettingsMap(settings);
      updateSessionDraftState((state) => ({
        ...state,
        pendingProjectsByChat: pendingProjects,
      }));
      return {
        composerDrafts: drafts,
        stepExpansion: withoutSessionViews(s.stepExpansion, key),
        composerSettingsBySession: settings,
        pendingProjectsByChat: pendingProjects,
        ...(s.activeChatKey === key
          ? { activeChatKey: null, currentSessionId: null }
          : {}),
      };
    }),

  setPendingProject: (chatKey, projectId) =>
    set((s) => {
      const pendingProjectsByChat = {
        ...s.pendingProjectsByChat,
        [chatKey]: projectId,
      };
      updateSessionDraftState((state) => ({
        ...state,
        pendingProjectsByChat,
      }));
      return { pendingProjectsByChat };
    }),

  additionalWorkingDirsBySession: {},
  setAdditionalWorkingDirs: (sessionKey, dirs) =>
    set((s) => ({
      additionalWorkingDirsBySession: {
        ...s.additionalWorkingDirsBySession,
        [sessionKey]: dirs,
      },
    })),

  takePendingProject: (chatKey) => {
    let projectId: string | null = null;
    set((s) => {
      projectId = s.pendingProjectsByChat[chatKey] ?? null;
      if (!projectId) return {};
      const pendingProjects = { ...s.pendingProjectsByChat };
      delete pendingProjects[chatKey];
      updateSessionDraftState((state) => ({
        ...state,
        pendingProjectsByChat: pendingProjects,
      }));
      return { pendingProjectsByChat: pendingProjects };
    });
    return projectId;
  },

  applyRead: (view, rows, whole = true) => {
    let steers: string[] = [];
    set((s) => {
      const read = applyReadRows({
        order: s.messageOrder[view] ?? [],
        rows: s.rowsByView[view] ?? NO_ROWS,
      }, rows);
      steers = read.steers;
      return replacedView(s, view, read.rows, whole ? { read: true } : undefined);
    });
    return steers;
  },

  applyLocalChange: (view, change) =>
    set((s) => {
      const named = "rowId" in change ? writableView(s.rowsByView, view, change.rowId) ?? view : view;
      // A change naming a row or view this store does not hold creates nothing.
      if ("rowId" in change ? !s.rowsByView[named]?.[change.rowId] : change.kind !== "local_append" && !s.rowsByView[named]) return {};
      if (change.kind === "local_append") noteMessageArrival(change.row.id);
      if (change.kind === "local_replace") rekeyMessageArrival(change.rowId, change.row.id);
      return writtenView(s, named, applyLocalChangeToView({
        order: s.messageOrder[named] ?? [],
        rows: s.rowsByView[named] ?? NO_ROWS,
        overlays: s.overlaysByView[named],
        heldFrames: s.heldFramesByView[named],
      }, change));
    }),

  writeView: (view, next) => set((s) => writtenView(s, view, next)),

  acceptExecutionUpdate: (executionId, eventSequence, status, sessionId, messageIds) => {
    let accepted = false;
    set((s) => {
      const decision = decideExecutionUpdateOrder(
        s.executionUpdateOrders[executionId],
        eventSequence,
        status,
        sessionId,
        messageIds,
      );
      if (!decision.accepted) return {};
      accepted = true;
      if (!decision.next) return {};
      return {
        executionUpdateOrders: {
          ...s.executionUpdateOrders,
          [executionId]: decision.next,
        },
      };
    });
    return accepted;
  },


  setRunningTaskFor: (sessionId, t, drain = "transition") =>
    set((s) => {
      // An execution this client already saw end can't run again. The
      // server releases the session a moment after the terminal frame, so
      // a session load or running_task in that window still names the
      // finished execution; restoring it would flash the row back to
      // running until running_task_clear arrives.
      if (t?.execution_id && s.executionUpdateOrders[t.execution_id]?.terminal) t = null;
      const wasRunning = Boolean(s.runningTasks[sessionId]);
      const next = { ...s.runningTasks };
      if (t) next[sessionId] = t;
      else delete next[sessionId];
      // Keep the session's scope store (what its composer actually renders
      // from) in step — this setter is the entry point for WS frames and
      // legacy bridges, which have no React context to write through.
      pushToSessionStore(sessionId, { running: t });
      // Turn finished → hand the client-side send queue its chance to
      // ship the next parked message. Stop and the server clear both
      // use `always` so a queued message goes out once, at 0ms, and
      // the later clear does not send it again. Deferred a tick so
      // drain writes cannot re-enter this `set`.
      if (!t && drain !== "never" && (wasRunning || drain === "always")) {
        queueMicrotask(() => {
          void import("@/lib/chat/send/send-queue").then((m) =>
            m.useSendQueue.getState().drain(sessionId),
          );
        });
      }
      if (t && !t.msg_id && !t.execution_id) {
        void import("@/lib/chat/send/send-queue").then((m) => m.recheckRunState(sessionId));
      }
      return { runningTasks: next };
    }),
  setPaused: (p) => set({ paused: p }),
  setProviderInfo: (p) => set({ providerInfo: p }),

  // Default to visible — first page load lands on /chat with no
  // session, the welcome panel should greet the user. sendChatMessage
  // flips it false once a turn goes out; setCurrentConv(null) flips
  // it back true on New chat.
  welcomeVisible: true,
  welcomeFade: false,
  setWelcomeVisible: (v, fade = false) => set({ welcomeVisible: v, welcomeFade: !v && fade }),

  transcriptReadStatus: {},
  setTranscriptReadStatus: (id, status) => set(s => ({ transcriptReadStatus: { ...s.transcriptReadStatus, [id]: status }, ...(status !== 'loading' && s.transcriptLoadingId === id ? { transcriptLoadingId: null } : {}) })),
  transcriptLoadingId: null,
  setTranscriptLoading: (id) => set({ transcriptLoadingId: id }),

  composerDrafts: initialSessionDraftState.composerDrafts,
  setComposerInput: (s) =>
    set((state) => {
      const sid = state.activeChatKey ?? state.currentSessionId ?? COMPOSER_NEW_KEY;
      const drafts = { ...state.composerDrafts, [sid]: s };
      // Persist on every keystroke. Cheap (one JSON.stringify per
      // session-count) and matches the "right dock" pattern above.
      persistComposerDrafts(drafts);
      pushToSessionStore(sid, { draft: s });
      return { composerDrafts: drafts };
    }),
  setComposerInputFor: (chatKey, s) =>
    set((state) => {
      const sid = chatKey ?? COMPOSER_NEW_KEY;
      const drafts = { ...state.composerDrafts, [sid]: s };
      persistComposerDrafts(drafts);
      pushToSessionStore(sid, { draft: s });
      return { composerDrafts: drafts };
    }),
  composerSettingsBySession: initialSessionDraftState.composerSettingsBySession,
  setComposerSettings: (patch, chatKey) =>
    set((state) => {
      const visibleKey =
        state.activeChatKey ?? state.currentSessionId ?? COMPOSER_NEW_KEY;
      const sid = chatKey === undefined ? visibleKey : (chatKey ?? COMPOSER_NEW_KEY);
      // Patch onto THAT session's own settings — no live slice to inherit
      // from, so a background pane can never pick up the focused chat's
      // values.
      const base =
        state.composerSettingsBySession[sid] ?? DEFAULT_COMPOSER_SETTINGS;
      const next = { ...base, ...patch };
      const map = { ...state.composerSettingsBySession, [sid]: next };
      persistComposerSettingsMap(map);
      pushToSessionStore(sid, { settings: next });
      return { composerSettingsBySession: map };
    }),
  composerFocusTick: 0,
  focusComposer: () =>
    set((state) => ({ composerFocusTick: state.composerFocusTick + 1 })),

  fnFormFunction: null,
  fnFormPrefill: null,
  fnFormForkOf: null,
  fnFormSubmitAction: null,
  openFnForm: (fn, prefill = null, submitAction = null) => set({
    fnFormFunction: fn, fnFormClosing: false,
    fnFormPrefill: prefill, fnFormForkOf: null, fnFormSubmitAction: submitAction,
  }),
  openFnFormEdit: (fn, prefill, forkOfNode) => set({
    fnFormFunction: fn, fnFormClosing: false,
    fnFormPrefill: prefill, fnFormForkOf: forkOfNode, fnFormSubmitAction: null,
  }),
  closeFnForm: () => set({
    fnFormFunction: null, fnFormClosing: false,
    fnFormPrefill: null, fnFormForkOf: null, fnFormSubmitAction: null,
  }),
  fnFormClosing: false,
  setFnFormClosing: (v) => set({ fnFormClosing: v }),

  pendingDecisions: [],
  enqueueDecision: (d) =>
    set((state) => {
      const previous = state.pendingDecisions.find((p) => p.id === d.id);
      const task = state.runningTasks[d.sessionId];
      if (!d.anchorId && !previous?.anchorId && task?.reply_id && task.execution_id === d.executionId) {
        d = { ...d, anchorId: task.reply_id };
      }
      if (!previous) return { pendingDecisions: [...state.pendingDecisions, d] };
      if (previous.executionId !== d.executionId || previous.expectedVersion > d.expectedVersion
          || previous.waitGeneration > d.waitGeneration) return {};
      // Refresh canonical metadata without resetting the component's answer draft.
      const supplied = Object.fromEntries(Object.entries(d).filter(([, value]) => value !== undefined));
      return { pendingDecisions: state.pendingDecisions.map((p) => p.id === d.id ? { ...p, ...supplied } : p) };
    }),
  dequeueDecision: (id) =>
    set((state) => ({
      pendingDecisions: state.pendingDecisions.filter((p) => p.id !== id),
    })),

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

  detailNode: null,
  // Fill the Details view without touching the dock — the DAG page has
  // its own inspector popover, so a node click must not pop the sidebar;
  // whenever the user opens it, the selected node is already there.
  populateDetail: (node) =>
    set({ detailNode: node, nodeSelected: true }),
  showDetail: (node, keepView) =>
    set((s) => {
      const next = keepView
        ? { ...s.rightDock, open: true }
        : { ...s.rightDock, open: true, view: "detail" };
      persistRightDock(next);
      return { detailNode: node, nodeSelected: true, rightDock: next };
    }),
  closeDetail: () =>
    set({ detailNode: null, nodeSelected: false }),
  nodeSelected: false,
  setNodeSelected: (selected) => set({ nodeSelected: selected }),
  stepExpansion: {},
  setStepExpansion: (view, key, open) =>
    set((state) => (state.stepExpansion[view]?.[key] === open ? {} : {
      stepExpansion: { ...state.stepExpansion, [view]: { ...state.stepExpansion[view], [key]: open } },
    })),
  llmContentOpen: readLlmContentOpen(),
  setLlmContentOpen: (key, open) =>
    set((state) => {
      if (!key || state.llmContentOpen[key] === open) return {};
      const next = { ...state.llmContentOpen, [key]: open };
      persistLlmContentOpen(next);
      return { llmContentOpen: next };
    }),
}));

// A scope store's setters update its own instance first (so the pane repaints
// on the same tick), then land here for persistence and for the keyed maps
// that the sidebar, transfer journal and legacy bridges read.
installScopeWriteThrough({
  draft: (sid, value) => useSessionStore.getState().setComposerInputFor(sid, value),
  settings: (sid, patch) => useSessionStore.getState().setComposerSettings(patch, sid),
  running: (sid, task) => useSessionStore.getState().setRunningTaskFor(sid, task),
});

/** The draft-channel-choice host this store reads and writes. Exported so
 *  the check scripts, which import query-isolated copies of this module,
 *  can reach the same instance the store itself uses. */
export function draftChoiceHost(): DraftChannelChoiceHost {
  return draftChannelChoiceHost;
}

export function snapshotSessionTransfer(
  _chatKeys: string[],
): SessionTransferSnapshot {
  void _chatKeys;
  const state = useSessionStore.getState();
  const host = draftChoiceHost();
  return {
    activeChatKey: state.activeChatKey,
    currentSessionId: state.currentSessionId,
    composerDrafts: structuredClone(state.composerDrafts),
    composerSettingsBySession: structuredClone(state.composerSettingsBySession),
    pendingProjectsByChat: structuredClone(state.pendingProjectsByChat),
    draftChannelChoices: structuredClone(
      host.__pendingChannelChoices
        ?? readSessionDraftState().draftChannelChoices,
    ),
  };
}

export function applySessionTransfer(
  snapshot: SessionTransferSnapshot,
  options: { persist: boolean },
): boolean {
  const host = draftChoiceHost();
  host.__pendingChannelChoices = structuredClone(snapshot.draftChannelChoices);
  host._pendingChannelChoice = snapshot.activeChatKey
    ? (host.__pendingChannelChoices[snapshot.activeChatKey] ?? null)
    : null;
  const drafts = structuredClone(snapshot.composerDrafts);
  const settings = structuredClone(snapshot.composerSettingsBySession);
  useSessionStore.setState({
    activeChatKey: snapshot.activeChatKey,
    currentSessionId: snapshot.currentSessionId,
    composerDrafts: drafts,
    composerSettingsBySession: settings,
    pendingProjectsByChat: structuredClone(snapshot.pendingProjectsByChat),
  });
  // Any scope store already alive for a transferred chat is holding the
  // pre-transfer values; re-seed it from what just landed.
  for (const key of Object.keys({ ...drafts, ...settings })) {
    pushToSessionStore(key, {
      ...(drafts[key] !== undefined ? { draft: drafts[key] } : {}),
      ...(settings[key] ? { settings: settings[key] } : {}),
    });
  }
  return options.persist
    ? replaceSessionDraftState({
      version: 1,
      composerDrafts: snapshot.composerDrafts,
      composerSettingsBySession: snapshot.composerSettingsBySession,
      pendingProjectsByChat: snapshot.pendingProjectsByChat,
      draftChannelChoices: snapshot.draftChannelChoices,
    })
    : true;
}

export function persistCurrentSessionTransfer(chatKeys: string[]): boolean {
  const snapshot = snapshotSessionTransfer(chatKeys);
  return replaceSessionDraftState({
    version: 1,
    composerDrafts: snapshot.composerDrafts,
    composerSettingsBySession: snapshot.composerSettingsBySession,
    pendingProjectsByChat: snapshot.pendingProjectsByChat,
    draftChannelChoices: snapshot.draftChannelChoices,
  });
}


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

const EMPTY_IDS: string[] = [];

// The conversation the user was last in, kept while a web page or a file
// is in front, so an action taken there (a page comment) can still reach
// that conversation's composer.
let lastChatKey: string | null = null;
{
  const remember = (s: ConvState) => {
    const key = s.activeChatKey ?? s.currentSessionId;
    if (key) lastChatKey = key;
  };
  remember(useSessionStore.getState());
  useSessionStore.subscribe(remember);
}

// The browser controls show the Agent operating a page only while its
// conversation runs (browser-control.ts presentedControlState).
{
  let reported: ConvState["runningTasks"] | null = null;
  const report = (s: ConvState) => {
    if (s.runningTasks === reported) return;
    reported = s.runningTasks;
    setRunningConversations(Object.keys(s.runningTasks));
  };
  report(useSessionStore.getState());
  useSessionStore.subscribe(report);
}

export function getLastChatKey(): string | null {
  return lastChatKey;
}
