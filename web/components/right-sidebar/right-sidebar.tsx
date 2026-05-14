"use client";

/**
 * Right Sidebar — React port of web/public/html/_right-sidebar.html +
 * web/public/js/shared/right-dock.js (shell only).
 *
 * Mirrors the left `<Sidebar />`: this component renders the visible
 * shell (icon rail + content host with the History / Detail / Branches
 * view children) but the *inner* content of each view is still owned
 * by legacy JS (history-graph.js writes into `.history-body`, ui.js
 * writes into `#detailBody` / `#detailTitle`, conversations.js writes
 * into `#branchesPanel`). We keep those legacy IDs as plain divs so the
 * still-loaded shared JS keeps painting into them without modification.
 *
 * Open / view state lives in `useSessionStore.rightDock` and is
 * persisted to `localStorage` under the same keys the legacy
 * `right-dock.js` used (`rightSidebarOpen`, `rightSidebarView`) so a
 * stale tab from before the migration restores into the same state.
 *
 * Legacy globals installed here (mirror right-dock.js's public API):
 *   window.rightDock.{show, close, toggle, restore}
 *   window.toggleDetail / closeDetail
 *   window.toggleHistoryPanel / openHistoryPanel / closeHistoryPanel
 * showDetail() in ui.js calls `window.rightDock.show('detail')`; the
 * topbar branch chip calls `toggleHistoryPanel`. These shims keep that
 * working without touching the legacy JS until those callers migrate.
 */

import { useEffect } from "react";
import { useSessionStore } from "@/lib/session-store";

// View IDs that round-trip through the `data-view` attribute. Matches
// the legacy template exactly: "history" picks `<div data-view="history">`,
// "detail" picks `<div data-view="detail">`.
const VIEW_HISTORY = "history";
const VIEW_DETAIL = "detail";

