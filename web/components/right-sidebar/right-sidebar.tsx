"use client";

/**
 * Right Sidebar — React port of web/public/html/_right-sidebar.html +
 * web/public/js/shared/right-dock.js (shell only).
 *
 * Mirrors the left `<Sidebar />`: this component renders the visible
 * shell (icon rail + content host with the Files / Worktrees / Detail /
 * Context view children). Some inner content is still painted by legacy
 * JS through stable ids (ui.js writes `#detailBody` / `#detailTitle`),
 * so those ids stay plain divs here.
 *
 * The session context DAG is NOT here: it is a center perspective
 * (`components/chat/dag-view.tsx`), reached from the chat pane's
 * top-right view toggle. Clicking a node in that graph is what
 * populates the Detail / Context views below.
 *
 * Open / view state lives in `useSessionStore.rightDock` and is
 * persisted to `localStorage` under the same keys the legacy
 * `right-dock.js` used (`rightSidebarOpen`, `rightSidebarView`) so a
 * stale tab from before the migration restores into the same state.
 *
 * Imperative entry points for callers that don't own this component
 * (`rightDock.{show, close, toggle, restore}` from `lib/right-dock.ts`)
 * are registered on mount — see `setRightDockApi` below.
 */

import { useEffect, useRef } from "react";
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
// Animated nav icons (pqoqubbw/icons), shared with the left sidebar.
import {
  type AnimatedNavIconHandle,
  FolderOpenIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "../animated-icons";
import { FileTree } from "../files/file-tree";
import { renderMarkdown, useMarkdownReady } from "../chat/messages/markdown";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useCurrentProject } from "@/lib/state/files-shared";
import { setRightDockApi } from "@/lib/right-dock";
import { activateOnKey } from "@/lib/utils";
import { useResizableRail } from "../layout/use-resizable-rail";

// View IDs that round-trip through the `data-view` attribute — e.g.
// "detail" picks `<div data-view="detail">`.
const VIEW_DETAIL = "detail";
const VIEW_CONTEXT = "context";
const VIEW_FILES = "files";

export function RightSidebar() {
  const { t, text } = useTranslation();
  const open = useSessionStore((s) => s.rightDock.open);
  const view = useSessionStore((s) => s.rightDock.view);
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);
  const { style: railStyle, resizeHandleProps } = useResizableRail({
    open,
    minWidth: 240,
    maxWidth: 720,
    defaultWidth: 288,
    direction: -1,
  });
  // Animated nav icons (pqoqubbw/icons), driven from each row's / the
  // toggle button's hover.
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
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
    setRightDockView(v);
    if (!open) setRightDockOpen(true);
  }

  // The `data-view` attr drives which child of `.right-view-host` shows
  // (`.right-sidebar[data-view="files"] .right-view[data-view="files"]
  // { display: flex }` in right-dock.css). The .collapsed class drives
  // the icon-rail-only width (defined in 02-sidebar.css).
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
        (open ? "" : "collapsed")
      }
      style={railStyle}
      data-view={view}
    >
      {/* Resize handle — 6px-wide strip on the LEFT edge, drag
          inward to widen the panel, outward to shrink it. Cursor
          hint is ew-resize when hovered. Only shown when the panel
          is open; in collapsed state the icon rail handles itself. */}
      {open && (
        <div
          {...resizeHandleProps}
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
          title={t("right.resize_panel")}
        />
      )}
      {/* Header — same 48px row + 8px padding as the left sidebar
          header, but `justify-start` keeps the toggle pinned to the
          LEFT edge so it mirrors the left sidebar's toggle (which
          sits flush against the RIGHT edge there).
          TODO(sidebar-headers): still 48px while the center strip is
          40px now — the sidebar-header team owns shrinking these. */}
      <div className="flex h-[48px] shrink-0 items-center justify-start p-[8px] box-border">
        <button
          className={sidebarToggleClass}
          onClick={onToggleRail}
          onMouseEnter={() => toggleIconRef.current?.startAnimation?.()}
          onMouseLeave={() => toggleIconRef.current?.stopAnimation?.()}
          title={t("right.toggle_panel")}
          type="button"
        >
          {/* Mirror of the LEFT toggle — same components flipped on X,
              so the line weight is identical and the chevron points the
              opposite way. Open → ›, collapsed → ‹. */}
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

      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        {/* Detail / Context 不进图标轨：图标轨是并列的顶层入口，而这两个
            是 DAG 节点的从属面板。选中 DAG 节点时它们自己弹出，彼此之间
            靠 <SessionViewSwitch /> 切换。 */}
        <div
          className={
            sidebarNavItemClass + " right-nav-item" +
            (view === VIEW_FILES ? " " + sidebarNavItemActiveClass : "")
          }
          data-view={VIEW_FILES}
          onClick={() => onNavClick(VIEW_FILES)}
          onMouseEnter={() => filesIconRef.current?.startAnimation?.()}
          onMouseLeave={() => filesIconRef.current?.stopAnimation?.()}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(() => onNavClick(VIEW_FILES))}
          title={text("Project files", "项目文件")}
        >
          <span className={sidebarNavIconClass}>
            <FolderOpenIcon ref={filesIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{text("Files", "文件")}</span>
        </div>
      </div>

      <div className="right-view-host">
        {/* Files view — the default: a plain project file tree. */}
        <div className="right-view" data-view={VIEW_FILES}>
          {treeProjectId ? (
            <FileTree projectId={treeProjectId} />
          ) : (
            <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
              {text("Bind a project to browse files", "绑定项目后可浏览文件")}
            </div>
          )}
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
    </aside>
  );
}

