"use client";

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { ExternalLink, X } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useCanvasOverview } from "@/lib/tabs/canvas/canvas-overview";
import {
  APPLICATION_TARGET,
  appPreviewTarget,
  followCurrentBranch,
  getPreviewPreference,
  hideResourcePreview,
  NATIVE_WINDOW_TARGET,
  selectResourcePreview,
  TERMINAL_TARGET,
  useBrowserResourceStore,
  viewedBranchFor,
} from "@/lib/chat/resources/session-resources";
import { nativeWindowFrameUrl, useNativeWindows } from "@/lib/browser/control/native-window-store";
import { useUsedApplications } from "@/lib/chat/resources/used-applications";
import { useTerminalResources } from "@/lib/desktop/terminal-resources";
import { embedAvailable } from "@/lib/desktop/embedded-window/embedded-window";
import { PIP_RESIZE_DIRS, useWebTabPip, type PipResizeDir, type WebTabPipRect } from "@/lib/browser/web-tab/web-tab-pip-store";
import { TerminalMirror } from "@/components/session-resources/terminal-resources";
import { ApplicationTabPane } from "@/components/center-tabs/pages/application-tab-pane";
import { LiveWindowView, type LiveWindowProblem } from "@/components/center-tabs/window/live-window-view";

import styles from "../browser/browser.module.css";
import { PIP_ICON, PIP_ICON_BUTTON, PIP_STAGE, PipFrame, PipMoreMenu, usePipGestures } from "./web-tab-pip";

/** The chat preview of what the Agent uses besides web pages: another
 *  application's window, a terminal, or a built-in application. Same frame,
 *  place, gestures and header controls as the web page preview
 *  (web-tab-pip.tsx); only the inside differs. An app window shows its live
 *  picture and takes input where the platform allows (the screenshot the
 *  Agent last took where it cannot be seen live), a terminal takes typing,
 *  an application runs as it does in its tab. "Open in tab" shows the same
 *  thing in a tab of its own. */
