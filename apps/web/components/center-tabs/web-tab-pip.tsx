"use client";

import { useEffect, useRef, useState } from "react";
import { Eye, Maximize2, Minimize2, X } from "lucide-react";

import { desktopBridge } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  controlResourceFromSession,
  displayedControlState,
  liveOperationMarker,
} from "@/lib/state/browser-control";
import { fittedImageRect, mapOperationPoint } from "@/lib/state/browser-marker-geometry";
import {
  followCurrentBranch,
  getPreviewPreference,
  hideResourcePreview,
  latestFollowTarget,
  listedBrowserResources,
  previewTabId,
  togglePreviewExpanded,
  useBrowserResourceStore,
  viewedBranchFor,
  type SessionResource,
} from "@/lib/state/session-resources";
import {
  clampPipRect,
  getSnapshot,
  pipCoversCenter,
  setSnapshot,
  startWebTabCaptureLoop,
  useWebTabPip,
  type WebTabPipRect,
} from "@/lib/state/web-tab-pip-store";
import { BrowserControlBar } from "./browser-control-bar";

import styles from "./center-tabs.module.css";

type PipDrag = {
  kind: "move" | "resize";
  pointerId: number;
  startX: number;
  startY: number;
  origin: WebTabPipRect;
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

export function WebTabPip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const ownerTabId = useWebTabPip((s) => s.ownerTabId);
  const hide = useWebTabPip((s) => s.hide);
  const rect = useWebTabPip((s) => s.rect);
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
  const resource = tabId ? resourceForTab(tabId) : undefined;
  const control = resource ? controlResourceFromSession(resource) : null;
  const center = { tabs, activeId, groups, splitWebTabId };
  const live = !!tabId && !!ownerTabId && pipCoversCenter(tabId, ownerTabId, center);
  const rootRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<PipDrag | null>(null);
  const pendingRectRef = useRef<WebTabPipRect | null>(null);
  const rafRef = useRef(0);
  const shotRef = useRef<HTMLImageElement>(null);
  const captureGenRef = useRef(0);
  const [freshness, setFreshness] = useState<"live" | "last-frame" | "unavailable">("unavailable");
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
    if (rafRef.current) window.cancelAnimationFrame(rafRef.current);
  }, []);

  useEffect(() => {
    if (live) return;
    dragRef.current = null;
    pendingRectRef.current = null;
    captureGenRef.current += 1;
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
  }, [live]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !live) return;
    const parent = el.offsetParent;
    if (!(parent instanceof HTMLElement)) return;
    const reclamp = () => {
      const current = useWebTabPip.getState().rect;
      if (!current) return;
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
    const chat = parent.querySelector(".center-pane-chat");
    if (chat instanceof HTMLElement) ro.observe(chat);
    return () => ro.disconnect();
  }, [live, setRect]);

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
        setSnapshot(id, dataUrl);
        if (captureGenRef.current !== gen) return;
        showShot(dataUrl);
        setFreshness("live");
      },
      onUnavailable: (id) => {
        if (captureGenRef.current !== gen) return;
        setFreshness(getSnapshot(id) ? "last-frame" : "unavailable");
      },
    });
    return () => {
      loop.stop();
      if (captureGenRef.current === gen) captureGenRef.current += 1;
    };
  }, [bridge, tabId, live]);

  const placed = !!rect;
  const pipStyle = placed ? {
    left: rect.x,
    top: rect.y,
    width: rect.width,
    height: rect.height,
    right: "auto",
    bottom: "auto",
  } : undefined;

  if (!tabId || !tab || !live) return null;

  const title = tab.title || url;
  const followLabel = text("Follow current branch", "跟随当前分支");
  const usePage = text("Use in webpage", "在网页中使用");
  const hideLabel = text("Hide", "隐藏");
  const expandLabel = pref?.expanded ? text("Collapse", "收起") : text("Expand", "展开");
  const resizeLabel = text("Resize preview", "调整预览大小");
  const modeLabel = pref?.mode === "follow"
    ? text("Following Agent", "跟随 Agent")
    : text("Manual inspection", "手动查看");
  const frameState = !connected && freshness === "live" ? "last-frame" : freshness;
  const freshLabel = frameState === "live"
    ? text("Read-only image mirror", "只读图像镜像")
    : frameState === "last-frame"
      ? text("Last frame", "最后一帧")
      : text("Image preview unavailable", "无法预览图像");

  const liveRect = (el: HTMLElement) =>
    rect ?? measuredRect(el);

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

  const commitRect = (el: HTMLElement, next: WebTabPipRect) => {
    el.style.transform = "";
    el.style.willChange = "";
    el.style.left = `${next.x}px`;
    el.style.top = `${next.y}px`;
    el.style.width = `${next.width}px`;
    el.style.height = `${next.height}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
    el.classList.remove(styles.webPipDragging);
    setRect(next);
  };

  const onDragPointerDown = (
    kind: "move" | "resize",
    event: React.PointerEvent<HTMLElement>,
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
    };
    pendingRectRef.current = dragRef.current.origin;
    el.classList.add(styles.webPipDragging);
    el.style.willChange = kind === "move" ? "transform" : "left, top, width, height";
    showShot(getSnapshot(tabId) ?? null);
  };

  const onDragPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    const el = rootRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !el) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = clampPipRect(
      drag.kind === "move"
        ? { ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy }
        : { ...drag.origin, width: drag.origin.width + dx, height: drag.origin.height + dy },
      containerBox(el),
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
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const el = rootRef.current;
    const next = pendingRectRef.current;
    dragRef.current = null;
    pendingRectRef.current = null;
    if (el && next) commitRect(el, next);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div
      ref={rootRef}
      className={`${styles.webPip} ${pref?.expanded ? styles.webPipExpanded : ""}`}
      data-pip="true"
      role="complementary"
      aria-label={title}
      data-state={control ? displayedControlState(control) : "readonly"}
      style={pipStyle}
    >
      <div
        className={styles.webPipChrome}
        onPointerDown={(event) => onDragPointerDown("move", event)}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerUp}
      >
        <span className={styles.webPipTitle} title={`${title} · ${modeLabel}`}>{title}</span>
        <small className={styles.webPipMode}>{modeLabel}</small>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => {
            if (!sessionId) return;
            const next = followCurrentBranch(sessionId, branchId);
            const target = listedBrowserResources().find(row => row.id === (next.targetId || latestFollowTarget(sessionId, branchId)));
            const nextTab = previewTabId(target);
            if (nextTab && ownerTabId) useWebTabPip.getState().show(nextTab, ownerTabId);
            render(value => value + 1);
          }}
          title={followLabel}
          aria-label={followLabel}
        >
          {followLabel}
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => useCenterTabs.getState().setActive(tabId)}
          title={usePage}
          aria-label={usePage}
        >
          <Eye size={14} />
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => {
            if (sessionId) togglePreviewExpanded(sessionId, branchId);
            render(value => value + 1);
          }}
          title={expandLabel}
          aria-label={expandLabel}
        >
          {pref?.expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => {
            if (sessionId) hideResourcePreview(sessionId, branchId);
            hide();
          }}
          title={hideLabel}
          aria-label={hideLabel}
        >
          <X size={14} />
        </button>
      </div>
      <BrowserControlBar resource={control} compact />
      <div className={styles.webPipStage}>
        <div className={styles.webPipBody}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img ref={shotRef} className={styles.webPipShot} alt="" />
          {frameState !== "live" && (
            <div className={styles.webPipFallback}>
              {freshLabel}
              {tabId ? (
                <button type="button" className={styles.webToolbarBtn} onClick={() => useCenterTabs.getState().setActive(tabId)}>
                  {usePage}
                </button>
              ) : null}
            </div>
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
        <div
          className={styles.webPipResize}
          role="separator"
          aria-orientation="horizontal"
          aria-label={resizeLabel}
          title={resizeLabel}
          onPointerDown={(event) => onDragPointerDown("resize", event)}
          onPointerMove={onDragPointerMove}
          onPointerUp={onDragPointerUp}
          onPointerCancel={onDragPointerUp}
        />
      </div>
    </div>
  );
}
