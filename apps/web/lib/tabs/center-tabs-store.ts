import { pruneFileSections } from "./panes/file-section-requests";
import { canvasActions } from "./store/layout/canvas";
/** Public tab-store entry point. Actions are grouped by responsibility in store/. */
import { create } from "zustand";
import { readCenterTabsPayload } from "./center-tabs-persistence";
import { fileHistoryFor } from "./navigation/selectors";
import { sessionHistory } from "./navigation/session-history";
import { browserActions } from "./store/browser";
import { layoutActions } from "./store/layout/layout";
import { navigationActions } from "./store/navigation";
import { pagesActions } from "./store/pages";
import { sessionsActions } from "./store/sessions";
import { splitActions } from "./store/layout/split";
import { bindTransfers } from "./store/transfers";
import type { CenterTabsState } from "./store/types";
export { builtinTabId, fileTabId, normalizeWebUrl, reviewTabId, sessionTabId, webTabId } from "./center-tab-ids";
export type { BuiltinPage } from "./center-tab-ids";
export { DRAFT_SESSION_TAB_ID, rebaseCenterTabsPayload } from "./center-tabs-persistence";
export type { CenterTabsPersistedPayload } from "./center-tabs-persistence";
export type * from "./store/types";

const closedSessionAckTombstones = new Set<string>();
// Drafts whose first message is on the wire and not yet acknowledged.
const sentDraftSessions = new Set<string>();

/** A provisional chat key some tab still holds as an unacknowledged draft:
 *  the server has no conversation for it yet. */
export function isDraftSession(sessionId: string): boolean {
  return useCenterTabs.getState().tabs.some(tab => [tab, ...(tab.pageHistory?.entries ?? [])].some(page =>
    page.kind === "session" && sessionHistory(page).entries.some(entry => entry.sessionId === sessionId && entry.draft)));
}

/** True while a draft's first message waits for its acknowledgement. */
export function sessionWasSent(sessionId: string): boolean {
  return sentDraftSessions.has(sessionId);
}

export const useCenterTabs = create<CenterTabsState>((set, get) => {
  const initial = readCenterTabsPayload();
  return {
    fileSectionRequests: {},
    fileSectionSequence: 0,
    consumeFileSection: (id, sequence) => set(state => {
      if (state.fileSectionRequests[id]?.sequence !== sequence) return {};
      const requests = { ...state.fileSectionRequests };
      delete requests[id];
      return { fileSectionRequests: requests };
    }),
    navigationRoute: initial.tabs.find(tab => tab.id === initial.activeId)?.navigationRoute,
    tabs: initial.tabs,
    activeId: initial.activeId,
    groups: initial.groups,
    splitWebTabId: initial.splitWebTabId,
    splitRatio: initial.splitRatio,
    closedTabs: [],
    fileNavigationHistory: fileHistoryFor(initial.tabs.find(tab => tab.id === initial.activeId)),
    fileNavigationRestore: initial.tabs.find(tab => tab.id === initial.activeId)?.fileNavigationSnapshot ?? null,
    ...layoutActions(set),
    ...canvasActions(set),
    ...navigationActions(set, get, closedSessionAckTombstones),
    ...sessionsActions(set, get, closedSessionAckTombstones, sentDraftSessions),
    ...pagesActions(set, get, closedSessionAckTombstones),
    ...browserActions(set),
    ...splitActions(set)
  };
});

useCenterTabs.subscribe(state => {
  const requests = pruneFileSections(state.fileSectionRequests, state.tabs);
  if (requests !== state.fileSectionRequests) useCenterTabs.setState({ fileSectionRequests: requests });
});

export const { sessionAckIsActive, chatAckIsActive, snapshotCenterTabsPayload, validateTransferredTabs, insertTransferredTabs, removeTransferredTabs, replaceCenterTabsPayload, persistCurrentCenterTabsPayload } = bindTransfers(useCenterTabs, closedSessionAckTombstones, sentDraftSessions);