export function AppPreviewPip() {
  const { text } = useTranslation();
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const groups = useCenterTabs(s => s.groups);
  const docked = useWebTabPip(s => s.docked);
  const dockSlot = useWebTabPip(s => s.dockSlot);
  const windows = useNativeWindows(s => s.windows);
  const applications = useUsedApplications(s => s.applications);
  const terminals = useTerminalResources(s => s.rows);
  const overview = useCanvasOverview(s => s.mode !== null);
  useBrowserResourceStore(s => s.followEpoch);
  useBrowserResourceStore(s => s.preferences);
  useBrowserResourceStore(s => s.viewedBranch);
  const rootRef = useRef<HTMLDivElement>(null);
  const presentedRef = useRef<WebTabPipRect | null>(null);
  const [problem, setProblem] = useState<{ target: string; problem: LiveWindowProblem } | null>(null);
  const [dockSlotShown, setDockSlotShown] = useState(false);
  useLayoutEffect(() => {
    if (!dockSlot) { setDockSlotShown(false); return; }
    const check = () => {
      const slot = dockSlot.getBoundingClientRect();
      setDockSlotShown(slot.width > 0 || slot.height > 0);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(dockSlot);
    return () => ro.disconnect();
  }, [dockSlot]);

  const owner = tabs.find(tab => tab.id === activeId && tab.kind === "session");
  const sessionId = owner?.kind === "session" ? owner.sessionId || null : null;
  const branchId = sessionId ? viewedBranchFor(sessionId) : null;
  const pref = sessionId ? getPreviewPreference(sessionId, branchId) : null;
  const target = sessionId ? appPreviewTarget(sessionId, branchId) : null;
  const operated = sessionId ? windows[sessionId] : undefined;
  const appWindow = target?.startsWith(NATIVE_WINDOW_TARGET)
    && operated && `${NATIVE_WINDOW_TARGET}${operated.windowId}` === target ? operated : null;
  const terminal = target?.startsWith(TERMINAL_TARGET)
    ? terminals[target.slice(TERMINAL_TARGET.length)] : undefined;
  const shownTerminal = terminal && sessionId && terminal.session_ids.includes(sessionId)
    && terminal.status === "running" ? terminal : null;
  const used = sessionId ? applications[sessionId] : undefined;
  const application = target?.startsWith(APPLICATION_TARGET)
    && used && `${APPLICATION_TARGET}${used.instanceId}` === target ? used : null;
  const shown = !!(appWindow || shownTerminal || application);
  const live = shown && !overview;
  const expanded = !!pref?.expanded;
  const dockedHere = live && docked && !expanded && !!dockSlot && dockSlotShown;
  const paneSet = groups.map((group) => group.visibleIds.join(",")).join("|");
  const subject = target ?? "";
  const { dragRef, onDragPointerDown, onDragPointerMove, onDragPointerUp, onDragPointerCancel } = usePipGestures({
    rootRef, presentedRef, live, expanded, dockedHere, subject,
  });
  const onProblem = useCallback((next: LiveWindowProblem) => setProblem({ target: subject, problem: next }), [subject]);

  if (!sessionId || !owner || !live || !target) return null;
  const title = appWindow ? appWindow.appName
    : application ? application.title || text("Application", "应用")
      : shownTerminal?.preset === "claude" ? "Claude Code" : text("Terminal", "终端");
  const kindLabel = appWindow ? text("Desktop app", "桌面应用")
    : application ? text("Application", "应用") : text("Terminal", "终端");
  const openLabel = text("Open in tab", "在标签页打开");
  const openHint = appWindow ? text("Show this window in a tab of its own", "在单独的标签页里显示这个窗口")
    : application ? text("Show this application in a tab of its own", "在单独的标签页里显示这个应用")
      : text("Show this terminal in a tab of its own", "在单独的标签页里显示这个终端");
  const hideLabel = text("Close preview", "关闭小窗");
  const hideHint = text("Hides the preview; the agent keeps working", "只隐藏小窗；Agent 继续工作");
  const resizeLabels: Record<PipResizeDir, string> = {
    n: text("Resize from top", "从顶部调整大小"),
    ne: text("Resize from top right", "从右上角调整大小"),
    e: text("Resize from right", "从右侧调整大小"),
    se: text("Resize from bottom right", "从右下角调整大小"),
    s: text("Resize from bottom", "从底部调整大小"),
    sw: text("Resize from bottom left", "从左下角调整大小"),
    w: text("Resize from left", "从左侧调整大小"),
    nw: text("Resize from top left", "从左上角调整大小"),
  };
  const pinned = pref?.mode === "manual";
  const toggleFollow = () => {
    if (pinned) followCurrentBranch(sessionId, branchId);
    else selectResourcePreview(sessionId, branchId, target);
  };
  const openInTab = () => {
    const store = useCenterTabs.getState();
    if (appWindow) store.openWindowTabFor({ id: appWindow.windowId, app: appWindow.appName, title: appWindow.appName });
    else if (application) store.openApplicationTab(application.applicationId, application.instanceId, title, sessionId);
    else if (shownTerminal) store.openTerminalResourceTab(shownTerminal.terminal_id, title, sessionId);
  };
  const windowProblem = problem && problem.target === target ? problem.problem : null;
  const frame = appWindow ? nativeWindowFrameUrl(appWindow) : null;

  let body: React.ReactNode;
  if (shownTerminal) {
    body = <TerminalMirror key={`${shownTerminal.terminal_id}:${shownTerminal.generation}`} resource={shownTerminal} interactive />;
  } else if (application) {
    body = <div className="absolute inset-0"><ApplicationTabPane key={application.instanceId} instanceId={application.instanceId} /></div>;
  } else if (appWindow && embedAvailable() && !windowProblem) {
    body = <LiveWindowView key={appWindow.windowId} windowId={appWindow.windowId} app={appWindow.appName}
      place={false} className="absolute inset-0" onProblem={onProblem} />;
  } else if (windowProblem?.kind === "closed") {
    body = <div className={cn(styles.webPipFallback, "text-fs-2xs text-text-muted")}>
      {text("This window was closed.", "这个窗口已经关闭了。")}
    </div>;
  } else if (frame) {
    // Not in the desktop App, or the window cannot be seen live: the latest
    // screenshot the Agent took of it.
    body = <img className={styles.webPipShot} style={{ display: "block" }} src={frame} alt="" />;
  } else {
    // Until the first screenshot arrives the frame stays empty.
    body = <div className={cn(styles.webPipFallback, "text-fs-2xs text-text-muted")} />;
  }

  return (
    <PipFrame
      frameRef={rootRef}
      dragRef={dragRef}
      presentedRef={presentedRef}
      live={live}
      expanded={expanded}
      tabId={target}
      ownerTabId={owner.id}
      paneSet={paneSet}
      activeId={activeId}
      dockedHere={dockedHere}
      dockSlot={dockSlot}
      title={title}
      state="readonly"
      kind={appWindow ? "native-window" : application ? "application" : "terminal"}
    >
      <div
        className={styles.webPipChrome}
        onPointerDown={(event) => onDragPointerDown("move", event)}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerCancel}
        onLostPointerCapture={onDragPointerCancel}
      >
        <span className={cn(styles.webPipTitle, "truncate text-fs-xs font-medium text-text-primary")}>{title}</span>
        {title !== kindLabel && (
          <small className={cn(styles.webPipMode, "truncate text-fs-2xs text-text-muted")}>{kindLabel}</small>
        )}
        <div className={styles.webPipActions} onPointerDown={(event) => event.stopPropagation()}>
          <HoverTip label={<TipBody title={openLabel} detail={openHint} />}>
            <button type="button" className={PIP_ICON_BUTTON} onClick={openInTab} aria-label={openLabel}>
              <ExternalLink size={14} className={PIP_ICON} aria-hidden="true" />
            </button>
          </HoverTip>
          <PipMoreMenu
            key={`${sessionId}:${branchId}`}
            showLabel={text("Show actions", "显示操作")}
            following={!pinned}
            onToggleFollow={toggleFollow}
          />
          <HoverTip label={<TipBody title={hideLabel} detail={hideHint} />}>
            <button
              type="button"
              className={cn(PIP_ICON_BUTTON, "-mr-1")}
              onClick={() => hideResourcePreview(sessionId, branchId)}
              aria-label={hideLabel}
            >
              <X size={14} className={PIP_ICON} aria-hidden="true" />
            </button>
          </HoverTip>
        </div>
      </div>
      <div className={cn("overflow-hidden relative flex-1 min-h-0 flex flex-col", PIP_STAGE)}>
        <div className={styles.webPipBody}>{body}</div>
      </div>
      {dockedHere ? null : PIP_RESIZE_DIRS.map((dir) => (
        <div
          key={dir}
          className={styles.webPipResize}
          data-dir={dir}
          data-pip-resize={dir}
          role="separator"
          aria-orientation={dir === "e" || dir === "w" ? "vertical" : "horizontal"}
          aria-label={resizeLabels[dir]}
          onPointerDown={(event) => onDragPointerDown("resize", event, dir)}
          onPointerMove={onDragPointerMove}
          onPointerUp={onDragPointerUp}
          onPointerCancel={onDragPointerCancel}
          onLostPointerCapture={onDragPointerCancel}
        />
      ))}
    </PipFrame>
  );
}
