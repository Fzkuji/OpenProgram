"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ExternalLink, Maximize2, Minimize2, MoreVertical, Pause, Pin, Play, X } from "lucide-react";

import { desktopBridge } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { MENU_PANEL } from "@/components/chat/top-bar/menu-styles";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSidebarMenu, type SidebarMenuItem } from "@/components/sidebar/use-sidebar-menu";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  controlResourceFromSession,
  displayedControlState,
  liveOperationMarker,
  operationHistory,
  requestExplicitPause,
  requestResumeAgent,
  resumeErrorFor,
  showActionsEnabled,
  toggleShowActions,
  useBrowserControlStore,
} from "@/lib/state/browser-control";
import { fittedImageRect, mapOperationPoint } from "@/lib/state/browser-marker-geometry";
import {
  followCurrentBranch,
  getPreviewPreference,
  hideResourcePreview,
  latestFollowTarget,
  listedBrowserResources,
  previewTabId,
  selectResourcePreview,
  togglePreviewExpanded,
  useBrowserResourceStore,
  viewedBranchFor,
  type SessionResource,
} from "@/lib/state/session-resources";
import { revealExistingWebTab } from "@/lib/state/web-page-management";
import {
  clampPipRect,
  getSnapshot,
  PIP_DEFAULT_HEIGHT,
  PIP_DEFAULT_WIDTH,
  PIP_MIN_HEIGHT,
  PIP_MIN_WIDTH,
  PIP_RESIZE_DIRS,
  pipChatRect,
  pipHostMode,
  resizePipRect,
  setSnapshot,
  startWebTabCaptureLoop,
  useWebTabPip,
  type PipResizeDir,
  type WebTabPipRect,
} from "@/lib/state/web-tab-pip-store";
import type { WebTabCaptureLoop } from "@/lib/state/web-tab-capture-loop";

import styles from "./center-tabs.module.css";

type PipDrag = {
  kind: "move" | "resize";
  pointerId: number;
  startX: number;
  startY: number;
  origin: WebTabPipRect;
  bounds: WebTabPipRect;
  tabId: string;
  target: HTMLElement;
  dir?: PipResizeDir;
};

