"use client";

/** The browser fallback: the page in a sandboxed iframe. */
import { useEffect, useState } from "react";
import { ExternalLink, RotateCw } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { normalizeWebUrl, useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { collapseWebTabToPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { presentedControlState, useBrowserControlStore } from "@/lib/browser/control/browser-control";
import { useBrowserResourceStore } from "@/lib/chat/resources/session-resources";
import styles from "../browser.module.css";
import { webToolbarButton } from "../toolbar/toolbar-button";
import { BookmarksLibraryButton, BrowserMenu } from "../toolbar/browser-controls";
import { BookmarkBar } from "../bookmark-bar";
import { AddressField, ToolbarTip } from "../toolbar/browser-chrome";
import { BookmarkButton, resourceForLiveTab, usePipCollapseTarget, CollapseToPipButton, WebPaneStage, IframeAgentLayer, HomeButton } from "./pane-controls";

export function IframeWebTabPane({ tabId, url, menuOwnerId }: { tabId: string; url: string; menuOwnerId: string }) {
  const { text } = useTranslation();
  const updateWebTab = useCenterTabs((s) => s.updateWebTab);
  const title = useCenterTabs((s) => s.tabs.find((tab) => tab.id === tabId)?.title || url);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserControlStore(s => s.running);
  const control = resourceForLiveTab(tabId);
  const [address, setAddress] = useState(url);
  // Bumping remounts the iframe — that's the reload button.
  const [frameEpoch, setFrameEpoch] = useState(0);
  const collapseTarget = usePipCollapseTarget(tabId);

  // Store url changed elsewhere (restore, future agent navigation) →
  // resync the address bar.
  useEffect(() => setAddress(url), [url]);

  function navigate() {
    const normalized = normalizeWebUrl(address);
    if (!normalized) {
      setAddress(url); // invalid input → snap back to the real URL
      return;
    }
    setAddress(normalized);
    if (normalized === url) {
      setFrameEpoch((e) => e + 1); // same URL → treat Enter as reload
    } else {
      updateWebTab(tabId, { url: normalized });
    }
  }

  function openExternal() {
    window.open(url, "_blank", "noopener");
  }

  function navigateTo(nextUrl: string) {
    const normalized = normalizeWebUrl(nextUrl);
    if (!normalized) return;
    setAddress(normalized);
    updateWebTab(tabId, { url: normalized });
  }

  return (
    <div
      className={styles.webPane}
      data-state={control ? presentedControlState(control) : undefined}
    >
      <div className={`${styles.webChrome} relative flex flex-col shrink-0`}>
      <div className={styles.webToolbar}>
        <ToolbarTip label={text("Reload", "重新加载")}>
          <button
            type="button"
            className={webToolbarButton()}
            onClick={() => {
              setFrameEpoch((e) => e + 1);
            }}
            aria-label={text("Reload", "重新加载")}
          >
            <RotateCw size={14} />
          </button>
        </ToolbarTip>
        <HomeButton tabId={tabId} />
        <AddressField
          value={address}
          onChange={setAddress}
          onSubmit={navigate}
          ariaLabel={text("Address", "地址")}
        >
          <BookmarkButton tabId={tabId} url={url} title={title} />
        </AddressField>
        <BookmarksLibraryButton />
        <ToolbarTip label={text("Open in browser", "在浏览器中打开")}>
          <button
            type="button"
            className={webToolbarButton(styles.webToolbarMedium)}
            onClick={openExternal}
            aria-label={text("Open in browser", "在浏览器中打开")}
          >
            <ExternalLink size={14} />
          </button>
        </ToolbarTip>
        <CollapseToPipButton tabId={tabId} />
        <BrowserMenu
          ownerId={menuOwnerId}
          actions={{
            home: () => useCenterTabs.getState().replaceWebTabWithNewTabPage(tabId),
            openExternal,
            collapseToPip: collapseTarget
              ? () => { collapseWebTabToPip(tabId); }
              : undefined,
          }}
        />
      </div>
      <BookmarkBar ownerId={menuOwnerId} onNavigate={navigateTo} />
      </div>
      {url.startsWith("file:") ? (
        /* Browsers silently block file:// in iframes — say so instead of
           showing a blank frame. */
        <WebPaneStage>
        <div
          className={styles.webFrame}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-secondary)",
            fontSize: "var(--fs-sm)",
            padding: "24px",
            textAlign: "center",
          }}
        >
          {text(
            "Local files can only be opened in the desktop app.",
            "本地文件仅能在桌面应用中打开。",
          )}
        </div>
        </WebPaneStage>
      ) : (
        <>
          <div className={styles.webHint}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {text(
                "If the page stays blank, the site refuses embedding — open it externally.",
                "页面空白说明该站点拒绝内嵌，请点右上角外部打开。",
              )}
            </span>
            <Button type="button" variant="link" size="xs" className="shrink-0" onClick={openExternal}>
              <ExternalLink />
              {text("Open externally", "外部打开")}
            </Button>
          </div>
          <WebPaneStage>
          <iframe
            key={frameEpoch}
            className={styles.webFrame}
            src={url}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            referrerPolicy="no-referrer"
            title={text("Web page", "网页")}
          />
          <IframeAgentLayer tabId={tabId} resource={control} navKey={`${url}:${frameEpoch}`} />
          </WebPaneStage>
        </>
      )}
    </div>
  );
}
