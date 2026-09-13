/** Public tab-store entry point. Actions are grouped by responsibility in store/. */
import { create } from "zustand";
import { readCenterTabsPayload } from "./center-tabs-persistence";
import { fileHistoryFor } from "./navigation/selectors";
import { browserActions } from "./store/browser";
import { layoutActions } from "./store/layout";
import { navigationActions } from "./store/navigation";
import { pagesActions } from "./store/pages";
import { sessionsActions } from "./store/sessions";
import { splitActions } from "./store/split";
import { bindTransfers } from "./store/transfers";
import type { CenterTabsState } from "./store/types";
export { builtinTabId, fileTabId, normalizeWebUrl, reviewTabId, sessionTabId, webTabId } from "./center-tab-ids";
export type { BuiltinPage } from "./center-tab-ids";
export { DRAFT_SESSION_TAB_ID, rebaseCenterTabsPayload } from "./center-tabs-persistence";
export type { CenterTabsPersistedPayload } from "./center-tabs-persistence";
export type * from "./store/types";

const closedSessionAckTombstones = new Set<string>();

export const useCenterTabs = create<CenterTabsState>((set, get) => {
  const initial = readCenterTabsPayload();
  return {
    navigationRoute: initial.tabs.find(tab => tab.id === initial.activeId)?.navigationRoute,
    tabs: initial.tabs,
    activeId: initial.activeId,
    groups: initial.groups,
    splitWebTabId: initial.splitWebTabId,
    splitRatio: initial.splitRatio,
    fileNavigationHistory: fileHistoryFor(initial.tabs.find(tab => tab.id === initial.activeId)),
    fileNavigationRestore: initial.tabs.find(tab => tab.id === initial.activeId)?.fileNavigationSnapshot ?? null,
    ...layoutActions(set, get),
    ...navigationActions(set, get, closedSessionAckTombstones),
    ...sessionsActions(set, get, closedSessionAckTombstones),
    ...pagesActions(set, get, closedSessionAckTombstones),
    ...browserActions(set, get),
    ...splitActions(set, get)
  };
});

export const { sessionAckIsActive, snapshotCenterTabsPayload, validateTransferredTabs, insertTransferredTabs, removeTransferredTabs, replaceCenterTabsPayload, persistCurrentCenterTabsPayload } = bindTransfers(useCenterTabs, closedSessionAckTombstones);
