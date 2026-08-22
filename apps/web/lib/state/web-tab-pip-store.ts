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

export const useWebTabPip = create<{
  tabId: string | null;
  ownerTabId: string | null;
  backgroundTabId: string | null;
  backgroundOwnerTabId: string | null;
  rect: WebTabPipRect | null;
  show: (tabId: string, ownerTabId: string) => void;
  hide: () => void;
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

export function peekWebTabPipBackgroundOwnerId(): string | null {
  return useWebTabPip.getState().backgroundOwnerTabId;
}

/** Reverse of PiP expand: keep the same WebTab leaf, move focus off it
 *  so the floating host can cover center again. Does not reload. */
export function collapseWebTabToPip(tabId: string): boolean {
  const store = useCenterTabs.getState();
  if (!store.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  const group = findCenterTabGroup(store.groups, tabId);
  const ownerTabId =
    group?.memberIds.find((id) =>
      store.tabs.some((tab) => tab.id === id && tab.kind === "session"),
    )
    ?? store.tabs.find((tab) => tab.kind === "session")?.id
    ?? null;
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

export function pipCoversCenter(
  tabId: string,
  state: {
    tabs: readonly { id: string; kind: string }[];
    activeId: string | null;
    groups: readonly { memberIds: string[]; visibleIds: string[] }[];
  } = useCenterTabs.getState(),
  ownerTabId = (() => {
    const pip = useWebTabPip.getState();
    return pip.tabId === tabId
      ? pip.ownerTabId
      : pip.backgroundTabId === tabId ? pip.backgroundOwnerTabId : null;
  })(),
): boolean {
  if (!state.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  if (!ownerTabId || state.activeId !== ownerTabId) return false;
  if (state.activeId === tabId) return false;
  const group = state.activeId
    ? state.groups.find((item) => item.memberIds.includes(state.activeId!))
    : undefined;
  if (group?.visibleIds.includes(tabId)) return false;
  return true;
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
