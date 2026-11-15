"use client";

/**
 * WebTabPane — center-column content of a web tab (kind "web").
 *
 * Two rendering backends behind one tab contract ({url, title} in the
 * center-tabs store):
 *
 *  - Desktop shell (window.openprogramDesktop present): a native
 *    Electron WebContentsView positioned by the main process over the
 *    body area of this pane. The pane renders only the toolbar plus an
 *    empty body div whose viewport rect joins the renderer's complete
 *    visible-bounds collection. Real back/forward/reload, no
 *    framing restrictions, so no hint bar.
 *
 *  - Browser fallback: a 40px address toolbar (mono URL input · reload
 *    · open-external) over a sandboxed <iframe>, with a persistent
 *    slim hint bar in between — many sites refuse framing
 *    (X-Frame-Options / CSP frame-ancestors) and a cross-origin load
 *    failure is not reliably detectable from here, so the
 *    open-externally escape hatch stays always visible. No
 *    back/forward buttons: iframe history is unreliable cross-origin.
 */
import { memo, useId } from "react";

import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { DesktopWebTabPane } from "./pane/desktop-web-tab-pane";
import { IframeWebTabPane } from "./pane/iframe-web-tab-pane";

export const WebTabPane = memo(function WebTabPane({ tabId, url, suspended = false }: { tabId: string; url: string; suspended?: boolean }) {
  // Bridge presence is fixed for the lifetime of the page (preload
  // ran or it didn't), so branching in render is stable. Web tabs
  // never server-render (the tabs store is empty during SSR).
  const bridge = desktopBridge();
  const menuOwnerId = useId();
  if (bridge) {
    return <DesktopWebTabPane bridge={bridge} tabId={tabId} url={url} menuOwnerId={menuOwnerId} suspended={suspended} />;
  }
  return <IframeWebTabPane tabId={tabId} url={url} menuOwnerId={menuOwnerId} />;
});
