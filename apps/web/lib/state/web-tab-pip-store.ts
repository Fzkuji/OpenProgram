import { create } from "zustand";

import { sessionHistory } from "./session-tab-history";
import { webTabId } from "@/lib/state/center-tab-ids";
import { findCenterTabGroup } from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { clampPipRectAspect } from "./web-tab-pip-geometry";

export { startWebTabCaptureLoop } from "./web-tab-capture-loop";
export {
  PIP_HEADER_HEIGHT,
  PIP_RESIZE_DIRS,
  clampPipRectAspect,
  pipContentAspect,
  resizePipRect,
} from "./web-tab-pip-geometry";
export type { PipResizeDir } from "./web-tab-pip-geometry";

/** Ephemeral picture-in-picture host for an agent-opened WebTab.
 *  Not persisted — closing the preview or expanding to split/fullscreen
 *  clears the visible host. The leaf tab itself stays in the center-tabs
 *  store. Position/size live only in memory. */
export const PIP_MIN_WIDTH = 240;
export const PIP_MIN_HEIGHT = 160;
export const PIP_DEFAULT_WIDTH = 300;
export const PIP_DEFAULT_HEIGHT = 198.75;
export const PIP_EXPANDED_WIDTH = 720;
export const PIP_EXPANDED_HEIGHT = 435;

export type WebTabPipRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type PipHostMode = "chat";

type PipCenterState = {
  tabs: readonly { id: string; kind: string; sessionId?: string }[];
  activeId: string | null;
  groups: readonly { memberIds: string[]; visibleIds: string[] }[];
  splitWebTabId?: string | null;
};

/** Session↔web pairing survives PiP rebinds: every tab that has floated
 *  for a session stays paired with it until either side closes. This is
 *  what lets a session keep several agent-opened pages while the single
 *  floating slot shows only the current working page. */
const pairedOwnerByTabId = new Map<string, string>();
const pairedSessionByTabId = new Map<string, string | null>();

export function pipPairedOwnerFor(tabId: string): string | null {
  return pairedOwnerByTabId.get(tabId) ?? null;
}

/** Record session↔web pairing without showing the floating host.
 *  Split-open uses this; PiP `show()` goes through it too. */
export function registerPipPair(tabId: string, ownerTabId: string): void {
  pairedOwnerByTabId.set(tabId, ownerTabId);
  pairedSessionByTabId.set(tabId, useCenterTabs.getState().tabs.find(tab => tab.id === ownerTabId)?.sessionId ?? null);
}