function containerBox(el: HTMLElement): WebTabPipRect {
  const parent = el.offsetParent as HTMLElement | null;
  if (!parent) {
    return { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
  }
  const parentRect = parent.getBoundingClientRect();
  const chat = parent.querySelector(".center-pane-chat");
  if (chat instanceof HTMLElement && getComputedStyle(chat).display !== "none") {
    const rect = chat.getBoundingClientRect();
    return {
      x: rect.left - parentRect.left,
      y: rect.top - parentRect.top,
      width: rect.width,
      height: rect.height,
    };
  }
  return { x: 0, y: 0, width: parentRect.width, height: parentRect.height };
}

function measuredRect(el: HTMLElement): WebTabPipRect {
  const parent = el.offsetParent as HTMLElement | null;
  const er = el.getBoundingClientRect();
  if (!parent) {
    return { x: er.left, y: er.top, width: er.width, height: er.height };
  }
  const pr = parent.getBoundingClientRect();
  return {
    x: er.left - pr.left,
    y: er.top - pr.top,
    width: er.width,
    height: er.height,
  };
}

function resourceForTab(tabId: string): SessionResource | undefined {
  return listedBrowserResources().find(row => previewTabId(row) === tabId);
}

function statusLabel(
  state: ReturnType<typeof displayedControlState>,
  text: (en: string, zh: string) => string,
  connected: boolean,
): string {
  if (state === "yielding") return text("Pausing…", "正在暂停…");
  if (state === "paused") return text("Paused", "已暂停");
  if (state === "stop_unconfirmed") return text("Could not pause. Try again", "暂停失败，请重试");
  if (state === "unknown") {
    return connected
      ? text("Could not confirm status", "无法确认状态")
      : text("Connection lost", "连接已断开");
  }
  if (state === "idle" || state === "closed") return text("Ready to use", "可直接操作");
  return text("Active", "活动中");
}

function PipActionMark({
  point,
  body,
  image,
}: {
  point: { x: number; y: number; width?: number; height?: number };
  body: HTMLElement | null;
  image: { width: number; height: number };
}) {
  if (!body) return null;
  const box = body.getBoundingClientRect();
  const fitted = fittedImageRect({ width: box.width, height: box.height }, image);
  const pos = mapOperationPoint(point, fitted, image);
  if (!pos) return null;
  return <span className={styles.browserActionMark} style={{ left: pos.left, top: pos.top }} aria-hidden="true" />;
}

function PipMoreMenu({
  historyItems,
  historyLabel,
  showLabel,
}: {
  historyItems: SidebarMenuItem[];
  historyLabel: string;
  showLabel: string;
}) {
  const { text } = useTranslation();
  const menu = useSidebarMenu();
  const moreLabel = text("More", "更多");
  const native = typeof window !== "undefined" && !!window.openprogramDesktop?.contextMenu;
  const items: SidebarMenuItem[] = [
    {
      id: "show-actions",
      label: showLabel,
      checked: showActionsEnabled(),
      onSelect: () => { toggleShowActions(); },
    },
    {
      id: "history",
      label: historyLabel,
      separatorBefore: true,
      children: historyItems,
    },
  ];
  if (native) {
    return (
      <button
        type="button"
        className={styles.webToolbarBtn}
        title={moreLabel}
        aria-label={moreLabel}
        aria-haspopup="menu"
        aria-expanded={menu.open}
        onClick={(event) => {
          if (menu.open) menu.close();
          else menu.show(event, items);
        }}
      >
        <MoreVertical size={14} aria-hidden="true" />
      </button>
    );
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={styles.webToolbarBtn}
          title={moreLabel}
          aria-label={moreLabel}
        >
          <MoreVertical size={14} aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className={MENU_PANEL}>
        <DropdownMenuItem onSelect={() => { toggleShowActions(); }}>
          {showActionsEnabled() ? "✓ " : ""}{showLabel}
        </DropdownMenuItem>
        <DropdownMenuItem disabled>{historyLabel}</DropdownMenuItem>
        {historyItems.map((item) => (
          <DropdownMenuItem key={item.id} disabled>{item.label}</DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function WebTabPip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const ownerTabId = useWebTabPip((s) => s.ownerTabId);
  const hide = useWebTabPip((s) => s.hide);
  const rect = useWebTabPip((s) => s.rect);
  const expandedSize = useWebTabPip((s) => s.expandedSize);
  const setRect = useWebTabPip((s) => s.setRect);
  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const groups = useCenterTabs((s) => s.groups);
  const splitWebTabId = useCenterTabs((s) => s.splitWebTabId);
  const tab = tabId
    ? tabs.find((item) => item.id === tabId && item.kind === "web")
    : undefined;
  const owner = ownerTabId ? tabs.find((item) => item.id === ownerTabId) : undefined;
  const sessionId = owner?.kind === "session" ? owner.sessionId || null : null;
  const branchId = sessionId ? viewedBranchFor(sessionId) : null;
  const pref = sessionId ? getPreviewPreference(sessionId, branchId) : null;
  const connected = useBrowserResourceStore(s => s.connected);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserResourceStore(s => s.preferences);
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.pending);
  useBrowserControlStore(s => s.resumeError);
  useBrowserControlStore(s => s.history);
  const resource = tabId ? resourceForTab(tabId) : undefined;
  const control = resource ? controlResourceFromSession(resource) : null;
  const center = { tabs, activeId, groups, splitWebTabId };
  const live = pipHostMode(tabId, ownerTabId, center) === "chat";
  const expanded = !!pref?.expanded;
  const rootRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<PipDrag | null>(null);
  const pendingRectRef = useRef<WebTabPipRect | null>(null);
  const rafRef = useRef(0);
  const shotRef = useRef<HTMLImageElement>(null);
  const captureGenRef = useRef(0);
  const captureLoopRef = useRef<WebTabCaptureLoop | null>(null);
  const dragFinishingRef = useRef(false);
  const endActiveDragRef = useRef<(persist: boolean) => void>(() => {});
  const [freshness, setFreshness] = useState<"live" | "last-frame" | "unavailable">("unavailable");
  const [chatBox, setChatBox] = useState<WebTabPipRect | null>(null);
  const [, render] = useState(0);
  const bridge = desktopBridge();
  const url = tab?.url || (tabId?.startsWith("w:") ? tabId.slice(2) : "");
  const marker = resource?.resourceId
    ? liveOperationMarker(resource.resourceId, { generation: resource.generation || 0 })
    : null;

  const showShot = (dataUrl: string | null) => {
    const img = shotRef.current;
    if (!img) return;
    if (dataUrl) {
      img.src = dataUrl;
      img.style.display = "block";
      return;
    }
    img.removeAttribute("src");
    img.style.display = "none";
  };

  useEffect(() => () => {
    endActiveDragRef.current(false);
  }, []);

  useEffect(() => {
    if (live) return;
    endActiveDragRef.current(false);
    captureGenRef.current += 1;
  }, [live]);

  useLayoutEffect(() => {
    if (!tabId || !live) return;
    showShot(getSnapshot(tabId) ?? null);
  }, [tabId, live]);

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || !live) {
      setChatBox(null);
      return;
    }
    const next = containerBox(el);
    setChatBox((prev) => (
      prev
        && prev.x === next.x
        && prev.y === next.y
        && prev.width === next.width
        && prev.height === next.height
        ? prev
        : next
    ));
  }, [live, expanded, rect, expandedSize]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !live) return;
    const parent = el.offsetParent;
    if (!(parent instanceof HTMLElement)) return;
    const reclamp = () => {
      if (dragRef.current) return;
      const current = useWebTabPip.getState().rect;
      if (!current || expanded) return;
      const next = clampPipRect(current, containerBox(el));
      if (
        next.x !== current.x || next.y !== current.y
        || next.width !== current.width || next.height !== current.height
      ) {
        setRect(next);
      }
    };
    const ro = new ResizeObserver(reclamp);
    ro.observe(parent);
    ro.observe(el);
    const chat = parent.querySelector(".center-pane-chat");
    if (chat instanceof HTMLElement) ro.observe(chat);
    return () => ro.disconnect();
  }, [live, expanded, setRect]);

  useEffect(() => {
    if (!tabId || !live) return;
    const gen = ++captureGenRef.current;
    const capture = bridge?.webTab.capture;
    showShot(getSnapshot(tabId) ?? null);
    if (typeof capture !== "function") {
      setFreshness(getSnapshot(tabId) ? "last-frame" : "unavailable");
      return;
    }
    const loop = startWebTabCaptureLoop({
      tabId,
      generation: gen,
      isCurrent: () => {
        if (captureGenRef.current !== gen) return null;
        const pip = useWebTabPip.getState();
        if (pip.tabId !== tabId) return null;
        return { tabId, generation: gen };
      },
      capture,
      onFrame: (id, dataUrl) => {
        if (captureGenRef.current !== gen || dragRef.current) return;
        setSnapshot(id, dataUrl);
        showShot(dataUrl);
        setFreshness("live");
      },
      onUnavailable: (id) => {
        if (captureGenRef.current !== gen || dragRef.current) return;
        setFreshness(getSnapshot(id) ? "last-frame" : "unavailable");
      },
    });
    captureLoopRef.current = loop;
    return () => {
      loop.stop();
      if (captureLoopRef.current === loop) captureLoopRef.current = null;
      endActiveDragRef.current(false);
      if (captureGenRef.current === gen) captureGenRef.current += 1;
    };
  }, [bridge, tabId, live]);

  const presented = live && chatBox
    ? pipChatRect(rect, expanded, chatBox, expandedSize)
    : null;
  const pipStyle = !presented ? undefined : {
    left: presented.x,
    top: presented.y,
    width: presented.width,
    height: presented.height,
    right: "auto",
    bottom: "auto",
  };

  if (!tabId || !tab || !live) return null;

  const title = resource?.title || tab.title || url;
  const openPage = text("Open page", "打开页面");
  const hideLabel = text("Hide", "隐藏");
  const expandLabel = pref?.expanded ? text("Collapse", "收起") : text("Expand", "展开");
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
  const pinLabel = pinned
    ? text("Unpin preview", "取消固定预览")
    : text("Pin preview", "固定预览");
  const pinHint = pinned
    ? text("Return to automatic display of the page the Agent is operating", "恢复自动显示 Agent 正在操作的页面")
    : text("Hold the current page", "保持当前页面");
  const modeLabel = pinned
    ? text("Fixed preview", "固定预览")
    : text("Auto preview", "自动预览");
  const controlState = control ? displayedControlState(control) : null;
  const stateText = controlState ? statusLabel(controlState, text, connected) : "";
  const resumeError = control ? resumeErrorFor(control.resourceId) : undefined;
  const statusText = [resumeError || stateText, modeLabel].filter(Boolean).join(" · ");
  const frameState = !connected && freshness === "live" ? "last-frame" : freshness;
  const freshLabel = frameState === "live"
    ? text("Read-only image mirror", "只读图像镜像")
    : frameState === "last-frame"
      ? text("Last frame", "最后一帧")
      : text("Image preview unavailable", "无法预览图像");
  const takeoverKind = !controlState || controlState === "idle" || controlState === "closed"
    ? null
    : controlState === "paused"
      ? "resume"
      : controlState === "yielding"
        ? "yielding"
        : controlState === "stop_unconfirmed"
          ? "retry"
          : "pause";
  const takeoverLabel = takeoverKind === "resume"
    ? text("Continue Agent", "让 Agent 继续")
    : takeoverKind === "yielding"
      ? text("Pausing…", "正在暂停…")
      : takeoverKind === "retry"
        ? text("Retry pause", "重试暂停")
        : text("Pause Agent to use page", "暂停 Agent，我来操作");
  const takeoverDisabled = takeoverKind === "yielding"
    || controlState === "unknown"
    || !connected
    || (takeoverKind === "resume" && controlState !== "paused");
  const history = control ? operationHistory(control.resourceId) : [];
  const historyLabel = text("Operation history", "操作历史");
  const showLabel = text("Show actions", "显示操作");
  const historyItems: SidebarMenuItem[] = history.length === 0
    ? [{ id: "empty", label: text("No operations yet.", "尚无操作。"), disabled: true }]
    : history.map(item => ({
      id: item.id,
      label: `${item.action} · ${item.phase}${item.error ? ` · ${item.error}` : ""}`,
      disabled: true,
    }));

  const togglePinnedPreview = () => {
    if (!sessionId) return;
    if (pinned) {
      const next = followCurrentBranch(sessionId, branchId);
      const target = listedBrowserResources().find(row => row.id === (next.targetId || latestFollowTarget(sessionId, branchId)));
      const nextTab = previewTabId(target);
      if (nextTab && ownerTabId) useWebTabPip.getState().show(nextTab, ownerTabId);
    } else if (resource?.id) {
      selectResourcePreview(sessionId, branchId, resource.id);
    }
    render(value => value + 1);
  };

  const liveRect = (el: HTMLElement) => presented ?? rect ?? measuredRect(el);

  const previewRect = (
    el: HTMLElement,
    drag: PipDrag,
    next: WebTabPipRect,
  ) => {
    if (drag.kind === "move") {
      el.style.transform = `translate(${next.x - drag.origin.x}px, ${next.y - drag.origin.y}px)`;
      return;
    }
    el.style.left = `${next.x}px`;
    el.style.top = `${next.y}px`;
    el.style.width = `${next.width}px`;
    el.style.height = `${next.height}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
  };

  const applyInlineRect = (el: HTMLElement, next: WebTabPipRect) => {
    el.style.transform = "";
    el.style.willChange = "";
    el.classList.remove(styles.webPipDragging);
    el.style.left = `${next.x}px`;
    el.style.top = `${next.y}px`;
    el.style.width = `${next.width}px`;
    el.style.height = `${next.height}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
  };

  const persistRect = (
    el: HTMLElement | null,
    next: WebTabPipRect,
    kind: "move" | "resize",
  ) => {
    if (el) applyInlineRect(el, next);
    const collapsed = {
      x: next.x,
      y: next.y,
      width: rect?.width ?? PIP_DEFAULT_WIDTH,
      height: rect?.height ?? PIP_DEFAULT_HEIGHT,
    };
    if (expanded) {
      if (kind === "resize") {
        useWebTabPip.setState({
          rect: collapsed,
          expandedSize: { width: next.width, height: next.height },
        });
        return;
      }
      setRect(collapsed);
      return;
    }
    setRect(next);
  };

  const finishDrag = (persist: boolean) => {
    const drag = dragRef.current;
    if (!drag || dragFinishingRef.current) return;
    dragFinishingRef.current = true;
    dragRef.current = null;
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const next = pendingRectRef.current;
    pendingRectRef.current = null;
    const el = rootRef.current;
    if (drag.target.hasPointerCapture(drag.pointerId)) {
      drag.target.releasePointerCapture(drag.pointerId);
    }
    const pip = useWebTabPip.getState();
    const commit = persist && !!next && live && pip.tabId === drag.tabId;
    if (commit && next) {
      persistRect(el, clampPipRect(next, el ? containerBox(el) : drag.bounds), drag.kind);
    } else if (el) {
      applyInlineRect(el, drag.origin);
    }
    if (persist) captureLoopRef.current?.resume();
    dragFinishingRef.current = false;
  };
  endActiveDragRef.current = finishDrag;

  const onDragPointerDown = (
    kind: "move" | "resize",
    event: React.PointerEvent<HTMLElement>,
    dir?: PipResizeDir,
  ) => {
    if (event.button !== 0) return;
    const el = rootRef.current;
    if (!el) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      kind,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: liveRect(el),
      bounds: containerBox(el),
      tabId,
      target: event.currentTarget,
      dir,
    };
    pendingRectRef.current = dragRef.current.origin;
    el.classList.add(styles.webPipDragging);
    el.style.willChange = kind === "move" ? "transform" : "left, top, width, height";
    captureLoopRef.current?.pause();
  };

  const onDragPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    const el = rootRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !el) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = drag.kind === "move"
      ? clampPipRect(
        { ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy },
        drag.bounds,
      )
      : resizePipRect(
        drag.origin,
        dx,
        dy,
        drag.bounds,
        PIP_MIN_WIDTH,
        PIP_MIN_HEIGHT,
        drag.dir ?? "se",
      );
    pendingRectRef.current = next;
    if (rafRef.current) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = 0;
      const liveDrag = pendingRectRef.current;
      const current = dragRef.current;
      const node = rootRef.current;
      if (!liveDrag || !current || !node) return;
      previewRect(node, current, liveDrag);
    });
  };

  const onDragPointerUp = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    finishDrag(true);
  };

  const onDragPointerCancel = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    finishDrag(true);
  };

  return (
    <div
      ref={rootRef}
      className={`${styles.webPip} ${expanded ? styles.webPipExpanded : ""}`}
      data-pip="true"
      data-pip-host="chat"
      role="complementary"
      aria-label={title}
      data-state={controlState || "readonly"}
      style={pipStyle}
    >
      <div
        className={styles.webPipChrome}
        onPointerDown={(event) => onDragPointerDown("move", event)}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerCancel}
        onLostPointerCapture={onDragPointerCancel}
      >
        <span className={styles.webPipTitle} title={`${title}${statusText || modeLabel ? ` · ${statusText || modeLabel}` : ""}`}>{title}</span>
        {statusText ? (
          <small
            className={styles.webPipMode}
            data-resume-error={resumeError ? "true" : undefined}
            title={resumeError ? `${stateText}: ${resumeError}` : stateText}
            aria-label={resumeError ? `${stateText}: ${resumeError}` : stateText}
            role={resumeError ? "status" : undefined}
            aria-live={resumeError ? "polite" : undefined}
          >
            {statusText}
          </small>
        ) : null}
        <div className={styles.webPipActions} onPointerDown={(event) => event.stopPropagation()}>
          <button
            type="button"
            className={styles.webToolbarBtn}
            onClick={() => revealExistingWebTab(tabId, useCenterTabs.getState())}
            title={openPage}
            aria-label={openPage}
          >
            <ExternalLink size={14} aria-hidden="true" />
          </button>
          {takeoverKind && control ? (
            <button
              type="button"
              className={styles.webToolbarBtn}
              disabled={takeoverDisabled}
              title={resumeError || takeoverLabel}
              aria-label={resumeError ? `${takeoverLabel}: ${resumeError}` : takeoverLabel}
              onClick={() => {
                void (takeoverKind === "resume" ? requestResumeAgent(control) : requestExplicitPause(control));
              }}
            >
              {takeoverKind === "resume"
                ? <Play size={14} aria-hidden="true" />
                : <Pause size={14} aria-hidden="true" />}
            </button>
          ) : null}
          <button
            type="button"
            className={styles.webToolbarBtn}
            aria-pressed={pinned}
            aria-label={pinLabel}
            title={pinHint}
            style={pinned ? { background: "var(--bg-hover)", color: "var(--text-bright)" } : undefined}
            onClick={togglePinnedPreview}
          >
            <Pin size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={styles.webToolbarBtn}
            onClick={() => {
              if (sessionId) togglePreviewExpanded(sessionId, branchId);
              render(value => value + 1);
            }}
            title={expandLabel}
            aria-label={expandLabel}
          >
            {pref?.expanded ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
          </button>
          <PipMoreMenu
            historyItems={historyItems}
            historyLabel={historyLabel}
            showLabel={showLabel}
          />
          <button
            type="button"
            className={styles.webToolbarBtn}
            onClick={() => {
              if (sessionId) hideResourcePreview(sessionId, branchId);
              hide();
            }}
            title={hideLabel}
            aria-label={hideLabel}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className={styles.webPipStage}>
        <div className={styles.webPipBody}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img ref={shotRef} className={styles.webPipShot} alt="" />
          {frameState !== "live" && (
            <div className={styles.webPipFallback}>{freshLabel}</div>
          )}
          {marker?.point ? (
            <PipActionMark
              point={marker.point}
              body={shotRef.current?.parentElement ?? null}
              image={{
                width: shotRef.current?.naturalWidth || marker.point.width || 0,
                height: shotRef.current?.naturalHeight || marker.point.height || 0,
              }}
            />
          ) : null}
        </div>
      </div>
      {PIP_RESIZE_DIRS.map((dir) => (
        <div
          key={dir}
          className={styles.webPipResize}
          data-dir={dir}
          data-pip-resize={dir}
          role="separator"
          aria-orientation={dir === "e" || dir === "w" ? "vertical" : "horizontal"}
          aria-label={resizeLabels[dir]}
          title={resizeLabels[dir]}
          onPointerDown={(event) => onDragPointerDown("resize", event, dir)}
          onPointerMove={onDragPointerMove}
          onPointerUp={onDragPointerUp}
          onPointerCancel={onDragPointerCancel}
          onLostPointerCapture={onDragPointerCancel}
        />
      ))}
    </div>
  );
}
