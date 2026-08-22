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

/** Session↔web pairing survives PiP rebinds: every tab that has floated
 *  for a session stays paired with it until either side closes. This is
 *  what lets a session keep several agent-opened pages while the single
 *  floating slot shows only the current working page. */
const pairedOwnerByTabId = new Map<string, string>();

export function pipPairedOwnerFor(tabId: string): string | null {
  return pairedOwnerByTabId.get(tabId) ?? null;
}

/** Record session↔web pairing without showing the floating host.
 *  Split-open uses this; PiP `show()` goes through it too. */
export function registerPipPair(tabId: string, ownerTabId: string): void {
  pairedOwnerByTabId.set(tabId, ownerTabId);
}

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
  show: (tabId, ownerTabId) => {
    registerPipPair(tabId, ownerTabId);
    set({
      tabId,
      ownerTabId,
      backgroundTabId: null,
      backgroundOwnerTabId: null,
    });
  },
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

export function peekWebTabPipBackgroundOwnerId(): string | null {
  return useWebTabPip.getState().backgroundOwnerTabId;
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

/** The session tab a collapse-to-PiP would bind this WebTab to: the
 *  session sharing its group, else the remembered pairing, else none.
 *  A web tab with no session relationship has nothing to float over —
 *  no guessing. */
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
  const paired = pairedOwnerByTabId.get(tabId);
  return paired
      && store.tabs.some((tab) => tab.id === paired && tab.kind === "session")
    ? paired
    : null;
}

/** Agent-interaction reveal: float a web tab so the active session's
 *  agent can act on it. Unpaired pages pair to this session via `show()`.
 *  A page already paired to another session is refused — no hijack. */
export function revealAgentWebTab(tabId: string): boolean {
  const state = useCenterTabs.getState();
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  if (active?.kind !== "session") return false;
  if (!state.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  const paired = pairedOwnerByTabId.get(tabId);
  if (paired && paired !== active.id) return false;
  useWebTabPip.getState().show(tabId, active.id);
  return true;
}

/** Reverse of PiP expand: keep the same WebTab leaf, move focus off it
 *  so the floating host can cover center again. Does not reload.
 *  A tab already floating for a session returns to that owner instead
 *  of being re-bound to another session. Hidden previews keep the
 *  background owner across the hide/show transition. */
export function collapseWebTabToPip(tabId: string): boolean {
  const store = useCenterTabs.getState();
  const pip = useWebTabPip.getState();
  const rememberedOwnerId = pip.tabId === tabId
    ? pip.ownerTabId
    : pip.backgroundTabId === tabId ? pip.backgroundOwnerTabId : null;
  const boundOwnerId =
    rememberedOwnerId
      && store.tabs.some((tab) => tab.id === rememberedOwnerId)
      ? rememberedOwnerId
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
  for (const [tabId, ownerId] of pairedOwnerByTabId) {
    if (!ids.has(tabId) || !ids.has(ownerId)) pairedOwnerByTabId.delete(tabId);
  }
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
