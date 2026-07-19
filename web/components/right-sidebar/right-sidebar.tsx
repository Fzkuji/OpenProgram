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
import { Bookmark, X } from "lucide-react";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import {
  RIGHT_PANEL_DEFAULT,
  RIGHT_PANEL_GAP,
  RIGHT_PANEL_MAX,
  RIGHT_PANEL_MIN,
  RIGHT_RAIL_WIDTH,
  clampPanelWidth,
  fitRightPanelWidth,
  handlePanelResizeKey,
  resetPanelResize,
  resolveRightPanelAction,
} from "@/lib/right-panel-behavior";
import { BranchesPanel } from "./branches";
import { ContextCommitTimeline } from "./context-commit-timeline";
import { WorktreesPanel } from "./worktrees";
import { BookmarksPanel } from "./bookmarks-panel";
import {
  sidebarNavIconClass,
  sidebarNavItemActiveClass,
  sidebarNavItemClass,
  sidebarToggleClass,
} from "../sidebar/nav-classes";
// Animated nav icons (pqoqubbw/icons), shared with the left sidebar.
import {
  type AnimatedNavIconHandle,
  FolderOpenIcon,
  GitGraphIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "../animated-icons";
import { FileTree } from "../files/file-tree";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useCurrentProject } from "@/lib/state/files-shared";

// View IDs that round-trip through the `data-view` attribute. Matches
// the legacy template exactly: "history" picks `<div data-view="history">`,
// "detail" picks `<div data-view="detail">`.
const VIEW_HISTORY = "history";
const VIEW_DETAIL = "detail";
const VIEW_CONTEXT = "context";
const VIEW_FILES = "files";
const VIEW_BOOKMARKS = "bookmarks";

type RightSidebarProps = {
  viewportWidth: number | null;
  narrow: boolean;
};

