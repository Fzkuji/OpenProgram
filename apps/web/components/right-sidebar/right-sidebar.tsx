"use client";

/**
 * Right Sidebar — React port of apps/web/public/html/_right-sidebar.html +
 * apps/web/public/js/shared/right-dock.js (shell only).
 *
 * Mirrors the left `<Sidebar />`: this component renders the visible
 * shell (icon rail + content host with the Files / Worktrees / Detail /
 * Context view children). Some inner content is still painted by legacy
 * JS through stable ids (ui.js writes `#detailBody` / `#detailTitle`),
 * so those ids stay plain divs here.
 *
 * The session context DAG is NOT here: it is a center perspective
 * (`components/chat/dag/dag-view.tsx`), reached from the chat pane's
 * top-right view toggle. Clicking a node in that graph is what
 * populates the Detail / Context views below.
 *
 * Open / view state lives in `useSessionStore.rightDock` and is
 * persisted to `localStorage` under the same keys the legacy
 * `right-dock.js` used (`rightSidebarOpen`, `rightSidebarView`) so a
 * stale tab from before the migration restores into the same state.
 *
 * Imperative entry points for callers that don't own this component
 * (`rightDock.{show, close, toggle, restore}` from `lib/tabs/panes/right-dock.ts`)
 * are registered on mount — see `setRightDockApi` below.
 */