export function RightSidebar() {
  const open = useSessionStore((s) => s.rightDock.open);
  const view = useSessionStore((s) => s.rightDock.view);
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);

  // Install window.rightDock + legacy shims so the still-loaded shared
  // JS (ui.js showDetail, branches code, topbar history-panel toggles)
  // can drive open/close + view switching without seeing the React
  // store directly. Each shim resolves the current state via
  // useSessionStore.getState() at call time so the values are always
  // fresh — no stale closure capture.
  useEffect(() => {
    const w = window as unknown as {
      rightDock?: {
        show: (view?: string) => void;
        close: () => void;
        toggle: (view?: string) => void;
        restore: () => void;
      };
      toggleDetail?: () => void;
      closeDetail?: () => void;
      toggleHistoryPanel?: () => void;
      openHistoryPanel?: () => void;
      closeHistoryPanel?: () => void;
    };
    const prev = {
      rightDock: w.rightDock,
      toggleDetail: w.toggleDetail,
      closeDetail: w.closeDetail,
      toggleHistoryPanel: w.toggleHistoryPanel,
      openHistoryPanel: w.openHistoryPanel,
      closeHistoryPanel: w.closeHistoryPanel,
    };

    function getState() {
      return useSessionStore.getState();
    }
    function show(v?: string) {
      const s = getState();
      if (v) s.setRightDockView(v);
      s.setRightDockOpen(true);
    }
    function close() {
      getState().setRightDockOpen(false);
    }
    function toggle(v?: string) {
      const s = getState();
      const cur = s.rightDock;
      if (!v) {
        s.setRightDockOpen(!cur.open);
        return;
      }
      if (!cur.open) {
        s.setRightDockView(v);
        s.setRightDockOpen(true);
      } else if (cur.view === v) {
        s.setRightDockOpen(false);
      } else {
        s.setRightDockView(v);
      }
    }
    function restore() {
      // The store reads localStorage at create time; nothing to do here.
      // Kept for legacy compatibility — right-dock.js called this after
      // HTML inject and AppShell still references it on the fallback path.
    }

    w.rightDock = { show, close, toggle, restore };
    w.toggleDetail = () => toggle(VIEW_DETAIL);
    w.closeDetail = () => {
      try {
        const ws = window as unknown as { selectedPath?: unknown };
        if ("selectedPath" in ws) ws.selectedPath = null;
      } catch {
        /* ignore */
      }
      close();
    };
    w.toggleHistoryPanel = () => toggle(VIEW_HISTORY);
    w.openHistoryPanel = () => show(VIEW_HISTORY);
    w.closeHistoryPanel = () => close();

    return () => {
      w.rightDock = prev.rightDock;
      w.toggleDetail = prev.toggleDetail;
      w.closeDetail = prev.closeDetail;
      w.toggleHistoryPanel = prev.toggleHistoryPanel;
      w.openHistoryPanel = prev.openHistoryPanel;
      w.closeHistoryPanel = prev.closeHistoryPanel;
    };
  }, []);

  function onToggleRail() {
    setRightDockOpen(!open);
  }
  function onNavClick(v: string) {
    if (!open) {
      setRightDockView(v);
      setRightDockOpen(true);
      return;
    }
    if (view === v) {
      setRightDockOpen(false);
    } else {
      setRightDockView(v);
    }
  }

  // `data-view` attr is preserved so the legacy CSS rules in
  // 09-right-dock.css (`.right-sidebar[data-view="history"]
  // .right-view[data-view="history"] { display: flex }`) keep working
  // unchanged. The .collapsed class drives the icon-rail-only width
  // (defined in 02-sidebar.css).
  return (
    <aside
      id="rightSidebar"
      className={"sidebar right-sidebar" + (open ? "" : " collapsed")}
      data-view={view}
    >
      <div className="sidebar-header right-sidebar-header">
        <button
          className="sidebar-toggle"
          onClick={onToggleRail}
          title="Toggle panel"
          type="button"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 256 256"
            fill="currentColor"
            style={{ transform: "scaleX(-1)" }}
          >
            <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,160H88V56H216V200Z" />
          </svg>
        </button>
      </div>

      <div className="sidebar-nav-group">
        <div
          className={
            "sidebar-nav-item right-nav-item" +
            (view === VIEW_HISTORY ? " active" : "")
          }
          data-view={VIEW_HISTORY}
          onClick={() => onNavClick(VIEW_HISTORY)}
          role="button"
        >
          <span className="sidebar-nav-icon">
            <svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor">
              <path d="M232,64a32,32,0,1,0-40,31v17a8,8,0,0,1-8,8H96a23.84,23.84,0,0,0-8,1.38V95a32,32,0,1,0-16,0v66a32,32,0,1,0,16,0V144a8,8,0,0,1,8-8h88a24,24,0,0,0,24-24V95A32.06,32.06,0,0,0,232,64ZM64,64A16,16,0,1,1,80,80,16,16,0,0,1,64,64ZM96,192a16,16,0,1,1-16-16A16,16,0,0,1,96,192Z" />
            </svg>
          </span>
          <span className="sidebar-nav-label">History</span>
        </div>
        <div
          className={
            "sidebar-nav-item right-nav-item" +
            (view === VIEW_DETAIL ? " active" : "")
          }
          data-view={VIEW_DETAIL}
          onClick={() => onNavClick(VIEW_DETAIL)}
          role="button"
        >
          <span className="sidebar-nav-icon">
            <svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor">
              <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm-8,96H188.64L159,188a8,8,0,0,1-6.95,4h-.46a8,8,0,0,1-6.89-4.84L103,89.92,79,132a8,8,0,0,1-7,4H48a8,8,0,0,1,0-16H67.36L97.05,68a8,8,0,0,1,14.3.82L153,166.08l24-42.05a8,8,0,0,1,6.95-4h24a8,8,0,0,1,0,16Z" />
            </svg>
          </span>
          <span className="sidebar-nav-label">Execution Detail</span>
        </div>
      </div>

      <div className="right-view-host">
        {/* History view: branches panel (top, conversations.js fills it)
            + history graph body (history-graph.js renders the DAG into
            `.history-body`). Both IDs/classes match the legacy template
            so existing query selectors keep working. */}
        <div id="historyPanel" className="right-view" data-view={VIEW_HISTORY}>
          <HistoryGraphPanel />
        </div>
        {/* Detail view: ui.js showDetail() writes innerHTML into
            #detailBody and textContent into #detailTitle. The template
            here pre-renders the empty-state markup that the legacy
            HTML used; the AppShell's /chat-route reset re-applies it. */}
        <div id="detailPanel" className="right-view" data-view={VIEW_DETAIL}>
          <DetailPanel />
        </div>
      </div>
    </aside>
  );
}

/**
 * History graph placeholder. The actual SVG is built by
 * `renderHistoryGraph()` in `web/public/js/shared/history-graph.js`,
 * which selects `#historyPanel .history-body` and replaces children.
 * We render the wrapper divs only; the branches panel host is
 * `#branchesPanel`, which `window.renderBranchesPanel()` in
 * `conversations.js` writes into.
 */
function HistoryGraphPanel() {
  return (
    <>
      <div id="branchesPanel"></div>
      <div className="history-body"></div>
    </>
  );
}

/**
 * Execution detail placeholder. `showDetail(node)` in `ui.js` sets
 * `#detailTitle` textContent and `#detailBody` innerHTML. The empty
 * state markup ships with the React tree and is restored by the
 * AppShell route-change handler when entering /chat with no session.
 */
function DetailPanel() {
  return (
    <div id="detailBody" className="detail-body">
      <div className="detail-empty">
        No execution selected.
        <br />
        <span>
          Click a node in the conversation tree to inspect its context and
          output.
        </span>
      </div>
    </div>
  );
}
