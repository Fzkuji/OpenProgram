import { create } from "zustand";

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

/** Reverse of PiP expand: keep the same WebTab leaf, move focus off it
 *  so the floating host can cover center again. Does not reload. */
export function collapseWebTabToPip(tabId: string): boolean {
  const store = useCenterTabs.getState();
  if (!store.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  const group = findCenterTabGroup(store.groups, tabId);
  const partnerId = group?.memberIds.find((id) => id !== tabId);
  const partner = partnerId
    ? store.tabs.find((tab) => tab.id === partnerId)
    : undefined;
  const ownerTabId = partner?.kind === "session"
    ? partner.id
    : store.tabs.find((tab) => tab.kind === "session")?.id ?? null;
  if (!ownerTabId) return false;
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

export function pipParkedOver(
  tabId: string,
  ownerTabId: string,
  state: PipCenterState = useCenterTabs.getState(),
): boolean {
  if (!pipCoverBase(tabId, state)) return false;
  if (!state.activeId || state.activeId === ownerTabId) return false;
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  return !!active && active.kind === "session";
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