export function RightSidebar({ viewportWidth, narrow }: RightSidebarProps) {
  const { t, text } = useTranslation();
  const open = useSessionStore((s) => s.rightDock.open);
  const view = useSessionStore((s) => s.rightDock.view);
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);
  const [panelWidth, setPanelWidth] = useState(RIGHT_PANEL_DEFAULT);
  const [resizing, setResizing] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const toggleButtonRef = useRef<HTMLButtonElement>(null);
  const railButtonRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Animated nav icons (pqoqubbw/icons), driven from each row's / the
  // toggle button's hover.
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
  const historyIconRef = useRef<AnimatedNavIconHandle>(null);
  const filesIconRef = useRef<AnimatedNavIconHandle>(null);
  // Files 视图的树 scope：当前中央 tab 的项目（文件 tab 自带
  // projectId；会话/新标签页回落到会话绑定的项目）。
  const activeTab = useCenterTabs((s) =>
    s.tabs.find((tab) => tab.id === s.activeId),
  );
  const currentProject = useCurrentProject();
  const treeProjectId =
    activeTab?.kind === "file"
      ? (activeTab.projectId ?? null)
      : (currentProject?.id ?? null);
  const effectivePanelWidth =
    narrow && viewportWidth !== null
      ? fitRightPanelWidth(panelWidth, viewportWidth)
      : panelWidth;
  const effectivePanelMaximum =
    narrow && viewportWidth !== null
      ? fitRightPanelWidth(RIGHT_PANEL_MAX, viewportWidth)
      : RIGHT_PANEL_MAX;
  const effectivePanelMinimum = Math.min(
    RIGHT_PANEL_MIN,
    effectivePanelMaximum,
  );

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

  function restoreRailFocus(targetView = view) {
    const target = railButtonRefs.current[targetView] ?? toggleButtonRef.current;
    requestAnimationFrame(() => target?.focus());
  }

  function closePanelAndRestoreFocus(targetView = view) {
    setRightDockOpen(false);
    restoreRailFocus(targetView);
  }

  function onToggleRail() {
    if (open) closePanelAndRestoreFocus();
    else setRightDockOpen(true);
  }

  function onNavClick(v: string, button: HTMLButtonElement) {
    const next = resolveRightPanelAction(
      { open, view },
      { type: "select", view: v },
    );
    if (next.view !== view) setRightDockView(next.view);
    setRightDockOpen(next.open);
    if (next.focusView) requestAnimationFrame(() => button.focus());
  }

  function onSidebarKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    event.stopPropagation();
    const next = resolveRightPanelAction({ open, view }, { type: "escape" });
    setRightDockOpen(next.open);
    if (next.focusView) restoreRailFocus(next.focusView);
  }

  function onResizePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { startX: event.clientX, startW: effectivePanelWidth };
    setResizing(true);
  }

  function onResizePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (
      !dragRef.current ||
      !event.currentTarget.hasPointerCapture(event.pointerId)
    ) {
      return;
    }
    setPanelWidth(
      clampPanelWidth(
        dragRef.current.startW + dragRef.current.startX - event.clientX,
      ),
    );
  }

  function resetResize() {
    resetPanelResize(dragRef, setResizing);
  }

  function finishResize(event: React.PointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    resetResize();
  }

  const panelTitle =
    view === VIEW_HISTORY
      ? t("right.history")
      : view === VIEW_BOOKMARKS
        ? text("Bookmarks", "书签")
        : view === VIEW_FILES
          ? text("Files", "文件")
          : view === VIEW_CONTEXT
            ? t("right.context")
            : text("Execution detail", "执行详情");

  const shellWidth = open
    ? effectivePanelWidth + RIGHT_RAIL_WIDTH + RIGHT_PANEL_GAP * 2
    : RIGHT_RAIL_WIDTH;

  return (
    <aside
      id="rightSidebar"
      className={`sidebar right-sidebar${open ? "" : " collapsed"}`}
      style={{ width: `${shellWidth}px`, minWidth: `${shellWidth}px` }}
      data-view={view}
      data-resizing={resizing ? "true" : "false"}
      onKeyDown={onSidebarKeyDown}
    >
      <section
        className="right-sidebar-panel"
        style={{ width: `${effectivePanelWidth}px` }}
        role="region"
        aria-labelledby="right-panel-title"
        hidden={!open}
      >
        <div
          className="right-panel-resize"
          role="separator"
          aria-label={t("right.resize_panel")}
          aria-orientation="vertical"
          aria-valuemin={effectivePanelMinimum}
          aria-valuemax={effectivePanelMaximum}
          aria-valuenow={effectivePanelWidth}
          tabIndex={0}
          onPointerDown={onResizePointerDown}
          onPointerMove={onResizePointerMove}
          onPointerUp={finishResize}
          onPointerCancel={finishResize}
          onLostPointerCapture={resetResize}
          onKeyDown={(event) =>
            handlePanelResizeKey(event, effectivePanelWidth, setPanelWidth)
          }
        />
        <header className="right-sidebar-panel-header">
          <span id="right-panel-title">{panelTitle}</span>
          <button
            type="button"
            className={sidebarToggleClass}
            onClick={() => closePanelAndRestoreFocus()}
            title={text("Close panel", "关闭面板")}
            aria-label={text("Close panel", "关闭面板")}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="right-view-host">
          <div className="right-view" data-view={VIEW_BOOKMARKS}>
            <BookmarksPanel />
          </div>
          <div className="right-view" data-view={VIEW_FILES}>
            {treeProjectId ? (
              <FileTree projectId={treeProjectId} />
            ) : (
              <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
                {text("Bind a project to browse files", "绑定项目后可浏览文件")}
              </div>
            )}
          </div>
          <div id="historyPanel" className="right-view" data-view={VIEW_HISTORY}>
            <HistoryGraphPanel />
          </div>
          <div id="detailPanel" className="right-view" data-view={VIEW_DETAIL}>
            <DetailPanel />
          </div>
          <div id="commitsPanel" className="right-view" data-view={VIEW_CONTEXT}>
            <ContextCommitTimeline />
          </div>
        </div>
      </section>

      <nav
        className="right-sidebar-rail"
        aria-label={text("Workspace tools", "工作区工具")}
      >
        <div className="right-sidebar-rail-header">
          <button
            ref={toggleButtonRef}
            className={sidebarToggleClass}
            onClick={onToggleRail}
            onMouseEnter={() => toggleIconRef.current?.startAnimation?.()}
            onMouseLeave={() => toggleIconRef.current?.stopAnimation?.()}
            title={t("right.toggle_panel")}
            aria-label={t("right.toggle_panel")}
            aria-expanded={open}
            type="button"
          >
            {open ? (
              <PanelLeftCloseIcon
                ref={toggleIconRef}
                size={20}
                style={{ transform: "scaleX(-1)" }}
              />
            ) : (
              <PanelLeftOpenIcon
                ref={toggleIconRef}
                size={20}
                style={{ transform: "scaleX(-1)" }}
              />
            )}
          </button>
        </div>

        <div className="right-sidebar-rail-items">
          <button
            ref={(node) => {
              railButtonRefs.current[VIEW_HISTORY] = node;
            }}
            type="button"
            className={
              sidebarNavItemClass +
              " right-nav-item" +
              (view === VIEW_HISTORY ? " " + sidebarNavItemActiveClass : "")
            }
            data-view={VIEW_HISTORY}
            onClick={(event) => onNavClick(VIEW_HISTORY, event.currentTarget)}
            onMouseEnter={() => historyIconRef.current?.startAnimation?.()}
            onMouseLeave={() => historyIconRef.current?.stopAnimation?.()}
            title={t("right.history")}
            aria-label={t("right.history")}
            aria-pressed={open && view === VIEW_HISTORY}
          >
            <span className={sidebarNavIconClass}>
              <GitGraphIcon ref={historyIconRef} size={20} />
            </span>
          </button>

          <button
            ref={(node) => {
              railButtonRefs.current[VIEW_BOOKMARKS] = node;
            }}
            type="button"
            className={
              sidebarNavItemClass +
              " right-nav-item" +
              (view === VIEW_BOOKMARKS ? " " + sidebarNavItemActiveClass : "")
            }
            data-view={VIEW_BOOKMARKS}
            onClick={(event) => onNavClick(VIEW_BOOKMARKS, event.currentTarget)}
            title={text("Bookmarks", "书签")}
            aria-label={text("Bookmarks", "书签")}
            aria-pressed={open && view === VIEW_BOOKMARKS}
          >
            <span className={sidebarNavIconClass}>
              <Bookmark size={20} aria-hidden="true" />
            </span>
          </button>

          <button
            ref={(node) => {
              railButtonRefs.current[VIEW_FILES] = node;
            }}
            type="button"
            className={
              sidebarNavItemClass +
              " right-nav-item" +
              (view === VIEW_FILES ? " " + sidebarNavItemActiveClass : "")
            }
            data-view={VIEW_FILES}
            onClick={(event) => onNavClick(VIEW_FILES, event.currentTarget)}
            onMouseEnter={() => filesIconRef.current?.startAnimation?.()}
            onMouseLeave={() => filesIconRef.current?.stopAnimation?.()}
            title={text("Project files", "项目文件")}
            aria-label={text("Project files", "项目文件")}
            aria-pressed={open && view === VIEW_FILES}
          >
            <span className={sidebarNavIconClass}>
              <FolderOpenIcon ref={filesIconRef} size={20} />
            </span>
          </button>
        </div>
      </nav>
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
  // 分支入口只保留下方的 BranchesPanel（带 HEAD 标记的列表）。原顶栏搬来
  // 的 BranchBadge chip 与它信息重复，已删。
  return (
    <>
      <BranchesPanel />
      <WorktreesPanel />
      <HighlightModeToggle />
      <div className="history-body"></div>
    </>
  );
}