/**
 * Entry point for the two DAG-subordinate views. Detail and Context are
 * not top-level destinations (no icon-rail row): they only mean anything
 * once a node in the center DAG perspective is selected, so the switch
 * appears once there is a selection and lets the two swap places.
 *
 * `nodeSelected` (not `detailNode`) is the gate, because both selection
 * paths set it: React callers via the store's showDetail, and the DAG's
 * `showDetail` in runtime-bridge/ui.ts, which paints #detailBody itself
 * and would otherwise leave the switch hidden.
 */
function SessionViewSwitch({ current }: { current: string }) {
  const { text } = useTranslation();
  const selected = useSessionStore((s) => s.nodeSelected);
  const setRightDockView = useSessionStore((s) => s.setRightDockView);
  if (!selected) return null;

  const options: Array<{ view: string; label: string }> = [
    { view: VIEW_DETAIL, label: text("Details", "详情") },
    { view: VIEW_CONTEXT, label: text("Context", "上下文") },
  ];
  return (
    <div
      className="session-view-switch"
      role="tablist"
      aria-label={text("Selected node views", "选中节点的视图")}
    >
      {options.map((option) => (
        <button
          key={option.view}
          type="button"
          role="tab"
          aria-selected={current === option.view}
          className={
            "session-view-switch-btn" +
            (current === option.view ? " is-active" : "")
          }
          onClick={() => setRightDockView(option.view)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
/**
 * One labelled block in the Details view.
 *
 * Value rendering, in order:
 *   * non-string (params, attempts) → pretty JSON in the code block;
 *   * a string that parses as a JSON object/array → re-indented JSON,
 *     so a tool result that arrived as one long `{"a":1,...}` line is
 *     readable instead of a wrapped wall;
 *   * a string that reads as Markdown (fences / headings / lists /
 *     tables / links) → the *chat* markdown renderer, so LLM replies
 *     look the same here as in the bubble;
 *   * anything else → plain preformatted text.
 *
 * `.detail-code` already scrolls at max-height 300px, so long values
 * stay contained without new CSS.
 */
function looksLikeMarkdown(s: string): boolean {
  return /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|)|```|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*/.test(s);
}

function DetailBlock({
  title,
  value,
  danger,
}: {
  title: string;
  value: unknown;
  danger?: boolean;
}) {
  // Subscribe to the markdown-ready gate so a block painted before the
  // shared `renderMd` global lands re-renders once it does.
  useMarkdownReady();
  if (value === undefined || value === null) return null;

  let body: React.ReactNode;
  if (typeof value !== "string") {
    body = <div className="detail-code">{JSON.stringify(value, null, 2)}</div>;
  } else {
    const trimmed = value.trim();
    let pretty: string | null = null;
    if (/^[[{]/.test(trimmed)) {
      try {
        pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        pretty = null;
      }
    }
    if (pretty !== null) {
      body = <div className="detail-code">{pretty}</div>;
    } else if (!danger && looksLikeMarkdown(value)) {
      body = (
        <div
          className="detail-code chat-text"
          style={{ whiteSpace: "normal", fontFamily: "inherit" }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(value) }}
        />
      );
    } else {
      body = (
        <div
          className="detail-code"
          style={danger ? { color: "var(--accent-red)" } : undefined}
        >
          {value}
        </div>
      );
    }
  }

  return (
    <div className="detail-section">
      <div className="detail-section-title">{title}</div>
      {body}
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

      <DetailBlock title="Prompt / Docstring" value={node.prompt || null} />

      <DetailBlock
        title="Parameters"
        value={
          filteredParams && Object.keys(filteredParams).length > 0
            ? filteredParams
            : null
        }
      />

      <DetailBlock title="Output" value={node.output ?? null} />

      <DetailBlock title="Error" value={node.error || null} danger />

      {node.node_type === "exec" ? (
        <>
          <DetailBlock
            title="LLM Input"
            value={node.params?._content != null ? String(node.params._content) : null}
          />
          <DetailBlock title="LLM Reply" value={node.raw_reply ?? null} />
        </>
      ) : (
        <DetailBlock title="Raw LLM Reply" value={node.raw_reply ?? null} />
      )}

      <DetailBlock
        title={`Attempts (${node.attempts?.length || 0})`}
        value={node.attempts && node.attempts.length > 0 ? node.attempts : null}
      />

      <div className="detail-section">
        <div className="detail-section-title">Render / Compress</div>
        <div className="detail-field-value">
          render: {node.render || "summary"} | compress: {node.compress ? "true" : "false"}
        </div>
      </div>
    </div>
  );
}
