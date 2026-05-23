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

import { useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/lib/session-store";
import { BranchesPanel } from "./branches-panel";
import { SnapshotTimeline } from "./snapshot-timeline";
import {
  sidebarNavIconClass,
  sidebarNavIconSvgClass,
  sidebarNavItemActiveClass,
  sidebarNavItemClass,
  sidebarNavLabelClass,
  sidebarToggleClass,
} from "../sidebar/nav-classes";

// View IDs that round-trip through the `data-view` attribute. Matches
// the legacy template exactly: "history" picks `<div data-view="history">`,
// "detail" picks `<div data-view="detail">`.
const VIEW_HISTORY = "history";
const VIEW_DETAIL = "detail";
const VIEW_SNAPSHOTS = "snapshots";

// Right sidebar width is independent of the left one — the left
// uses --sidebar-width (288px default), the right persists its own
// value in localStorage so users can widen the History DAG panel
// without dragging the left sidebar along.
const RIGHT_W_KEY = "rightSidebar.width";
const RIGHT_W_MIN = 240;
const RIGHT_W_MAX = 720;
const RIGHT_W_DEFAULT = 288;

function _readStoredWidth(): number {
  if (typeof window === "undefined") return RIGHT_W_DEFAULT;
  try {
    const v = parseInt(localStorage.getItem(RIGHT_W_KEY) || "", 10);
    if (Number.isFinite(v) && v >= RIGHT_W_MIN && v <= RIGHT_W_MAX) return v;
  } catch { /* ignore */ }
  return RIGHT_W_DEFAULT;
}

export function RightSidebar() {
  const open = useSessionStore((s) => s.rightDock.open);
  const view = useSessionStore((s) => s.rightDock.view);
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);
  const [width, setWidth] = useState<number>(RIGHT_W_DEFAULT);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  // Hydrate from localStorage on mount (avoids SSR mismatch).
  useEffect(() => {
    setWidth(_readStoredWidth());
  }, []);

  const onResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: width };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      // Right sidebar grows when dragging LEFT (toward the chat), so
      // delta is startX - clientX.
      const delta = dragRef.current.startX - ev.clientX;
      const next = Math.max(
        RIGHT_W_MIN,
        Math.min(RIGHT_W_MAX, dragRef.current.startW + delta),
      );
      setWidth(next);
    };
    const onUp = () => {
      dragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      try { localStorage.setItem(RIGHT_W_KEY, String(width)); } catch { /* */ }
      // Re-read so the persisted value reflects the final width
      // (state above is captured at handler binding time).
      try {
        const cur = document.getElementById("rightSidebar")?.clientWidth;
        if (cur && cur >= RIGHT_W_MIN && cur <= RIGHT_W_MAX) {
          localStorage.setItem(RIGHT_W_KEY, String(cur));
        }
      } catch { /* */ }
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

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
    // History / Execution Detail nav buttons only switch view +
    // ensure the panel is open. Collapsing is the top toggle's job.
    setRightDockView(v);
    if (!open) setRightDockOpen(true);
  }

  // `data-view` attr is preserved so the legacy CSS rules in
  // 09-right-dock.css (`.right-sidebar[data-view="history"]
  // .right-view[data-view="history"] { display: flex }`) keep working
  // unchanged. The .collapsed class drives the icon-rail-only width
  // (defined in 02-sidebar.css).
  return (
    <aside
      id="rightSidebar"
      // Shell layout via Tailwind (parity with the left `<Sidebar />`).
      // `border-l` instead of `border-r` is the only directional diff.
      // `.sidebar` + `.right-sidebar` + `.collapsed` classes are kept
      // for the cascade rules in 09-right-dock.css (`.right-sidebar
      // [data-view="..."]` view switching, `.right-sidebar.collapsed
      // .right-view-host { display: none }`) and the small
      // `.sidebar.collapsed *` override in 02-sidebar.css.
      className={
        "sidebar right-sidebar relative flex shrink-0 flex-col overflow-hidden " +
        "bg-bg-secondary border-l border-[var(--border)] " +
        // Skip the width transition while dragging so the handle
        // feels responsive; the only transition we still want is the
        // open/close collapse animation.
        (dragRef.current ? "" : "[transition:width_0.3s_ease,min-width_0.3s_ease] ") +
        (open ? "" : "collapsed")
      }
      style={open
        ? { width: `${width}px`, minWidth: `${RIGHT_W_MIN}px` }
        : { width: "48px", minWidth: "48px" }}
      data-view={view}
    >
      {/* Resize handle — 6px-wide strip on the LEFT edge, drag
          inward to widen the panel, outward to shrink it. Cursor
          hint is ew-resize when hovered. Only shown when the panel
          is open; in collapsed state the icon rail handles itself. */}
      {open && (
        <div
          onMouseDown={onResizeMouseDown}
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: "6px",
            cursor: "ew-resize",
            zIndex: 10,
            background: "transparent",
          }}
          title="Drag to resize panel"
        />
      )}
      {/* Header — same 48px row + 8px padding as the left sidebar
          header, but `justify-start` keeps the toggle pinned to the
          LEFT edge so it mirrors the left sidebar's toggle (which
          sits flush against the RIGHT edge there). */}
      <div className="flex h-[48px] shrink-0 items-center justify-start p-[8px] box-border">
        <button
          className={sidebarToggleClass}
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

      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        <div
          className={
            sidebarNavItemClass + " right-nav-item" +
            (view === VIEW_HISTORY ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_HISTORY}
          onClick={() => onNavClick(VIEW_HISTORY)}
          role="button"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 256 256" fill="currentColor">
              <path d="M232,64a32,32,0,1,0-40,31v17a8,8,0,0,1-8,8H96a23.84,23.84,0,0,0-8,1.38V95a32,32,0,1,0-16,0v66a32,32,0,1,0,16,0V144a8,8,0,0,1,8-8h88a24,24,0,0,0,24-24V95A32.06,32.06,0,0,0,232,64ZM64,64A16,16,0,1,1,80,80,16,16,0,0,1,64,64ZM96,192a16,16,0,1,1-16-16A16,16,0,0,1,96,192Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>History</span>
        </div>
        <div
          className={
            sidebarNavItemClass + " right-nav-item" +
            (view === VIEW_DETAIL ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_DETAIL}
          onClick={() => onNavClick(VIEW_DETAIL)}
          role="button"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 256 256" fill="currentColor">
              <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm-8,96H188.64L159,188a8,8,0,0,1-6.95,4h-.46a8,8,0,0,1-6.89-4.84L103,89.92,79,132a8,8,0,0,1-7,4H48a8,8,0,0,1,0-16H67.36L97.05,68a8,8,0,0,1,14.3.82L153,166.08l24-42.05a8,8,0,0,1,6.95-4h24a8,8,0,0,1,0,16Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Execution Detail</span>
        </div>
        <div
          className={
            sidebarNavItemClass + " right-nav-item" +
            (view === VIEW_SNAPSHOTS ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_SNAPSHOTS}
          onClick={() => onNavClick(VIEW_SNAPSHOTS)}
          role="button"
          title="Context snapshots"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 256 256" fill="currentColor">
              <path d="M208,32H184V24a8,8,0,0,0-16,0v8H88V24a8,8,0,0,0-16,0v8H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,48v8a8,8,0,0,0,16,0V48h80v8a8,8,0,0,0,16,0V48h24V80H48V48ZM208,208H48V96H208V208Zm-68-76a12,12,0,1,1-12-12A12,12,0,0,1,140,132Zm44,0a12,12,0,1,1-12-12A12,12,0,0,1,184,132ZM96,172a12,12,0,1,1-12-12A12,12,0,0,1,96,172Zm44,0a12,12,0,1,1-12-12A12,12,0,0,1,140,172Zm44,0a12,12,0,1,1-12-12A12,12,0,0,1,184,172Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Snapshots</span>
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
        <div id="snapshotsPanel" className="right-view" data-view={VIEW_SNAPSHOTS}>
          <SnapshotTimeline />
        </div>
      </div>
    </aside>
  );
}

/**
 * History view: the React <BranchesPanel /> on top, the history-graph
 * DAG body below. The SVG is still built by `renderHistoryGraph()` in
 * `web/public/js/shared/history-graph.js`, which selects
 * `#historyPanel .history-body` and replaces its children.
 */
function HistoryGraphPanel() {
  return (
    <>
      <BranchesPanel />
      <HighlightModeToggle />
      <div className="history-body"></div>
    </>
  );
}

/** Toggle: white-fill on DAG nodes follows the chat scroll
 *  position (viewport) or the next-LLM-call context range
 *  (context). Drives ``window.setHistoryHighlightMode``. */
function HighlightModeToggle() {
  const [mode, setMode] = useState<"viewport" | "context">("viewport");
  function pick(next: "viewport" | "context") {
    setMode(next);
    const w = window as unknown as {
      setHistoryHighlightMode?: (m: string) => void;
    };
    w.setHistoryHighlightMode?.(next);
  }
  const style = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "4px 8px",
    fontSize: 12,
    fontFamily: "inherit",
    border: "1px solid var(--border)",
    background: active ? "var(--bg-hover)" : "transparent",
    color: active ? "var(--text-bright)" : "var(--text-muted)",
    cursor: "pointer",
    borderRadius: 6,
    transition: "background 0.15s, color 0.15s",
  });
  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        padding: "6px 8px",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <button
        type="button"
        onClick={() => pick("viewport")}
        style={style(mode === "viewport")}
        title="High light follows chat scroll position"
      >
        Viewport
      </button>
      <button
        type="button"
        onClick={() => pick("context")}
        style={style(mode === "context")}
        title="Highlight the message set the next LLM turn will load"
      >
        Context
      </button>
    </div>
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
