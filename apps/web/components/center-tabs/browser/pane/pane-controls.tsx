"use client";

/** Pieces both web pane variants share: the toolbar's bookmark, home and
 *  collapse-to-PiP buttons, the page stage and the agent-control layer. */
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { House, PictureInPicture2, Star } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { isBookmarked, subscribeBookmarks, toggleBookmark, updateBookmarkFavicon } from "@/lib/tabs/browser/bookmarks";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { collapseWebTabToPip, pipCollapseTargetFor, useWebTabPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { controlResourceFromSession, presentedControlState } from "@/lib/browser/control/browser-control";
import { listedBrowserResources, previewTabId } from "@/lib/chat/resources/session-resources";
import styles from "../browser.module.css";
import { webToolbarButton } from "../toolbar/toolbar-button";
import { addressStarButton, ToolbarTip } from "../toolbar/browser-chrome";
import { ActionCueTravel, BrowserControlBar } from "../toolbar/browser-control-bar";
import { controlButtonVisible, cueCancelKey, prefersCueReducedMotion } from "@/lib/browser/control/browser-action-cue";
import { liveOperationMarker, useBrowserControlStore } from "@/lib/browser/control/browser-control";

export function BookmarkButton({ tabId, url, title }: { tabId: string; url: string; title: string }) {
  const { text } = useTranslation();
  const faviconUrl = useCenterTabs(state => state.tabs.find(tab => tab.id === tabId)?.faviconUrl);
  useEffect(() => updateBookmarkFavicon(url, faviconUrl), [url, faviconUrl]);
  const [bookmarked, setBookmarked] = useState(() => isBookmarked(url));

  useEffect(() => {
    const refresh = () => setBookmarked(isBookmarked(url));
    refresh();
    return subscribeBookmarks(refresh);
  }, [url]);

  const label = text(bookmarked ? "Remove bookmark" : "Bookmark", bookmarked ? "移除书签" : "添加书签");
  return (
    <ToolbarTip label={label}>
      <button
        type="button"
        className={addressStarButton()}
        data-on={bookmarked || undefined}
        onClick={() => toggleBookmark({ url, title, faviconUrl })}
        aria-label={label}
      >
        <Star size={14} fill={bookmarked ? "currentColor" : "none"} />
      </button>
    </ToolbarTip>
  );
}

export function findButton() {
  return buttonVariants({ variant: "ghost", size: "icon-xs" });
}

export function resourceForLiveTab(tabId: string) {
  const row = listedBrowserResources().find(item => previewTabId(item) === tabId);
  return row ? controlResourceFromSession(row) : null;
}

export function usePipCollapseTarget(tabId: string) {
  const tabs = useCenterTabs((s) => s.tabs);
  const groups = useCenterTabs((s) => s.groups);
  const boundOwnerId = useWebTabPip((s) => (s.tabId === tabId ? s.ownerTabId : null));
  return boundOwnerId ?? pipCollapseTargetFor(tabId, { tabs, groups });
}

export function CollapseToPipButton({ tabId }: { tabId: string }) {
  const { text } = useTranslation();
  const tabs = useCenterTabs((s) => s.tabs);
  const boundOwnerId = useWebTabPip((s) => (s.tabId === tabId ? s.ownerTabId : null));
  const targetId = usePipCollapseTarget(tabId);
  if (!targetId) return null;
  const targetTitle = tabs.find((tab) => tab.id === targetId)?.title
    || text("that session", "该会话");
  const label = boundOwnerId
    ? text(
      `Return to the floating window in “${targetTitle}”`,
      `返回「${targetTitle}」的悬浮窗`,
    )
    : text(`Float over “${targetTitle}”`, `浮动到「${targetTitle}」`);
  return (
    <ToolbarTip label={label}>
      <button
        type="button"
        className={webToolbarButton()}
        onClick={() => collapseWebTabToPip(tabId)}
        aria-label={label}
      >
        <PictureInPicture2 size={14} />
      </button>
    </ToolbarTip>
  );
}

export function WebPaneStage({
  children,
}: {
  children: ReactNode;
}) {
  return <div className="relative flex-1 min-w-0 min-h-0 grid grid-cols-[minmax(0,_1fr)] grid-rows-[minmax(0,_1fr)]">{children}</div>;
}

export function IframeAgentLayer({
  tabId,
  resource,
  navKey,
}: {
  tabId: string;
  resource: ReturnType<typeof resourceForLiveTab>;
  navKey: string;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  useBrowserControlStore(s => s.markers);
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.running);
  const marker = resource ? liveOperationMarker(resource.resourceId, { generation: resource.generation }) : null;
  const point = marker?.point ? { x: marker.point.x, y: marker.point.y } : null;
  const state = resource ? presentedControlState(resource) : undefined;
  if (!controlButtonVisible(state) && !point) return null;
  return (
    <div ref={stageRef} className={styles.browserFloatLayer} data-tab={tabId}>
      <ActionCueTravel
        point={point}
        resourceKey={resource?.resourceId || tabId}
        operationKey={marker?.id}
        cancelKey={cueCancelKey({
          resourceId: resource?.resourceId || tabId,
          generation: resource?.generation || 0,
          geometryRevision: marker?.geometry_revision,
          navKey,
        })}
        reducedMotion={prefersCueReducedMotion()}
      />
      <BrowserControlBar resource={resource} compact surface="float" />
    </div>
  );
}

export function HomeButton({ tabId }: { tabId: string }) {
  const { text } = useTranslation();
  const label = text("Home", "主页");

  return (
    <ToolbarTip label={label}>
      <button
        type="button"
        className={webToolbarButton(styles.webToolbarMedium)}
        onClick={() => useCenterTabs.getState().replaceWebTabWithNewTabPage(tabId)}
        aria-label={label}
      >
        <House size={14} />
      </button>
    </ToolbarTip>
  );
}