export const useWebTabPip = create<{
  tabId: string | null;
  ownerTabId: string | null;
  ownerSessionId: string | null;
  previews: Record<string, string>;
  backgroundTabId: string | null;
  backgroundOwnerTabId: string | null;
  rect: WebTabPipRect | null;
  expandedSize: { width: number; height: number } | null;
  show: (tabId: string, ownerTabId: string) => void;
  hide: () => void;
  end: () => void;
  setRect: (rect: WebTabPipRect) => void;
  setExpandedSize: (size: { width: number; height: number } | null) => void;
}>((set) => ({
  tabId: null,
  ownerTabId: null,
  ownerSessionId: null,
  previews: {},
  backgroundTabId: null,
  backgroundOwnerTabId: null,
  rect: null,
  expandedSize: null,
  show: (tabId, ownerTabId) => {
    registerPipPair(tabId, ownerTabId);
    const ownerSessionId = pairedSessionByTabId.get(tabId) ?? null;
    set(state => ({
      tabId,
      ownerTabId,
      ownerSessionId,
      previews: ownerSessionId ? { ...state.previews, [ownerSessionId]: tabId } : state.previews,
      backgroundTabId: null,
      backgroundOwnerTabId: null,
    }));
  },
  hide: () => set((s) => ({
    tabId: null,
    ownerTabId: null,
    backgroundTabId: s.tabId ?? s.backgroundTabId,
    backgroundOwnerTabId: s.ownerTabId ?? s.backgroundOwnerTabId,
  })),
  end: () => set({
    ownerSessionId: null,
    previews: {},
    tabId: null,
    ownerTabId: null,
    backgroundTabId: null,
    backgroundOwnerTabId: null,
    expandedSize: null,
  }),
  setRect: (rect) => set({ rect }),
  setExpandedSize: (expandedSize) => set({ expandedSize }),
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
    tabs: readonly { id: string; kind: string; sessionId?: string }[];
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
  const ownerSessionId = pairedSessionByTabId.get(tabId);
  const owner = store.tabs.find(tab => tab.id === ownerTabId);
  if (ownerSessionId && owner?.sessionId !== ownerSessionId) {
    const entry = owner?.kind === "session"
      ? sessionHistory(owner).entries.find(item => item.sessionId === ownerSessionId) : undefined;
    store.openSessionTab(ownerSessionId, entry?.title ?? ownerSessionId);
  }
  const currentOwner = useCenterTabs.getState().activeId;
  if (!currentOwner) return false;
  useWebTabPip.getState().show(tabId, currentOwner);
  return true;
}

function pipCoverBase(tabId: string, state: PipCenterState): boolean {
  if (!state.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
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
  const pip = useWebTabPip.getState();
  const owner = state.tabs.find(tab => tab.id === ownerTabId);
  const sessionId = pip.tabId === tabId && pip.ownerTabId === ownerTabId
    ? pip.ownerSessionId : pairedSessionByTabId.get(tabId);
  return pipCoverBase(tabId, state) && !!ownerTabId && state.activeId === ownerTabId
    && (owner?.sessionId ?? null) === (sessionId ?? null);
}

/** Chat PiP is a floating overlay on the owner conversation only. */
export function pipHostMode(
  tabId: string | null,
  ownerTabId: string | null,
  state: PipCenterState = useCenterTabs.getState(),
): PipHostMode | null {
  if (!tabId || !pipCoversCenter(tabId, ownerTabId, state)) return null;
  return "chat";
}

export function pipPresentationSize(
  rect: WebTabPipRect | null,
  expanded: boolean,
  expandedSize: { width: number; height: number } | null = null,
): { width: number; height: number } {
  if (expanded) {
    return {
      width: expandedSize?.width ?? PIP_EXPANDED_WIDTH,
      height: expandedSize?.height ?? PIP_EXPANDED_HEIGHT,
    };
  }
  return {
    width: rect?.width ?? PIP_DEFAULT_WIDTH,
    height: rect?.height ?? PIP_DEFAULT_HEIGHT,
  };
}

export function pipChatRect(
  rect: WebTabPipRect | null,
  expanded: boolean,
  box: WebTabPipRect,
  expandedSize: { width: number; height: number } | null = null,
): WebTabPipRect {
  const size = pipPresentationSize(rect, expanded, expandedSize);
  const origin = rect
    ? { x: rect.x, y: rect.y }
    : {
      x: box.x + Math.max(0, box.width - size.width - 12),
      y: box.y + 78,
    };
  return clampPipRect({ ...origin, ...size }, box);
}

export function clampPipRect(
  rect: WebTabPipRect,
  box: WebTabPipRect,
): WebTabPipRect {
  return clampPipRectAspect(rect, box, PIP_MIN_WIDTH, PIP_MIN_HEIGHT);
}

useCenterTabs.subscribe((state) => {
  const pip = useWebTabPip.getState();
  const ids = new Set(state.tabs.map((tab) => tab.id));
  for (const [tabId, ownerId] of pairedOwnerByTabId) {
    if (!ids.has(tabId) || !ids.has(ownerId)) {
      pairedOwnerByTabId.delete(tabId);
      pairedSessionByTabId.delete(tabId);
    }
  }
  if (
    (pip.ownerTabId && !ids.has(pip.ownerTabId))
    || (pip.tabId && !ids.has(pip.tabId))
  ) {
    useWebTabPip.setState({
      tabId: null, ownerTabId: null, ownerSessionId: null,
      backgroundTabId: null, backgroundOwnerTabId: null,
      previews: Object.fromEntries(Object.entries(pip.previews).filter(([, id]) => ids.has(id))),
    });
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