/** Toggle: white-fill on DAG nodes follows the chat scroll
 *  position (viewport) or the next-LLM-call context range
 *  (context). Drives ``window.setHistoryHighlightMode``. */
function HighlightModeToggle() {
  const { t } = useTranslation();
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
        title={t("right.viewport_tooltip")}
      >
        {t("right.viewport")}
      </button>
      <button
        type="button"
        onClick={() => pick("context")}
        style={style(mode === "context")}
        title={t("right.context_highlight_tooltip")}
      >
        {t("right.context")}
      </button>
    </div>
  );
}

function DetailPanel() {
  const { t } = useTranslation();
  const node = useSessionStore((s) => s.detailNode);

  if (!node) {
    return (
      <div id="detailBody" className="detail-body">
        <div className="detail-empty">
          {t("right.no_execution")}
          <br />
          <span>{t("right.no_execution_hint")}</span>
        </div>
      </div>
    );
  }

  const statusIcon = node.status === "success" ? "✓" : node.status === "error" ? "✗" : "●";
  const dur = node.duration_ms && node.duration_ms > 0 ? `${Math.round(node.duration_ms)}ms` : "running...";

  const filteredParams = node.params
    ? Object.fromEntries(Object.entries(node.params).filter(([k]) => k !== "runtime" && k !== "callback"))
    : null;

  return (
    <div id="detailBody" className="detail-body">
      <div className="detail-section">
        <div className="detail-section-title">Status</div>
        <div className={`detail-badge ${node.status}`}>
          {statusIcon} {node.status} &middot; {dur}
        </div>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">Path</div>
        <div className="detail-field-value">{node.path}</div>
      </div>

      {node.prompt ? (
        <div className="detail-section">
          <div className="detail-section-title">Prompt / Docstring</div>
          <div className="detail-code">{node.prompt}</div>
        </div>
      ) : null}

      {filteredParams && Object.keys(filteredParams).length > 0 ? (
        <div className="detail-section">
          <div className="detail-section-title">Parameters</div>
          <div className="detail-code">{JSON.stringify(filteredParams, null, 2)}</div>
        </div>
      ) : null}

      {node.output != null ? (
        <div className="detail-section">
          <div className="detail-section-title">Output</div>
          <div className="detail-code">
            {typeof node.output === "string" ? node.output : JSON.stringify(node.output, null, 2)}
          </div>
        </div>
      ) : null}

      {node.error ? (
        <div className="detail-section">
          <div className="detail-section-title">Error</div>
          <div className="detail-code" style={{ color: "var(--accent-red)" }}>{node.error}</div>
        </div>
      ) : null}

      {node.node_type === "exec" ? (
        <>
          {node.params?._content ? (
            <div className="detail-section">
              <div className="detail-section-title">LLM Input</div>
              <div className="detail-code">{"→ "}{String(node.params._content)}</div>
            </div>
          ) : null}
          {node.raw_reply != null ? (
            <div className="detail-section">
              <div className="detail-section-title">LLM Reply</div>
              <div className="detail-code">{"← "}{node.raw_reply}</div>
            </div>
          ) : null}
        </>
      ) : node.raw_reply != null ? (
        <div className="detail-section">
          <div className="detail-section-title">Raw LLM Reply</div>
          <div className="detail-code">{node.raw_reply}</div>
        </div>
      ) : null}

      {node.attempts && node.attempts.length > 0 ? (
        <div className="detail-section">
          <div className="detail-section-title">Attempts ({node.attempts.length})</div>
          <div className="detail-code">{JSON.stringify(node.attempts, null, 2)}</div>
        </div>
      ) : null}

      <div className="detail-section">
        <div className="detail-section-title">Render / Compress</div>
        <div className="detail-field-value">
          render: {node.render || "summary"} | compress: {node.compress ? "true" : "false"}
        </div>
      </div>
    </div>
  );
}
