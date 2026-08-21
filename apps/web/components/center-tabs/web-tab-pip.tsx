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
import { findCenterTabGroup } from "@/lib/state/center-tab-groups";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useWebTabPip } from "@/lib/state/web-tab-pip-store";
import { isWebTabOccluded, measureWebTabBounds } from "@/lib/web-tab-bounds";

import styles from "./center-tabs.module.css";

function pipCoversCenter(tabId: string): boolean {
  const state = useCenterTabs.getState();
  if (!state.tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return false;
  }
  if (state.activeId === tabId) return false;
  const group = state.activeId
    ? findCenterTabGroup(state.groups, state.activeId)
    : undefined;
  if (group?.visibleIds.includes(tabId)) return false;
  return true;
}

export function WebTabPip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const hide = useWebTabPip((s) => s.hide);
  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const groups = useCenterTabs((s) => s.groups);
  const tab = tabId
    ? tabs.find((item) => item.id === tabId && item.kind === "web")
    : undefined;
  const visible = !!tabId && pipCoversCenter(tabId);
  const bodyRef = useRef<HTMLDivElement>(null);
  const bridge = desktopBridge();
  const url = tab?.url || (tabId?.startsWith("w:") ? tabId.slice(2) : "");

  useEffect(() => {
    if (tabId && !visible) hide();
  }, [tabId, visible, hide, activeId, groups, tabs]);

  useEffect(() => {
    if (!bridge || !tabId || !visible || !url) return;
    ensureWebView(bridge, tabId, url);
    const el = bodyRef.current;
    if (!el) return;
    const report = () => {
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
    report();
    const ro = new ResizeObserver(report);
    ro.observe(el);
    const mo = new MutationObserver(report);
    mo.observe(document.body, { subtree: true, childList: true, attributes: true });
    window.addEventListener("resize", report);
    window.addEventListener("scroll", report, true);
    return () => {
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", report);
      window.removeEventListener("scroll", report, true);
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

  return (
    <div className={styles.webPip} role="complementary" aria-label={title}>
      <div className={styles.webPipChrome}>
        <span className={styles.webPipTitle} title={title}>{title}</span>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => useCenterTabs.getState().setSplitWebTab(tabId)}
          title={expandSplit}
          aria-label={expandSplit}
        >
          <Columns2 size={14} />
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
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
          onClick={hide}
          title={closePreview}
          aria-label={closePreview}
        >
          <X size={14} />
        </button>
      </div>
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
    </div>
  );
}
