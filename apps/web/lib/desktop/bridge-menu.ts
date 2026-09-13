// Desktop bridge menu responsibilities.
import { agentCanAccessWebTab } from "../browser/web-page-management";
/**
 * Desktop bridge — typed accessor for the Electron preload API
 * (`window.openprogramDesktop`) plus the renderer-side bookkeeping the
 * contract leaves to us:
 *
 *  - which native web views this renderer has created (the bridge has
 *    no list call, so destroying views after their tab closes works
 *    off a local set);
 *  - the app-menu DOM CustomEvents ("op-desktop-new-tab" /
 *    "op-desktop-close-tab") wired into the center-tabs store.
 *
 * Absent bridge (plain browser) ⇒ every helper is a no-op and
 * desktopBridge() returns null; callers keep their web fallbacks.
 */
import { useCenterTabs, webTabId } from "@/lib/tabs/center-tabs-store";
import { pipOpenMustFork, registerPipPair, useWebTabPip } from "@/lib/browser/web-tab-pip-store";
import { findCenterTabGroup } from "@/lib/tabs/center-tab-groups";
import "@/lib/net/ws-events";
import { getSocket } from "@/lib/runtime-bridge/state";
import { showActionsEnabled } from "@/lib/browser/browser-control";
import { desktopBridge } from "./bridge-api";
import { receiveBrowserResource, showCenterSurface, subscribeBrowserHumanInput, subscribeWebTabPopups } from "./bridge-events";
import { destroyStaleWebViews, persistNativeWebTabState, reregisterDesktopWindow, restorableResourceForTab, restoreRetainedWebViews, retryRestoreWebTab } from "./bridge-restoration";
import { ensureWebView, isDesktopSplitLayoutAvailable, liveViewIds, waitForWebTabReady, webTabGeometryRevisions } from "./bridge-state";
import { browserPageInventory, closeAgentWebTabResult, finalizeBoundWebTabActivation, finalizeBoundWebTabScreenshot, finalizeWebTabPreview, restorePriorActiveTabAfterFailedWebOpen, selectedMirrorTabById, sendWebTabResult, visibleWebTab, visibleWebTabById } from "./bridge-surfaces";
import { installTabTransferHandlers, recoverPendingTabTransfers } from "./bridge-transfer";
import { destroyStaleTerminals } from "./bridge-terminals";



export let installed = false;

// The server drops a pending renderer request after 15 seconds. Finish locally
// first so a late CDP target resolution cannot orphan an agent-created Page.
export const BACKGROUND_WEBTAB_RESOLVE_TIMEOUT_MS = 12_000;

export function rollbackCreatedAgentPage(
  id: string,
  created: boolean,
): { error: string; reason_code: "page_cleanup_failed" } | undefined {
  if (!created) return undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      useCenterTabs.getState().closeTab(id);
      return undefined;
    } catch {
      // Retry once before declaring that the exact Page needs manual cleanup.
    }
  }
  return {
    error: (
      "desktop web tab did not expose a CDP target; "
      + "agent-created Page cleanup failed"
    ),
    reason_code: "page_cleanup_failed",
  };
}

/**
 * Wire the desktop shell into the tabs store. Idempotent; no-op
 * outside the desktop shell. Currently called on web-pane mount
 * (web-tab-pane.tsx); app-shell should ALSO call it once at startup
 * so the app menu works before the first web tab ever opens.
 *
 *  - "op-desktop-new-tab" CustomEvent (dispatched on window by preload for
 *    menu accelerators) → openNewTabPage. The strip lifecycle owns the close
 *    event because it must close a composite as one dirty-checked entry.
 *  - Store subscription that destroys native views the moment their
 *    tab closes — a closed tab's pane is already unmounted (or never
 *    was, for background tabs), so no component can do this cleanup.
 */
