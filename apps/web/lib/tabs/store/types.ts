import type { FileSectionRequests } from "../panes/file-section-requests";
import type { CanvasDockSide, CanvasLayout, LayoutNode } from "../canvas/canvas-layout";
import type { CenterTabGroup } from "@/lib/tabs/center-tab-groups";
import type { BuiltinPage } from "@/lib/tabs/center-tab-ids";
import { type TabPageHistory } from "../navigation/page-history";
import { type SessionTabHistory } from "../navigation/session-history";
import type { ClosedTabEntry } from "./closed-tabs";
export type CenterTabKind = "session" | "file" | "web" | "ntp" | "builtin" | "application";

export interface CenterTab {
  /** Empty canvas layout tab; removed once real content occupies the layout. */
  canvasAnchor?: boolean;
  applicationId?: string;
  applicationInstanceId?: string;
  /** Application tabs: the chat whose window this is; its entries stay
   *  resident for that chat until the tab closes. */
  applicationSessionId?: string;
  id: string;
  kind: CenterTabKind;
  /** Session tabs: conversation title (may lag; synced from the
   *  session store). File tabs: basename. Web tabs: hostname until
   *  updateWebTab sets a real title. NTP: unused (i18n label). */
  title: string;
  /** Session tabs only. Drafts use a provisional local_* id. */
  sessionId?: string;
  /** Session tabs only — true until the first server acknowledgement. */
  draft?: boolean;
  /** Per-tab session navigation; identity and group references stay fixed. */
  sessionHistory?: SessionTabHistory;
  pageHistory?: TabPageHistory;
  navigationRoute?: string;
  fileNavigationSnapshot?: FileNavigationSnapshot;
  /** File tabs only. */
  projectId?: string;
  /** File tabs only — project-relative, "/"-separated. */
  path?: string;
  readOnly?: boolean;
  fileSessionId?: string;
  previewOwnerSessionId?: string;
  /** File tabs only — the turn this file was opened FROM. Both set
   *  ⇒ the pane can fetch that turn's diff and defaults to showing
   *  it instead of the raw file. */
  diffSessionId?: string;
  diffMsgId?: string;
  /** File tabs only — 1-based line to scroll to on open, briefly
   *  highlighted (with `highlightLines`, an inclusive [from, to]). */
  scrollToLine?: number;
  highlightLines?: [number, number];
  /** Web tabs only — current http(s) URL (may drift from the id
   *  after in-pane navigation). */
  url?: string;
  /** Epoch ms when `url` last came from a trusted native navigation event. */
  urlNativeAt?: number;
  /** Popup web tabs only — exact opener tab in this renderer window. */
  openerTabId?: string;
  /** Agent-created page attribution, independent of the active session/view. */
  agentOpened?: boolean;
  agentSessionId?: string;
  agentBranchId?: string;
  agentExecutionId?: string;
  /** Explicit top-strip visibility: false retains a session Page only in Resources. */
  webPinned?: boolean;
  /** Web tabs only — favicon URL reported by the desktop shell; the
   *  strip falls back to the Chrome icon when absent or unloadable. */
  faviconUrl?: string;
  /** Builtin tabs only — which built-in page this tab shows. */
  page?: BuiltinPage;
  /** Review tab only — current scope and source turn. */
  reviewSessionId?: string;
  reviewMsgId?: string;
  reviewScope?: "turn" | "branch" | "workspace";
  reviewPath?: string;
  /** Unsaved-changes marker — strip shows ● instead of ✕. Set via
   *  setTabDirty by whoever owns the tab's content (file editor). */
  dirty?: boolean;
  /** Session tabs only — which perspective the center shows: the
   *  transcript (falsy, the default) or the session context DAG.
   *  Per tab, so parking one session on the graph doesn't move the
   *  others. Not persisted: a reload starts on the transcript. */
  dagView?: boolean;
}

export interface FileNavigationSnapshot {
  projectId: string;
  path: string;
  selectedType: "file" | "dir";
  expanded: string[];
  scroll: { path: string; offset: number } | null;
}

export interface FileNavigationHistory {
  entries: FileNavigationSnapshot[];
  index: number;
}

export interface FileTabOptions {
  section?: string;
  diffSessionId?: string;
  diffMsgId?: string;
  scrollToLine?: number;
  highlightLines?: [number, number];
}

