import { create } from "zustand";

import { webTabId } from "@/lib/state/center-tab-ids";
import { findCenterTabGroup } from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";

/** Ephemeral picture-in-picture host for an agent-opened WebTab.
 *  Not persisted — closing the preview or expanding to split/fullscreen
 *  clears the visible host. The leaf tab itself stays in the center-tabs
 *  store. Position/size live only in memory. */
export const PIP_MIN_WIDTH = 240;
export const PIP_MIN_HEIGHT = 160;

export type WebTabPipRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type PipCenterState = {
  tabs: readonly { id: string; kind: string }[];
  activeId: string | null;
  groups: readonly { memberIds: string[]; visibleIds: string[] }[];
};

export const useWebTabPip = create<{
  tabId: string | null;
  ownerTabId: string | null;
  backgroundTabId: string | null;
  backgroundOwnerTabId: string | null;
  rect: WebTabPipRect | null;
  show: (tabId: string, ownerTabId: string) => void;
  hide: () => void;
  end: () => void;
  setRect: (rect: WebTabPipRect) => void;
}>((set) => ({
  tabId: null,
  ownerTabId: null,
  backgroundTabId: null,
  backgroundOwnerTabId: null,
  rect: null,
  show: (tabId, ownerTabId) => set({
    tabId,
    ownerTabId,
    backgroundTabId: null,
    backgroundOwnerTabId: null,
  }),
  hide: () => set((s) => ({
    tabId: null,
    ownerTabId: null,
    backgroundTabId: s.tabId ?? s.backgroundTabId,
    backgroundOwnerTabId: s.ownerTabId ?? s.backgroundOwnerTabId,
  })),
  end: () => set({
    tabId: null,
    ownerTabId: null,
    backgroundTabId: null,
    backgroundOwnerTabId: null,
  }),
  setRect: (rect) => set({ rect }),
}));

export function peekWebTabPipId(): string | null {
  return useWebTabPip.getState().tabId;
}

export function peekWebTabPipOwnerId(): string | null {
  return useWebTabPip.getState().ownerTabId;
}

export function peekWebTabPipBackgroundId(): string | null {
  return useWebTabPip.getState().backgroundTabId;
}

export function peekLiveWebTabPipId(
  state: PipCenterState = useCenterTabs.getState(),
): string | null {
  const { tabId, ownerTabId } = useWebTabPip.getState();
  if (!tabId || !pipCoversCenter(tabId, ownerTabId, state)) return null;
  return tabId;
}

export function pipBoundTabId(): string | null {
  const { tabId, ownerTabId } = useWebTabPip.getState();
  return tabId && ownerTabId ? tabId : null;
}

/** Same-URL open must not steal another session's bound PiP leaf. */
export function pipOpenMustFork(
  url: string,
  activeOwnerId: string,
  boundTabId: string | null = peekWebTabPipId(),
  boundOwnerId: string | null = peekWebTabPipOwnerId(),
): boolean {
  return webTabId(url) === boundTabId
    && !!boundOwnerId
    && boundOwnerId !== activeOwnerId;
}

export const usePipSnapshots = create<{
  shots: Record<string, string>;
  setSnapshot: (tabId: string, dataUrl: string) => void;
}>((set) => ({
  shots: {},
  setSnapshot: (tabId, dataUrl) =>
    set((s) => ({ shots: { ...s.shots, [tabId]: dataUrl } })),
}));

export function getSnapshot(tabId: string): string | undefined {
  return usePipSnapshots.getState().shots[tabId];
}

export function setSnapshot(tabId: string, dataUrl: string): void {
  usePipSnapshots.getState().setSnapshot(tabId, dataUrl);
}

/** Most recently focused session tab — the collapse-to-PiP fallback
 *  target for ungrouped web tabs ("float back to where I just was"). */
let recentSessionTabId: string | null = null;
function trackRecentSession(s: {
  tabs: readonly { id: string; kind: string }[];
  activeId: string | null;
}): void {
  const active = s.tabs.find((tab) => tab.id === s.activeId);
  if (active?.kind === "session") recentSessionTabId = active.id;
}
trackRecentSession(useCenterTabs.getState());
useCenterTabs.subscribe(trackRecentSession);

