"use client";

import { useEffect } from "react";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { ownerOnCanvas, useWebTabPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { pipMotionAllowed } from "@/lib/browser/web-tab/web-tab-pip-motion";
import {
  followPreviewBinding,
  getPreviewPreference,
  hideResourcePreview,
  recoverSessionResources,
  resourceSessionId,
  setBrowserConnection,
  showResourcePreview,
  useBrowserResourceStore,
  viewedBranchFor,
} from "@/lib/chat/resources/session-resources";
import {
  clearYieldingOnDisconnect,
  settlePendingClose,
  useBrowserControlStore,
} from "@/lib/browser/control/browser-control";

export { recoverSessionResources };

export function viewedConversationId(
  tabs: readonly { id: string; kind: string; sessionId?: string; agentSessionId?: string; diffSessionId?: string; draft?: boolean }[],
  activeId: string | null,
  currentSessionId?: string | null,
): string | null {
  const active = tabs.find(tab => tab.id === activeId);
  return resourceSessionId(active as never) || currentSessionId || null;
}

type PreviewBinding = { tabId: string; ownerTabId: string };
type ProjectionTabs = readonly { id: string; kind: string; sessionId?: string }[];

/** The Page a conversation previews: its follow target, else the Page it
 *  last floated in this run. */
function sessionPreview(sessionId: string, tabs: ProjectionTabs): PreviewBinding | null {
  const branchId = useBrowserResourceStore.getState().viewedBranch[sessionId] ?? null;
  const binding = followPreviewBinding(sessionId, branchId, tabs);
  if (binding) return binding;
  const pref = getPreviewPreference(sessionId, branchId);
  const savedTabId = !branchId && !pref.hidden && !pref.targetId
    ? useWebTabPip.getState().previews[sessionId] : null;
  const owner = tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
  if (savedTabId && owner && tabs.some(tab => tab.id === savedTabId)) {
    return { tabId: savedTabId, ownerTabId: owner.id };
  }
  return null;
}

/** Conversations in the other panes of the focused window's split. */
export function besidePaneSessions(
  sessionId: string | null,
  tabs: ProjectionTabs,
  center: { activeId: string | null; groups: readonly { memberIds: string[]; visibleIds: string[] }[] } = useCenterTabs.getState(),
): string[] {
  const activeId = center.activeId;
  if (!activeId) return [];
  const group = center.groups.find(item => item.memberIds.includes(activeId));
  if (!group) return [];
  const out: string[] = [];
  for (const paneId of group.visibleIds) {
    if (paneId === activeId) continue;
    const other = tabs.find(tab => tab.id === paneId && tab.kind === "session")?.sessionId;
    if (other && other !== sessionId && !out.includes(other)) out.push(other);
  }
  return out;
}

export function applyFollowPreview(
  sessionId: string | null,
  tabs: ProjectionTabs,
): PreviewBinding | null {
  if (!sessionId) return null;
  const pip = useWebTabPip.getState();
  const show = (binding: PreviewBinding, owner: string) => {
    if (pip.tabId !== binding.tabId || pip.ownerTabId !== binding.ownerTabId || pip.ownerSessionId !== owner) {
      pip.show(binding.tabId, binding.ownerTabId);
    }
    return binding;
  };
  const own = sessionPreview(sessionId, tabs);
  if (own) return show(own, sessionId);
  // The focused pane has no preview of its own. Another pane's preview
  // stays while that pane is on screen beside it, and one a pane beside it
  // has comes up (after a restart, say), whichever pane has focus.
  if (pip.tabId && pip.ownerTabId && pip.ownerSessionId !== sessionId
    && ownerOnCanvas(pip.ownerTabId)) return null;
  for (const other of besidePaneSessions(sessionId, tabs)) {
    const binding = sessionPreview(other, tabs);
    if (binding) return show(binding, other);
  }
  if (pip.tabId) pip.hide();
  return null;
}

/** Float a page again after the user closed its preview. Closing hid the preview for the
 *  conversation; showing it again undoes that, or the projection hides the page at once.
 *  It unfolds out of the composer's web page chip (web-tab-pip-motion.ts). */
export function restorePagePreview(tabId: string, ownerTabId: string): void {
  if (useWebTabPip.getState().motion) return;
  const sessionId = useCenterTabs.getState().tabs.find(tab => tab.id === ownerTabId)?.sessionId;
  if (sessionId) showResourcePreview(sessionId, viewedBranchFor(sessionId));
  useWebTabPip.getState().show(tabId, ownerTabId);
  if (pipMotionAllowed()) useWebTabPip.setState({ motion: "unfolding" });
}

/** Close the floating preview: it folds into the composer's web page chip
 *  first (components/center-tabs/pip/web-tab-pip.tsx), then closes with
 *  closePagePreview. The page stays in the background for restorePagePreview. */
export function hidePagePreview(): void {
  const pip = useWebTabPip.getState();
  if (pip.motion) return;
  if (pip.tabId && pipMotionAllowed() && document.querySelector('[data-pip="true"]')) {
    useWebTabPip.setState({ motion: "folding" });
    return;
  }
  closePagePreview();
}

/** Close the floating preview at once. */
export function closePagePreview(): void {
  const sessionId = useWebTabPip.getState().ownerSessionId;
  if (sessionId) hideResourcePreview(sessionId, viewedBranchFor(sessionId));
  useWebTabPip.setState({ motion: null });
  useWebTabPip.getState().hide();
}

export function applyBrowserConnection(connected: boolean): void {
  setBrowserConnection(connected);
  if (!connected) clearYieldingOnDisconnect();
}

/** Window-level projection: follow PiP, snapshot recovery, disconnect. One owner. */
export function BrowserResourceProjection({ sessionId }: { sessionId: string | null }) {
  const tabs = useCenterTabs(s => s.tabs);
  const followEpoch = useBrowserResourceStore(s => s.followEpoch);
  const viewedBranch = useBrowserResourceStore(s => s.viewedBranch);
  const rows = useBrowserResourceStore(s => s.rows);
  const pendingCloses = useBrowserControlStore(s => s.pendingCloses);

  const besideKey = useCenterTabs(s => besidePaneSessions(sessionId, s.tabs, s).join("\n"));

  useEffect(() => {
    applyFollowPreview(sessionId, tabs);
  }, [sessionId, tabs, followEpoch, viewedBranch, besideKey, rows]);

  useEffect(() => {
    settlePendingClose(id => useCenterTabs.getState().closeTab(id));
  }, [rows, pendingCloses]);

  useEffect(() => {
    if (!sessionId) return;
    // The panes beside the focused one can float a preview too. The
    // focused conversation goes last, so its snapshot is the freshest.
    const sessions = [...(besideKey ? besideKey.split("\n") : []), sessionId];
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    // A hidden window skips the poll and catches up once it shows again.
    async function poll() {
      if (running) return;
      running = true;
      clearTimeout(timer);
      if (document.visibilityState !== "hidden") {
        for (const id of sessions) {
          try {
            await recoverSessionResources(id);
          } catch {
            /* snapshot recovery is optional */
          }
        }
      }
      running = false;
      if (!disposed) timer = setTimeout(poll, 3000);
    }
    const onVisible = () => { if (document.visibilityState === "visible") void poll(); };
    void poll();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId, besideKey]);

  useEffect(() => {
    const onConnection = (event: Event) => {
      const connected = (event as CustomEvent<{ connected?: boolean }>).detail?.connected;
      if (typeof connected === "boolean") applyBrowserConnection(connected);
    };
    const onBranches = () => {
      if (sessionId) void recoverSessionResources(sessionId).catch(() => {});
    };
    window.addEventListener("op:browser-connection", onConnection);
    window.addEventListener("branches-updated", onBranches);
    return () => {
      window.removeEventListener("op:browser-connection", onConnection);
      window.removeEventListener("branches-updated", onBranches);
    };
  }, [sessionId]);

  return null;
}