export function installDesktopMenuHandlers(): void {
  if (installed || typeof window === "undefined") return;
  const bridge = desktopBridge();
  if (!bridge) return;
  installed = true;
  const startupWebTabIds = useCenterTabs.getState().tabs
    .filter((tab) => tab.kind === "web")
    .map((tab) => tab.id);
  window.addEventListener("op-desktop-new-tab", () => {
    useCenterTabs.getState().openNewTabPage();
    showCenterSurface();
  });
  subscribeWebTabPopups(bridge);
  subscribeBrowserHumanInput(bridge);
  bridge.webTab.onState((state) => persistNativeWebTabState(state));
  window.addEventListener("op:browser-connection", (event: Event) => {
    if ((event as CustomEvent<{ connected: boolean }>).detail?.connected) {
      void restoreRetainedWebViews(bridge).then(() => reregisterDesktopWindow(bridge));
      return;
    }
    for (const id of liveViewIds) void bridge.webTab.showAction?.(id, null).catch(() => {});
  });
  window.addEventListener("op:browser-actions-changed", () => {
    if (!showActionsEnabled()) {
      for (const id of liveViewIds) void bridge.webTab.showAction?.(id, null).catch(() => {});
    }
  });
  // Exact-window commands and receipts share the authenticated App socket.
  // Retained resource events remain live while the Resources panel is closed.
  window.addEventListener("op:ws-message", (e) => {
    const detail = e.detail;
    if (detail?.type === "browser.resource") {
      receiveBrowserResource(bridge, detail.data);
      return;
    }
    if (detail?.type !== "webtab.command") return;
    const d = detail.data as
      | { op?: string; url?: string; session_id?: string; execution_id?: string; branch_id?: string; window_id?: string; tab_id?: string; req_id?: string; background?: boolean; nonce?: string; expected_geometry_revision?: number }
      | undefined;
    if (!d?.req_id || !["open", "active", "activate", "preview", "screenshot", "list", "resolve", "close", "self_update_capture"].includes(d.op || "")) return;
    const ws = getSocket();
    if (ws?.readyState !== WebSocket.OPEN) return;

    const canAccess = (id: string) => agentCanAccessWebTab(id, d.session_id, useCenterTabs.getState());
    const denied = { ok: false, reason_code: "page_not_accessible", error: "Page is private to another conversation" };
    const replySocket = ws;
    const guardedSocket = { send(payload: string) {
      const result = JSON.parse(payload);
      const targetId = d.tab_id || (d.op === "active" ? result.tab_id : null);
      if (targetId && result.ok && d.op !== "close" && !canAccess(targetId)) {
        replySocket.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ...denied }));
      } else replySocket.send(payload);
    } };
    if (d.tab_id && d.op !== "open" && !canAccess(d.tab_id)) {
      ws.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ...denied }));
      return;
    }

    if (d.op === "self_update_capture") {
      const reply = (ok: boolean) => {
        if (getSocket() === ws && ws.readyState === WebSocket.OPEN) {
          guardedSocket.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok, window_id: "main" }));
        }
      };
      if (bridge.windowId !== "main" || d.window_id !== "main" || !bridge.selfUpdateCapture ||
          typeof d.nonce !== "string" || !/^[0-9a-f]{64}$/.test(d.nonce)) {
        reply(false);
      } else {
        void bridge.selfUpdateCapture(d.nonce).then((result) => reply(result.ok === true)).catch(() => reply(false));
      }
      return;
    }

    if (d.op === "close") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab belongs to another window",
        }));
        return;
      }
      const state = useCenterTabs.getState();
      const closed = closeAgentWebTabResult(d.tab_id, state.tabs, state.groups);
      if (closed.ok) state.closeTab(d.tab_id!);
      guardedSocket.send(JSON.stringify({
        action: "webtab_result",
        req_id: d.req_id,
        ok: closed.ok,
        ...(closed.ok
          ? { tab_id: d.tab_id }
          : { error: closed.reason, reason_code: closed.reason }),
      }));
      return;
    }

    if (d.op === "list") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok: false }));
        return;
      }
      void browserPageInventory(bridge, d.session_id).then((inventory) => {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: true,
          ...inventory,
        }));
      });
      return;
    }

    if (d.op === "resolve") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok: false }));
        return;
      }
      const tab = d.tab_id
        ? useCenterTabs.getState().tabs.find((item) => item.id === d.tab_id && item.kind === "web")
        : null;
      void (tab && bridge.webTab.resolve
        ? bridge.webTab.resolve(tab.id)
        : Promise.resolve(null)
      ).then((targetId) => sendWebTabResult(
        guardedSocket,
        d.req_id!,
        tab?.kind === "web" ? tab : { id: d.tab_id ?? "", url: "" },
        targetId,
      ));
      return;
    }

    if (d.op === "screenshot") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab belongs to another window",
        }));
        return;
      }
      const tab = d.tab_id
        ? useCenterTabs.getState().tabs.find(
          (item) => item.id === d.tab_id && item.kind === "web",
        )
        : null;
      if (!tab || !bridge.webTab.capture) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab cannot be captured",
        }));
        return;
      }
      const geometryRevision = webTabGeometryRevisions.get(tab.id) ?? 0;
      if (d.expected_geometry_revision
          && d.expected_geometry_revision !== geometryRevision) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "web tab geometry changed",
          reason_code: "page_context_stale",
          geometry_revision: geometryRevision,
        }));
        return;
      }
      void bridge.webTab.capture(tab.id).then((imageDataUrl) => {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ...finalizeBoundWebTabScreenshot(
            tab.id,
            geometryRevision,
            imageDataUrl,
          ),
        }));
      }).catch(() => {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ...finalizeBoundWebTabScreenshot(
            tab.id,
            geometryRevision,
            null,
          ),
        }));
      });
      return;
    }

    if (d.op === "preview" || d.op === "activate") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab belongs to another window",
        }));
        return;
      }
      const runOp = (tab: { id: string; kind: string } | null) => {
      if (tab && !canAccess(tab.id)) {
        guardedSocket.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ...denied }));
        return;
      }
      if (!tab || tab.kind !== "web") {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab is not visible in this window",
        }));
        return;
      }
      const geometryRevision = webTabGeometryRevisions.get(tab.id) ?? 0;
      if (d.expected_geometry_revision
          && d.expected_geometry_revision !== geometryRevision) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "web tab geometry changed",
          reason_code: "page_context_stale",
          geometry_revision: geometryRevision,
        }));
        return;
      }
      if (d.op === "preview") {
        void bridge.webTab.preview(tab.id, d.background === true).then((result) => {
          guardedSocket.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ...finalizeWebTabPreview(
              tab.id,
              d.expected_geometry_revision ?? 0,
              result,
              d.background === true,
            ),
          }));
        }).catch(() => {
          guardedSocket.send(JSON.stringify({
            action: "webtab_result", req_id: d.req_id,
            ok: false, error: "desktop web tab preview is unavailable",
          }));
        });
      } else {
        void bridge.webTab.activate(tab.id, d.url, true).then((targetId) => {
          guardedSocket.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ...finalizeBoundWebTabActivation(
              tab.id,
              d.expected_geometry_revision ?? 0,
              targetId,
            ),
          }));
        }).catch(() => {
          guardedSocket.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ok: false,
            error: "desktop web tab did not expose a CDP target",
          }));
        });
      }
      };
      // Reading a selected mirror never reveals or reparents the native Page.
      const tab = d.tab_id
        ? (d.op === "preview" && d.background === true
          ? selectedMirrorTabById(d.tab_id)
          : visibleWebTabById(d.tab_id))
        : null;
      const resource = tab?.kind === "web" ? restorableResourceForTab(tab.id) : undefined;
      if (
        tab?.kind === "web"
        && resource
        && resource.status !== "closed"
        && resource.status !== "open"
      ) {
        void retryRestoreWebTab(bridge, tab.id, canAccess).finally(() => runOp(tab));
      } else {
        runOp(tab);
      }
      return;
    }

    if (d.op === "open") {
      if (!d.url) return;
      if (d.window_id && d.window_id !== bridge.windowId) {
        guardedSocket.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab belongs to another window",
        }));
        return;
      }
      // Agent pages belong to Resources even when an older caller omits
      // background. Opening is not an operation or a request for user focus.
      if (d.background || d.session_id) {
        const priorTabIds = new Set(
          useCenterTabs.getState().tabs.map((tab) => tab.id),
        );
        const id = useCenterTabs.getState().ensureExclusiveWebTab(d.url);
        const created = !priorTabIds.has(id);
        if (created) useCenterTabs.getState().markAgentWebTab(id, d.session_id, {
          branchId: d.branch_id, executionId: d.execution_id,
        });
        const ownership = { created, reused: !created };
        ensureWebView(bridge, id, d.url);
        let settled = false;
        let deadline: ReturnType<typeof setTimeout>;
        const expiresAt = Date.now() + BACKGROUND_WEBTAB_RESOLVE_TIMEOUT_MS;
        const finish = (targetId: string | null) => {
          if (settled) return;
          settled = true;
          clearTimeout(deadline);
          const tab = useCenterTabs.getState().tabs.find(
            (item) => item.id === id && item.kind === "web",
          );
          const failure = targetId
            ? undefined
            : rollbackCreatedAgentPage(id, created);
          sendWebTabResult(
            guardedSocket,
            d.req_id!,
            tab?.kind === "web" ? tab : { id, url: d.url },
            targetId,
            ownership,
            failure,
          );
        };
        deadline = setTimeout(
          () => finish(null),
          BACKGROUND_WEBTAB_RESOLVE_TIMEOUT_MS,
        );
        void Promise.resolve()
          .then(() => bridge.webTab.resolve?.(id) ?? null)
          .then(
            (targetId) => finish(Date.now() >= expiresAt ? null : targetId),
            () => finish(null),
          );
        return;
      }
      const state = useCenterTabs.getState();
      const priorTabIds = new Set(state.tabs.map((tab) => tab.id));
      const active = state.tabs.find((tab) => tab.id === state.activeId);
      const priorActiveId = state.activeId;
      const activeGroup = active
        ? findCenterTabGroup(state.groups, active.id)
        : undefined;
      const activeGroupHasWeb = activeGroup?.memberIds.some((memberId) =>
        state.tabs.some((tab) => tab.id === memberId && tab.kind === "web"
          && (!tab.agentOpened || tab.agentSessionId === d.session_id)),
      ) ?? false;
      const canonical = state.tabs.find(tab => tab.id === webTabId(d.url!));
      const ownerConflict = !!canonical?.agentOpened && canonical.agentSessionId !== d.session_id;
      const split = !ownerConflict && active?.kind === "session"
        && (!d.session_id || active.sessionId === d.session_id)
        && isDesktopSplitLayoutAvailable()
        && activeGroupHasWeb;
      const usePip = active?.kind === "session" && !split
        && (!d.session_id || active.sessionId === d.session_id);
      const routeVisible =
        window.location.pathname === "/chat" ||
        window.location.pathname.startsWith("/s/");
      let id: string | null;
      if (split) {
        id = state.openWebTabInSplit(d.url);
        if (active?.kind === "session") registerPipPair(id, active.id);
      } else if (usePip && active) {
        id = ownerConflict || pipOpenMustFork(d.url, active.id)
          ? state.ensureExclusiveWebTab(d.url)
          : state.ensureWebTab(d.url);
        useWebTabPip.getState().show(id, active.id);
      } else if (ownerConflict) {
        id = state.ensureExclusiveWebTab(d.url);
        state.setActive(id);
      } else {
        state.openWebTab(d.url, true);
        id = useCenterTabs.getState().activeId;
      }
      const created = !!id && !priorTabIds.has(id);
      if (created && id) useCenterTabs.getState().markAgentWebTab(id, d.session_id);
      const ownership = { created, reused: !!id && !created };
      if (!split && !routeVisible) {
        const routed = showCenterSurface();
        if (!routed) {
          restorePriorActiveTabAfterFailedWebOpen(priorActiveId, id);
          const failure = id
            ? rollbackCreatedAgentPage(id, created)
            : undefined;
          sendWebTabResult(
            guardedSocket,
            d.req_id!,
            { id: id ?? "", url: d.url },
            null,
            ownership,
            failure ?? { error: "center-tab navigation unavailable" },
          );
          return;
        }
      }
      void (async () => {
        const ready = !!id && await waitForWebTabReady(id, 2000);
        const tab = id
          ? useCenterTabs.getState().tabs.find((item) => item.id === id)
          : null;
        let targetId: string | null = null;
        if (ready && tab?.kind === "web") {
          try {
            targetId = await bridge.webTab.activate(tab.id, tab.url);
          } catch {
            targetId = null;
          }
        }
        if (!targetId && !split) {
          restorePriorActiveTabAfterFailedWebOpen(priorActiveId, id);
        }
        const failure = !targetId && id
          ? rollbackCreatedAgentPage(id, created)
          : undefined;
        sendWebTabResult(
          guardedSocket,
          d.req_id!,
          tab?.kind === "web" ? tab : { id: id ?? "", url: d.url },
          targetId,
          ownership,
          failure,
        );
      })();
      return;
    }

    const active = visibleWebTab();
    const routeVisible =
      window.location.pathname === "/chat" ||
      window.location.pathname.startsWith("/s/");
    if (!routeVisible || !active || !canAccess(active.id)) {
      guardedSocket.send(JSON.stringify({
        action: "webtab_result",
        req_id: d.req_id,
        ok: false,
        error: "no visible active web tab",
      }));
      return;
    }
    void bridge.webTab
      .activate(active.id)
      .then((targetId) => sendWebTabResult(guardedSocket, d.req_id!, active, targetId))
      .catch(() => sendWebTabResult(guardedSocket, d.req_id!, active, null));
  });
  installTabTransferHandlers(bridge);
  // Fixed startup order (multiwindow plan Task 7): committed storage is
  // already hydrated by store init, listeners are installed above, then
  // journal recovery → terminal/orphan acks → ordinary native-view
  // reconciliation → claimPending for a detached window's pending token.
  void recoverPendingTabTransfers(bridge, () => {
    const reconcileNativeResources = () => {
      const tabs = useCenterTabs.getState().tabs;
      destroyStaleWebViews(
        bridge,
        tabs.map((t) => t.id),
      );
      destroyStaleTerminals(bridge, tabs);
    };
    useCenterTabs.subscribe(reconcileNativeResources);
    reconcileNativeResources();
    void restoreRetainedWebViews(bridge, { tabIds: startupWebTabIds }).then(
      () => reregisterDesktopWindow(bridge),
    );
  });
}