/** The session tab a collapse-to-PiP would bind this WebTab to:
 *  the session sharing its group, else the most recently focused
 *  session, else the first session tab. */
export function pipCollapseTargetFor(
  tabId: string,
  store: {
    tabs: readonly { id: string; kind: string }[];
    groups: readonly { memberIds: string[] }[];
  } = useCenterTabs.getState(),
): string | null {
  if (!store.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return null;
  }
  const group = store.groups.find((item) => item.memberIds.includes(tabId));
  const partnerId = group?.memberIds.find((id) => id !== tabId);
  const partner = partnerId
    ? store.tabs.find((tab) => tab.id === partnerId)
    : undefined;
  if (partner?.kind === "session") return partner.id;
  const recent = store.tabs.find(
    (tab) => tab.id === recentSessionTabId && tab.kind === "session",
  );
  return recent?.id
    ?? store.tabs.find((tab) => tab.kind === "session")?.id
    ?? null;
}

/** Reverse of PiP expand: keep the same WebTab leaf, move focus off it
 *  so the floating host can cover center again. Does not reload.
 *  A tab already floating for a session returns to that owner instead
 *  of being re-bound to another session. */
export function collapseWebTabToPip(tabId: string): boolean {
  const store = useCenterTabs.getState();
  const pip = useWebTabPip.getState();
  const boundOwnerId =
    pip.tabId === tabId
      && pip.ownerTabId
      && store.tabs.some((tab) => tab.id === pip.ownerTabId)
      ? pip.ownerTabId
      : null;
  const ownerTabId = boundOwnerId ?? pipCollapseTargetFor(tabId, store);
  if (!ownerTabId) return false;
  const group = findCenterTabGroup(store.groups, tabId);
  if (store.splitWebTabId === tabId) {
    store.setSplitWebTab(null);
  } else if (group) {
    store.ungroupTab(tabId);
  }
  store.setActive(ownerTabId);
  useWebTabPip.getState().show(tabId, ownerTabId);
  return true;
}

function pipCoverBase(tabId: string, state: PipCenterState): boolean {
  if (!state.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  if (state.activeId === tabId) return false;
  const group = state.activeId
    ? state.groups.find((item) => item.memberIds.includes(state.activeId!))
    : undefined;
  if (group?.visibleIds.includes(tabId)) return false;
  // The PiP is a chat-side preview: it only floats over the session pane.
  // When the user focuses any other center page (files, terminal, another
  // web tab, ...) it must not cover that page.
  if (state.activeId) {
    const active = state.tabs.find((tab) => tab.id === state.activeId);
    if (active && active.kind !== "session") return false;
  }
  return true;
}

export function pipCoversCenter(
  tabId: string,
  ownerTabId: string | null,
  state: PipCenterState = useCenterTabs.getState(),
): boolean {
  return pipCoverBase(tabId, state) && !!ownerTabId && state.activeId === ownerTabId;
}

export function clampPipRect(
  rect: WebTabPipRect,
  box: WebTabPipRect,
): WebTabPipRect {
  const width = Math.max(
    PIP_MIN_WIDTH,
    Math.min(rect.width, Math.max(PIP_MIN_WIDTH, box.width)),
  );
  const height = Math.max(
    PIP_MIN_HEIGHT,
    Math.min(rect.height, Math.max(PIP_MIN_HEIGHT, box.height)),
  );
  return {
    x: Math.max(box.x, Math.min(rect.x, box.x + box.width - width)),
    y: Math.max(box.y, Math.min(rect.y, box.y + box.height - height)),
    width,
    height,
  };
}

useCenterTabs.subscribe((state) => {
  const pip = useWebTabPip.getState();
  const ids = new Set(state.tabs.map((tab) => tab.id));
  if (
    (pip.ownerTabId && !ids.has(pip.ownerTabId))
    || (pip.tabId && !ids.has(pip.tabId))
  ) {
    pip.end();
    return;
  }
  if (
    (pip.backgroundTabId && !ids.has(pip.backgroundTabId))
    || (pip.backgroundOwnerTabId && !ids.has(pip.backgroundOwnerTabId))
  ) {
    useWebTabPip.setState({
      backgroundTabId: null,
      backgroundOwnerTabId: null,
    });
  }
});