import { useEffect, useRef, useState } from "react";
import { SessionResourcesPanel } from "../session-resources/session-resources-panel";
import { BrowserResourceProjection } from "@/lib/browser/browser-resource-projection";
import { usePathname } from "next/navigation";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { ContextCommitTimeline } from "./context-commit-timeline";
import {
  sidebarNavIconClass,
  sidebarNavItemActiveClass,
  sidebarNavItemClass,
  sidebarNavLabelClass,
  sidebarToggleClass,
} from "../sidebar/nav-classes";
import { Sidebar as SidebarShell } from "@/components/ui/sidebar";
// Animated nav icons (pqoqubbw/icons), shared with the left sidebar.
import {
  ActivityIcon,
  BoxIcon,
  type AnimatedNavIconHandle,
  FolderOpenIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "../animated-icons";
import { FileTree } from "../files/tree/file-tree";
import { RunningPanel } from "./running/running-panel";
import { PANEL_SNAP_WIDTHS } from "./running/agent-chart-model";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { resourceSessionId } from "@/lib/chat/resources/session-resources";
import { useCurrentProject } from "@/lib/files/files-shared";
import { setRightDockApi } from "@/lib/tabs/panes/right-dock";
import { activateOnKey } from "@/lib/utils";
import { useResizableRail } from "../layout/use-resizable-rail";
import {
  DetailPanel,
  SessionViewSwitch,
  VIEW_CONTEXT,
  VIEW_DETAIL,
} from "./detail-panel";

// View IDs that round-trip through the `data-view` attribute — e.g.
// "detail" picks `<div data-view="detail">`.
const VIEW_FILES = "files";
const VIEW_RUNNING = "running";
const VIEW_RESOURCES = "resources";

export function RightSidebar() {
  const { t, text } = useTranslation();
  const open = useSessionStore((s) => s.rightDock.open);
  const storedView = useSessionStore((s) => s.rightDock.view);
  const view = storedView === "pages" ? VIEW_RESOURCES : storedView === "debugger" ? VIEW_RUNNING : storedView;
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const [activityResetSignal, setActivityResetSignal] = useState(0);
  const {
    style: railStyle,
    shellProps: railShellProps,
    resizeHandleProps,
  } = useResizableRail({
    open,
    minWidth: 240,
    // A drag rests where one, two or three columns of an Agent's time chart
    // fit whole (288, 544, 800); the first is the default.
    maxWidth: PANEL_SNAP_WIDTHS[PANEL_SNAP_WIDTHS.length - 1],
    defaultWidth: PANEL_SNAP_WIDTHS[0],
    snapWidths: PANEL_SNAP_WIDTHS,
    direction: -1,
    storageKey: "openprogram.right-sidebar-width",
    maxWindowShare: 0.5,
  });
  // Animated nav icons (pqoqubbw/icons), driven from each row's / the
  // toggle button's hover.
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
  const toggleOpenIconRef = useRef<AnimatedNavIconHandle>(null);
  const filesIconRef = useRef<AnimatedNavIconHandle>(null);
  const runningIconRef = useRef<AnimatedNavIconHandle>(null);
  const resourcesIconRef = useRef<AnimatedNavIconHandle>(null);
  // Files 视图的树 scope：当前中央 tab 的项目（文件 tab 自带
  // projectId；会话/新标签页回落到会话绑定的项目）。
  const activeTab = useCenterTabs((s) =>
    s.tabs.find((tab) => tab.id === s.activeId),
  );
  const canvasGroup = useCenterTabs(s => s.groups.find(group =>
    group.canvas && group.visibleIds.includes(s.activeId ?? "")));
  const pathname = usePathname();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const [lastCanvasSessionId, setLastCanvasSessionId] = useState<string | null>(null);
  const canvasSessionId = useCenterTabs(s => {
    if (!canvasGroup) return null;
    if (activeTab?.kind === "session") return resourceSessionId(activeTab);
    const sessions = s.tabs.filter(tab => tab.kind === "session" && !tab.draft
      && canvasGroup.visibleIds.includes(tab.id));
    return sessions.find(tab => tab.sessionId === lastCanvasSessionId)?.sessionId
      ?? sessions.find(tab => tab.sessionId === currentSessionId)?.sessionId
      ?? sessions[0]?.sessionId ?? null;
  });
  useEffect(() => {
    if (canvasGroup && activeTab?.kind === "session") {
      setLastCanvasSessionId(resourceSessionId(activeTab));
    }
  }, [canvasGroup, activeTab]);
  // Tab metadata can briefly retain the previous session while /chat resets.
  // Never query or show that session unless the visible route and chat agree.
  const activitySessionId = canvasGroup ? canvasSessionId
    : activeTab?.kind === "session"
      ? !activeTab.draft && activeTab.sessionId === currentSessionId
        && pathname === `/s/${encodeURIComponent(currentSessionId ?? "")}`
        ? activeTab.sessionId ?? null : null
      : resourceSessionId(activeTab);
  const currentProject = useCurrentProject(canvasGroup || activitySessionId
    ? { sessionId: activitySessionId, chatKey: activitySessionId }
    : undefined);
  const treeProjectId =
    activeTab?.kind === "file"
      ? (activeTab.projectId ?? null)
      : (currentProject?.id ?? null);

  // Register the imperative rightDock handle so callers that don't own
  // this component (e.g. the turn file chips' "reveal in tree") can
  // drive open/close + view switching without reaching into the React
  // store. Each method resolves the current state via
  // useSessionStore.getState() at call time so the values are always
  // fresh — no stale closure capture.
  useEffect(() => {
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
      // Kept for compatibility — AppShell still references it on the
      // fallback path.
    }

    setRightDockApi({ show, close, toggle, restore });
    return () => setRightDockApi(null);
  }, []);

  function onToggleRail() {
    setRightDockOpen(!open);
  }
  function onNavClick(v: string) {
    // History / Execution Detail nav buttons only switch view +
    // ensure the panel is open. Collapsing is the top toggle's job.
    if (v === VIEW_RUNNING && view === VIEW_RUNNING) setActivityResetSignal(value => value + 1);
    setRightDockView(v);
    if (!open) setRightDockOpen(true);
  }

  // The `data-view` attr drives which child of `.right-view-host` shows
  // (`.right-sidebar[data-view="files"] .right-view[data-view="files"]
  // { display: flex }` in right-dock/view-host.css). `.collapsed` comes
  // and goes with `open`: the views, mounted in both states at the open
  // width, fade, and the toggle and the rows ride the card's moving left
  // edge (styles/base/layout.css, "Opening and closing").
  return (
    <SidebarShell
      id="rightSidebar"
      side="right"
      // The shadcn floating shell (components/ui/sidebar.tsx), parity
      // with the left `<Sidebar />`. `.sidebar` + `.right-sidebar` +
      // `.collapsed` classes are kept for the cascade rules in
      // right-dock/view-host.css (`.right-sidebar [data-view="..."]`
      // view switching) and the rail rules in styles/base/layout.css.
      className={
        "sidebar right-sidebar relative shrink-0 rail-shell " +
        (open ? "" : "collapsed")
      }
      style={railStyle}
      {...railShellProps}
      data-view={view}
    >
      <BrowserResourceProjection sessionId={activitySessionId} />
      {/* The resize handle draws nothing and stays mounted in both
          states; closed or closing, it takes no pointer
          (use-resizable-rail.ts). */}
      <div
        {...resizeHandleProps}
        aria-label={t("right.resize_panel")}
      />
      <div className="rail-content">
      {/* Header — the same 48px row as the left sidebar's, with the
          toggle at the LEFT edge, mirroring the left sidebar's toggle at
          its right edge: 8px in when open, centred in the collapsed card,
          riding the card's moving edge in between (.rail-header in
          styles/base/layout.css). */}
      <div className="rail-header">
        <button
          className={sidebarToggleClass}
          onClick={onToggleRail}
          onMouseEnter={() => (open ? toggleIconRef : toggleOpenIconRef).current?.startAnimation?.()}
          onMouseLeave={() => (open ? toggleIconRef : toggleOpenIconRef).current?.stopAnimation?.()}
          aria-label={open ? text("Hide panel", "隐藏面板") : text("Show panel", "显示面板")}
          type="button"
        >
          {/* Mirror of the LEFT toggle — same components flipped on X,
              so the line weight is identical and the chevron points the
              opposite way. Open → ›, collapsed → ‹. Both stay mounted and
              cross-fade with the rail's move (.rail-toggle-glyphs). */}
          <span className="rail-toggle-glyphs" aria-hidden="true">
            <PanelLeftCloseIcon
              ref={toggleIconRef}
              size={20}
              className="rail-toggle-close"
              style={{ transform: "scaleX(-1)" }}
            />
            <PanelLeftOpenIcon
              ref={toggleOpenIconRef}
              size={20}
              className="rail-toggle-open"
              style={{ transform: "scaleX(-1)" }}
            />
          </span>
        </button>
      </div>

      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        {/* Detail / Context 不进图标轨：图标轨是并列的顶层入口，而这两个
            是 DAG 节点的从属面板。选中 DAG 节点时它们自己弹出，彼此之间
            靠 <SessionViewSwitch /> 切换。 */}
        <div
          className={
            sidebarNavItemClass +
            " right-nav-item rail-row" +
            (view === VIEW_FILES ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_FILES}
          onClick={() => onNavClick(VIEW_FILES)}
          onMouseEnter={() => filesIconRef.current?.startAnimation?.()}
          onMouseLeave={() => filesIconRef.current?.stopAnimation?.()}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(() => onNavClick(VIEW_FILES))}
          aria-label={text("Project files", "项目文件")}
          data-rail-tip={text("Files", "文件")}
        >
          <span className={sidebarNavIconClass}>
            <FolderOpenIcon ref={filesIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{text("Files", "文件")}</span>
        </div>
        <div
          className={
            sidebarNavItemClass +
            " right-nav-item rail-row" +
            (view === VIEW_RUNNING ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_RUNNING}
          onClick={() => onNavClick(VIEW_RUNNING)}
          onMouseEnter={() => runningIconRef.current?.startAnimation?.()}
          onMouseLeave={() => runningIconRef.current?.stopAnimation?.()}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(() => onNavClick(VIEW_RUNNING))}
          aria-label={text("Agents and programs in this conversation", "当前会话的 Agent 和程序")}
          data-rail-tip={text("Activity", "运行记录")}
        >
          <span className={sidebarNavIconClass}>
            <ActivityIcon ref={runningIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>
            {text("Activity", "运行记录")}
          </span>
        </div>

        <div role="button" tabIndex={0}
          className={sidebarNavItemClass + " right-nav-item rail-row" + (view === VIEW_RESOURCES ? " " + sidebarNavItemActiveClass : "")}
          data-view={VIEW_RESOURCES}
          data-resource-drop-session={activitySessionId || undefined}
          onClick={() => onNavClick(VIEW_RESOURCES)}
          onKeyDown={activateOnKey(() => onNavClick(VIEW_RESOURCES))}
          onMouseEnter={() => resourcesIconRef.current?.startAnimation?.()}
          onMouseLeave={() => resourcesIconRef.current?.stopAnimation?.()}
          aria-label={text("Session resources", "会话资源")}
          aria-expanded={open && view === VIEW_RESOURCES}
          aria-controls="sessionResourcesPanel">
          <span className={sidebarNavIconClass}><BoxIcon ref={resourcesIconRef} size={20} /></span>
          <span className={sidebarNavLabelClass}>{text("Resources", "资源")}</span>
        </div>
      </div>

      {/* Mounted in both states at the open width, against the card's
          right edge, which stays put; collapsed, faded out, and inert
          once the close has settled (use-resizable-rail.ts).
          What polls or streams in the views runs only while the panel is
          open (`active`). */}
      <div className="right-view-host rail-open-only rail-open-width">
        {/* Files view — the default: a plain project file tree. */}
        <div className="right-view" data-view={VIEW_FILES}>
          {treeProjectId ? (
            <FileTree projectId={treeProjectId} />
          ) : (
            <div
              style={{ padding: 16, fontSize: "var(--right-panel-meta-size, var(--fs-sm))", color: "var(--text-secondary)" }}
            >
              {text("Bind a project to browse files", "绑定项目后可浏览文件")}
            </div>
          )}
        </div>
        {/* One conversation-owned view for Agents and their managed programs. */}
        <div className="right-view" data-view={VIEW_RUNNING}>
          <RunningPanel key={activitySessionId || "no-session"} sessionId={activitySessionId} active={open && visible && view === VIEW_RUNNING} resetSignal={activityResetSignal} />
        </div>
        <div id="sessionResourcesPanel" className="right-view" data-view={VIEW_RESOURCES} data-resource-drop-session={open && view === VIEW_RESOURCES ? activitySessionId || undefined : undefined}>
          {view === VIEW_RESOURCES && <SessionResourcesPanel sessionId={activitySessionId} active={open && visible} />}
        </div>
        {/* Detail view: ui.js showDetail() writes innerHTML into
            #detailBody and textContent into #detailTitle. The template
            here pre-renders the empty-state markup that the legacy
            HTML used; the AppShell's /chat-route reset re-applies it. */}
        <div id="detailPanel" className="right-view" data-view={VIEW_DETAIL}>
          <SessionViewSwitch current={VIEW_DETAIL} />
          <DetailPanel />
        </div>
        <div id="commitsPanel" className="right-view" data-view={VIEW_CONTEXT}>
          <SessionViewSwitch current={VIEW_CONTEXT} />
          <ContextCommitTimeline />
        </div>
      </div>
      </div>
    </SidebarShell>
  );
}
