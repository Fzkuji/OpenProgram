"use client";

import { useEffect } from "react";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useSessionStore } from "@/lib/session-store";
import { useWebTabPip } from "@/lib/browser/web-tab-pip-store";
import {
  followPreviewBinding,
  getPreviewPreference,
  recoverSessionResources,
  resourceSessionId,
  setBrowserConnection,
  useBrowserResourceStore,
} from "@/lib/chat/session-resources";
import {
  clearYieldingOnDisconnect,
  settlePendingClose,
  useBrowserControlStore,
} from "@/lib/browser/browser-control";

export { recoverSessionResources };

export function viewedConversationId(
  tabs: readonly { id: string; kind: string; sessionId?: string; agentSessionId?: string; diffSessionId?: string; draft?: boolean }[],
  activeId: string | null,
  currentSessionId?: string | null,
): string | null {
  const active = tabs.find(tab => tab.id === activeId);
  return resourceSessionId(active as never) || currentSessionId || null;
}

export function applyFollowPreview(
  sessionId: string | null,
  tabs: readonly { id: string; kind: string; sessionId?: string }[],
): { tabId: string; ownerTabId: string } | null {
  if (!sessionId) return null;
  const branchId = useBrowserResourceStore.getState().viewedBranch[sessionId] ?? null;
  const binding = followPreviewBinding(sessionId, branchId, tabs);
  const pip = useWebTabPip.getState();
  if (!binding) {
    const pref = getPreviewPreference(sessionId, branchId);
    const savedTabId = !branchId && !pref.hidden && !pref.targetId ? pip.previews[sessionId] : null;
    const owner = tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
    if (savedTabId && owner && tabs.some(tab => tab.id === savedTabId)) {
      pip.show(savedTabId, owner.id);
      return { tabId: savedTabId, ownerTabId: owner.id };
    }
    if (pip.tabId) pip.hide();
    return null;
  }
  if (pip.tabId !== binding.tabId || pip.ownerTabId !== binding.ownerTabId || pip.ownerSessionId !== sessionId) {
    pip.show(binding.tabId, binding.ownerTabId);
  }
  return binding;
}

export function applyBrowserConnection(connected: boolean): void {
  setBrowserConnection(connected);
  if (!connected) clearYieldingOnDisconnect();
}

/** Window-level projection: follow PiP, snapshot recovery, disconnect. One owner. */
export function BrowserResourceProjection() {
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const currentSessionId = useSessionStore(s => s.currentSessionId);
  const followEpoch = useBrowserResourceStore(s => s.followEpoch);
  const viewedBranch = useBrowserResourceStore(s => s.viewedBranch);
  const rows = useBrowserResourceStore(s => s.rows);
  const pendingCloses = useBrowserControlStore(s => s.pendingCloses);
  const sessionId = viewedConversationId(tabs, activeId, currentSessionId);

  useEffect(() => {
    applyFollowPreview(sessionId, tabs);
  }, [sessionId, tabs, followEpoch, viewedBranch]);

  useEffect(() => {
    settlePendingClose(id => useCenterTabs.getState().closeTab(id));
  }, [rows, pendingCloses]);

  useEffect(() => {
    if (!sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        await recoverSessionResources(sessionId!);
      } catch {
        /* snapshot recovery is optional */
      }
      if (!disposed) timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [sessionId]);

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
