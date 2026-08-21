"use client";

import { useEffect, useRef } from "react";
import { Columns2, ExternalLink, Maximize2, X } from "lucide-react";

import {
  desktopBridge,
  ensureWebView,
  registerVisibleWebTabBounds,
  removeVisibleWebTabBounds,
  setWebTabReady,
} from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  clampPipRect,
  pipCoversCenter,
  useWebTabPip,
  type WebTabPipRect,
} from "@/lib/state/web-tab-pip-store";
import { isWebTabOccluded, measureWebTabBounds } from "@/lib/web-tab-bounds";

import styles from "./center-tabs.module.css";

const BOUNDS_THROTTLE_MS = 100;

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

export function WebTabPip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const hide = useWebTabPip((s) => s.hide);
  const rect = useWebTabPip((s) => s.rect);
  const setRect = useWebTabPip((s) => s.setRect);
  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const groups = useCenterTabs((s) => s.groups);
  const tab = tabId
    ? tabs.find((item) => item.id === tabId && item.kind === "web")
    : undefined;
  const visible = !!tabId && pipCoversCenter(tabId, { tabs, activeId, groups });
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<PipDrag | null>(null);
  const throttleRef = useRef(0);
  const lastPublishRef = useRef(0);
  const reportRef = useRef<(immediate?: boolean) => void>(() => {});
  const bridge = desktopBridge();
  const url = tab?.url || (tabId?.startsWith("w:") ? tabId.slice(2) : "");

  useEffect(() => {
    if (tabId && !visible) hide();
  }, [tabId, visible, hide, activeId, groups, tabs]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !visible) return;
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
  }, [visible, setRect]);

  useEffect(() => {
    if (!bridge || !tabId || !visible || !url) return;
    ensureWebView(bridge, tabId, url);
    const el = bodyRef.current;
    if (!el) return;
    const publish = () => {
      lastPublishRef.current = Date.now();
      const bounds = measureWebTabBounds(el);
      const occluded = isWebTabOccluded(
        bounds,
        document.querySelectorAll(
          '[role="dialog"], [role="menu"], [role="listbox"], .branches-merge-modal-backdrop, [data-native-view-occluder="true"]',
        ),
      );
      if (occluded || bounds.width <= 0 || bounds.height <= 0) {
        removeVisibleWebTabBounds(bridge, tabId);
        setWebTabReady(tabId, false);
        return;
      }
      registerVisibleWebTabBounds(bridge, tabId, bounds);
      setWebTabReady(tabId, true);
    };
    const report = (immediate = false) => {
      if (immediate) {
        window.clearTimeout(throttleRef.current);
        throttleRef.current = 0;
        publish();
        return;
      }
      const wait = Math.max(0, BOUNDS_THROTTLE_MS - (Date.now() - lastPublishRef.current));
      if (wait === 0) {
        window.clearTimeout(throttleRef.current);
        throttleRef.current = 0;
        publish();
        return;
      }
      if (throttleRef.current) return;
      throttleRef.current = window.setTimeout(() => {
        throttleRef.current = 0;
        publish();
      }, wait) as unknown as number;
    };
    const onWindowChange = () => report();
    reportRef.current = report;
    report(true);
    const ro = new ResizeObserver(() => report());
    ro.observe(el);
    const mo = new MutationObserver(() => report());
    mo.observe(document.body, { subtree: true, childList: true, attributes: true });
    window.addEventListener("resize", onWindowChange);
    window.addEventListener("scroll", onWindowChange, true);
    return () => {
      reportRef.current = () => {};
      window.clearTimeout(throttleRef.current);
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", onWindowChange);
      window.removeEventListener("scroll", onWindowChange, true);
      removeVisibleWebTabBounds(bridge, tabId);
      setWebTabReady(tabId, false);
    };
  }, [bridge, tabId, url, visible]);

  useEffect(() => {
    if (!bridge || !tabId || !visible) return;
    return bridge.webTab.onState((state) => {
      if (state.id !== tabId) return;
      if (state.url) useCenterTabs.getState().updateWebTab(tabId, { url: state.url });
      if (state.title) useCenterTabs.getState().updateWebTab(tabId, { title: state.title });
      if (state.faviconUrl !== undefined) {
        useCenterTabs.getState().updateWebTab(tabId, { faviconUrl: state.faviconUrl });
      }
    });
  }, [bridge, tabId, visible]);

  if (!tabId || !tab || !visible) return null;

  const title = tab.title || url;
  const expandSplit = text("Split with chat", "展开为分屏");
  const takeOver = text("Open fullscreen", "全屏接管");
  const closePreview = text("Close preview", "关闭预览");
  const openExternal = text("Open in new tab", "在新标签打开");
  const resizeLabel = text("Resize preview", "调整预览大小");

  const liveRect = (el: HTMLElement) =>
    rect ?? measuredRect(el);

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
  };

  const onDragPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    const el = rootRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !el) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = drag.kind === "move"
      ? { ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy }
      : { ...drag.origin, width: drag.origin.width + dx, height: drag.origin.height + dy };
    setRect(clampPipRect(next, containerBox(el)));
  };

  const onDragPointerUp = (event: React.PointerEvent<HTMLElement>) => {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    reportRef.current(true);
  };

  const placed = !!rect;

  return (
    <div
      ref={rootRef}
      className={styles.webPip}
      role="complementary"
      aria-label={title}
      style={placed ? {
        left: rect.x,
        top: rect.y,
        width: rect.width,
        height: rect.height,
        right: "auto",
        bottom: "auto",
      } : undefined}
    >
      <div
        className={styles.webPipChrome}
        onPointerDown={(event) => onDragPointerDown("move", event)}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerUp}
      >
        <span className={styles.webPipTitle} title={title}>{title}</span>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => useCenterTabs.getState().setSplitWebTab(tabId)}
          title={expandSplit}
          aria-label={expandSplit}
        >
          <Columns2 size={14} />
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => useCenterTabs.getState().setActive(tabId)}
          title={takeOver}
          aria-label={takeOver}
        >
          <Maximize2 size={14} />
        </button>
        {!bridge ? (
          <button
            type="button"
            className={styles.webToolbarBtn}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => window.open(url, "_blank", "noopener")}
            title={openExternal}
            aria-label={openExternal}
          >
            <ExternalLink size={14} />
          </button>
        ) : null}
        <button
          type="button"
          className={styles.webToolbarBtn}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={hide}
          title={closePreview}
          aria-label={closePreview}
        >
          <X size={14} />
        </button>
      </div>
      <div className={styles.webPipStage}>
        <div ref={bodyRef} className={styles.webPipBody}>
          {bridge ? null : url.startsWith("file:") ? (
            <div className={styles.webPipFallback}>
              {text(
                "Local files can only be opened in the desktop app.",
                "本地文件仅能在桌面应用中打开。",
              )}
            </div>
          ) : (
            <iframe
              className={styles.webFrame}
              src={url}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
              referrerPolicy="no-referrer"
              title={text("Web page", "网页")}
            />
          )}
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