export interface CenterTabsState {
  fileSectionRequests: FileSectionRequests;
  fileSectionSequence: number;
  consumeFileSection: (id: string, sequence: number) => void;
  setCanvas: (targetId: string, canvas: CanvasLayout) => void;
  /** Live, unpersisted canvas tree while a divider is dragged. */
  previewCanvas: (targetId: string, root: LayoutNode) => void;
  dockCanvas: (sourceId: string | string[], targetId: string, paneId: string, side: CanvasDockSide) => void;
  navigationRoute?: string;
  recordRouteNavigation: (pathname: string) => void;
  canNavigateHistory: (direction: -1 | 1) => boolean;
  navigateHistory: (direction: -1 | 1) => void;
  tabs: CenterTab[];
  activeId: string | null;
  groups: CenterTabGroup[];
  splitWebTabId: string | null;
  splitRatio: number;
  /** File-tree projection of the active tab history. */
  fileNavigationHistory: FileNavigationHistory;
  fileNavigationRestore: FileNavigationSnapshot | null;
  updateFileNavigationView: (view: Pick<FileNavigationSnapshot, "expanded" | "scroll">) => void;
  setActive: (id: string) => void;
  moveTab: (id: string, beforeId: string | null) => void;
  moveGroup: (groupId: string, beforeId: string | null) => void;
  moveGroupMember: (groupId: string, memberId: string, toIndex: number) => void;
  groupTab: (
    sourceId: string,
    targetId: string,
    memberIndex: number,
    groupId?: string,
  ) => boolean;
  mergeGroup: (
    sourceGroupId: string,
    targetId: string,
    memberIndex: number,
  ) => boolean;
  ungroupTab: (id: string, beforeId?: string | null) => void;
  focusGroupMember: (groupId: string, memberId: string) => void;
  /** Navigate the active session tab, otherwise create a session tab.
   *  ``newTab`` keeps the active tab and opens the session beside it. */
  openSessionTab: (sessionId: string, title: string, options?: { newTab?: boolean }) => void;
  navigateSessionHistory: (direction: -1 | 1) => void;
  navigateFileHistory: (direction: -1 | 1) => void;
  canNavigateFile: (direction: -1 | 1) => boolean;
  recordFileNavigation: (snapshot: FileNavigationSnapshot) => void;
  removeSessionFromHistory: (sessionId: string) => void;
  /** Create a distinct draft. An active NTP is replaced in place;
   *  otherwise the draft is appended. Returns its provisional id. */
  openDraftSessionTab: () => string;
  /** NTP New session: replace only the active NTP with a distinct draft. */
  claimDraftSessionTab: () => string;
  /** First acknowledgement: keep the same tab/id and clear draft state. */
  markSessionReady: (sessionId: string) => void;
  /** A draft's first message left the client: until its acknowledgement the
   *  draft is no longer an empty New chat that can be reused. ``false``
   *  undoes it when the backend rejects that message. */
  markSessionSent: (sessionId: string, sent?: boolean) => void;
  openFileTab: (
    projectId: string,
    path: string,
    options?: FileTabOptions,
  ) => void;
  openFilePreview: (sessionId: string, target: { projectId: string; path: string; readOnly?: boolean; section?: string }, automatic?: boolean) => boolean;
  /** Open a local page (an HTML file's file:// URL) in a browser tab beside the chat. */
  openPagePreview: (sessionId: string, target: { url: string; title: string }, automatic?: boolean) => boolean;
  /** Focus-or-create a web tab for `url` (must already be a valid
   *  http(s) URL — run user input through normalizeWebUrl first). */
  openWebTab: (url: string, agentRequest?: boolean) => void;
  /** Always append a distinct web tab for a native page popup. */
  /** ``id`` is the desktop's, when it already made the popup's native Page. */
  /** `behind` keeps the tab the user is on: the popup came from an Agent's step. */
  openPopupWebTab: (url: string, openerTabId: string, id?: string, behind?: boolean) => string;
  /** Create or reuse a web tab without focusing it or opening a split. */
  markAgentWebTab: (id: string, sessionId?: string, attribution?: {
    branchId?: string; executionId?: string;
  }) => void;
  setWebTabPinned: (id: string, pinned: boolean) => void;
  ensureWebTab: (url: string) => string;
  /** Create a unique same-URL leaf without focusing it. */
  ensureExclusiveWebTab: (url: string) => string;
  /** Appends or reuses a split web tab; an existing owner composite becomes active. */
  openWebTabInSplit: (url: string) => string;
  setSplitWebTab: (id: string | null) => void;
  setSplitRatio: (ratio: number) => void;
  /** Update a web tab's url/title in place (address-bar navigation,
   *  later title reporting from the sidecar browser). Id stays fixed. */
  updateWebTab: (
    id: string,
    patch: { url?: string; title?: string; faviconUrl?: string; urlNativeAt?: number },
  ) => void;
  /** Replace a web tab in place with the built-in new-tab page. */
  replaceWebTabWithNewTabPage: (id: string) => void;
  /** Unsaved-changes marker groundwork — content owners call this;
   *  the strip renders ● instead of ✕ while dirty. */
  setTabDirty: (id: string, dirty: boolean) => void;
  /** Flip a session tab between the transcript and the context DAG. */
  setTabDagView: (id: string, dagView: boolean) => void;
  /** Retarget a file tab after its file was renamed/moved on disk:
   *  new file identity and title, preserving its own history and order.
   *  A separate tab displaying the target remains independent. */
  retargetFileTab: (oldId: string, newProjectId: string, newPath: string) => void;
  /** Focus-or-create the singleton tab for a built-in page. */
  openBuiltinTab: (page: BuiltinPage) => void;
  openApplicationTab: (appId: string, instanceId: string, title: string, sessionId?: string) => void;
  openReviewTab: (
    sessionId: string,
    assistantMsgId?: string,
    scope?: "turn" | "branch" | "workspace",
    path?: string,
  ) => void;
  /** Create a distinct new-tab page with its own navigation history. */
  openNewTabPage: () => void;
  /** Close a tab; closing the active one activates the right
   *  visible neighbor, else the left. The final tab leaves an empty view. */
  closeTab: (id: string, options?: { retainSessionResources?: boolean }) => void;
  /** The last closed tabs (newest last, at most 10) for Cmd+Shift+T. */
  closedTabs: ClosedTabEntry[];
  /** Reopen the most recently closed tab where it was. A session tab whose
   *  conversation `sessionGone` reports deleted is skipped, never recreated
   *  as an empty tab. Returns the reopened tab's id, or null. */
  reopenClosedTab: (sessionGone?: (sessionId: string) => boolean) => string | null;
  renameSessionTab: (sessionId: string, title: string) => void;
}
