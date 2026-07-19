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
 *    empty body div whose viewport rect is reported to
 *    webTab.setBounds; show/hide follows mount/unmount (only the
 *    active tab's pane is mounted). Real back/forward/reload, no
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
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Columns2, ExternalLink, RotateCw, Star } from "lucide-react";

import {
  desktopBridge,
  destroyStaleWebViews,
  ensureWebView,
  installDesktopMenuHandlers,
  setWebTabReady,
  type DesktopBridge,
} from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import {
  isBookmarked,
  subscribeBookmarks,
  toggleBookmark,
} from "@/lib/bookmarks";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import { useSessionStore } from "@/lib/session-store";
import styles from "./center-tabs.module.css";

export function WebTabPane({ tabId, url }: { tabId: string; url: string }) {
  // Bridge presence is fixed for the lifetime of the page (preload
  // ran or it didn't), so branching in render is stable. Web tabs
  // never server-render (the tabs store is empty during SSR).
  const bridge = desktopBridge();
  if (bridge) {
    return <DesktopWebTabPane bridge={bridge} tabId={tabId} url={url} />;
  }
  return <IframeWebTabPane tabId={tabId} url={url} />;
}

function BookmarkButton({ url, title }: { url: string; title: string }) {
  const { text } = useTranslation();
  const [bookmarked, setBookmarked] = useState(() => isBookmarked(url));

  useEffect(() => {
    const refresh = () => setBookmarked(isBookmarked(url));
    refresh();
    return subscribeBookmarks(refresh);
  }, [url]);

  return (
    <button
      type="button"
      className={styles.webToolbarBtn}
      onClick={() => toggleBookmark({ url, title })}
      title={text(bookmarked ? "Remove bookmark" : "Bookmark", bookmarked ? "移除书签" : "添加书签")}
      aria-label={text(bookmarked ? "Remove bookmark" : "Bookmark", bookmarked ? "移除书签" : "添加书签")}
    >
      <Star size={14} fill={bookmarked ? "currentColor" : "none"} />
    </button>
  );
}

function SplitButton({ tabId }: { tabId: string }) {
  const { text } = useTranslation();
  const splitPinned = useCenterTabs((s) => s.splitWebTabId === tabId);
  const label = splitPinned
    ? text("Exit split view", "退出分屏")
    : text("Open split view", "打开分屏");

  function toggleSplit() {
    const state = useCenterTabs.getState();
    if (splitPinned) {
      state.setSplitWebTab(null);
      return;
    }
    const routeSessionId = window.location.pathname.startsWith("/s/")
      ? decodeURIComponent(window.location.pathname.slice(3))
      : null;
    const sessionState = useSessionStore.getState();
    const routeSession = routeSessionId
      ? state.tabs.find(
          (tab) => tab.kind === "session" && tab.sessionId === routeSessionId,
        )
      : undefined;
    const activeDraft = !routeSessionId
      ? state.tabs.find(
          (tab) =>
            tab.kind === "session" &&
            tab.draft &&
            tab.sessionId === sessionState.activeChatKey,
        )
      : undefined;
    state.setSplitWebTab(tabId);
    sessionState.setRightDockOpen(false);
    if (routeSession) {
      state.setActive(routeSession.id);
    } else if (routeSessionId) {
      const title = sessionState.conversations[routeSessionId]?.title ?? "";
      state.openSessionTab(routeSessionId, title);
      const openedState = useCenterTabs.getState();
      const openedSession = openedState.tabs.find(
        (tab) => tab.kind === "session" && tab.sessionId === routeSessionId,
      );
      if (openedSession) openedState.setActive(openedSession.id);
    } else if (activeDraft) {
      state.setActive(activeDraft.id);
    } else {
      const draftId = state.openDraftSessionTab();
      (window as unknown as { newSession?: (id?: string) => void })
        .newSession?.(draftId);
    }
  }

  return (
    <button
      type="button"
      className={styles.webToolbarBtn}
      onClick={toggleSplit}
      title={label}
      aria-label={label}
    >
      <Columns2 size={14} />
    </button>
  );
}

/* ---- Desktop shell: native WebContentsView ------------------------- */

function DesktopWebTabPane({
  bridge,
  tabId,
  url,
}: {
  bridge: DesktopBridge;
  tabId: string;
  url: string;
}) {
  const { text } = useTranslation();
  const updateWebTab = useCenterTabs((s) => s.updateWebTab);
  const title = useCenterTabs((s) => s.tabs.find((tab) => tab.id === tabId)?.title || url);
  // 历史遗留的白屏竞态可能把 store 里的 url 冲成空串；tab id 本身带着
  // 原始 URL（"w:<url>"），空 url 时从 id 找回，老 tab 自愈。
  const effectiveUrl =
    url || (tabId.startsWith("w:") ? tabId.slice(2) : "");
  const [address, setAddress] = useState(effectiveUrl);
  const [loading, setLoading] = useState(false);
  const [canGoBack, setCanGoBack] = useState(false);
  const [canGoForward, setCanGoForward] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const addressRef = useRef<HTMLInputElement>(null);
  // Last URL the native view is known to be at (our navigate calls +
  // onState reports). The store-url effect below only steers the view
  // on real drift, so onState → updateWebTab echoes never re-navigate.
  const viewUrlRef = useRef(effectiveUrl);

  // View lifecycle. Visibility model: this pane is mounted only while
  // its tab is active, so show on mount / hide on unmount. destroy
  // lives elsewhere — the store subscription installed by
  // installDesktopMenuHandlers destroys views as their tabs close,
  // and the mount-time reconcile sweeps anything that slipped through.
  useEffect(() => {
    installDesktopMenuHandlers();
    destroyStaleWebViews(
      bridge,
      useCenterTabs.getState().tabs.map((t) => t.id),
    );
    ensureWebView(bridge, tabId, viewUrlRef.current);
    bridge.webTab.show(tabId);
    return () => {
      setWebTabReady(tabId, false);
      bridge.webTab.hide(tabId);
    };
  }, [bridge, tabId]);

  // Bounds: main positions the native view from the DIP rect of the
  // body div. getBoundingClientRect is viewport-relative CSS px ==
  // DIP relative to the window content area. Report on mount, when
  // the div resizes, and on window resize / any ancestor scroll.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const report = () => {
      const r = el.getBoundingClientRect();
      const roundedBounds = {
        x: Math.round(r.left),
        y: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
      const occluded = !!document.querySelector(
        '[role="dialog"], .branches-merge-modal-backdrop, [data-native-view-occluder="true"]',
      );
      if (occluded || roundedBounds.width <= 0 || roundedBounds.height <= 0) {
        bridge.webTab.setBounds(tabId, { x: 0, y: 0, width: 0, height: 0 });
        setWebTabReady(tabId, false);
        return;
      }
      bridge.webTab.setBounds(tabId, roundedBounds);
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
    };
  }, [bridge, tabId]);

  // Main → renderer state: address bar (unless the user is typing in
  // it), tab title, loading spinner, history-button enablement. URL
  // changes also flow into the store so the tab persists (and later
  // remounts restore) the page actually being viewed.
  useEffect(() => {
    return bridge.webTab.onState((state) => {
      if (state.id !== tabId) return;
      // 空串一律忽略（加载初期 getURL/getTitle 为空；写进 store 会把
      // url 冲空、面板被 app-shell 卸载 → 白屏）。主进程侧已过滤，这里
      // 是第二道保险。
      if (state.url) {
        viewUrlRef.current = state.url;
        if (document.activeElement !== addressRef.current) {
          setAddress(state.url);
        }
        updateWebTab(tabId, { url: state.url });
      }
      if (state.title) updateWebTab(tabId, { title: state.title });
      if (state.loading !== undefined) setLoading(state.loading);
      if (state.canGoBack !== undefined) setCanGoBack(state.canGoBack);
      if (state.canGoForward !== undefined) setCanGoForward(state.canGoForward);
    });
  }, [bridge, tabId, updateWebTab]);

  // Store url changed by someone else (session restore, future agent
  // navigation) → steer the view. Our own updates (navigate() below,
  // onState echoes) already advanced viewUrlRef, so they no-op here.
  useEffect(() => {
    if (!url || url === viewUrlRef.current) return; // 空串不算漂移
    viewUrlRef.current = url;
    bridge.webTab.navigate(tabId, url);
    if (document.activeElement !== addressRef.current) setAddress(url);
  }, [bridge, tabId, url]);

  function navigate() {
    const normalized = normalizeWebUrl(address);
    if (!normalized) {
      setAddress(viewUrlRef.current); // invalid input → snap back
      return;
    }
    setAddress(normalized);
    if (normalized === viewUrlRef.current) {
      bridge.webTab.reload(tabId); // same URL → treat Enter as reload
    } else {
      viewUrlRef.current = normalized;
      bridge.webTab.navigate(tabId, normalized);
      updateWebTab(tabId, { url: normalized });
    }
  }

  const disabledStyle = { opacity: 0.35, cursor: "default" } as const;

  return (
    <div className={styles.webPane}>
      <div className={styles.webToolbar}>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => bridge.webTab.goBack(tabId)}
          disabled={!canGoBack}
          style={canGoBack ? undefined : disabledStyle}
          title={text("Back", "后退")}
        >
          <ArrowLeft size={14} />
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => bridge.webTab.goForward(tabId)}
          disabled={!canGoForward}
          style={canGoForward ? undefined : disabledStyle}
          title={text("Forward", "前进")}
        >
          <ArrowRight size={14} />
        </button>
        <input
          ref={addressRef}
          className={styles.webAddress}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") navigate();
          }}
          spellCheck={false}
          autoComplete="off"
          aria-label={text("Address", "地址")}
        />
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => bridge.webTab.reload(tabId)}
          title={text("Reload", "重新加载")}
        >
          <RotateCw
            size={14}
            style={
              loading ? { animation: "opWebTabSpin 0.8s linear infinite" } : undefined
            }
          />
        </button>
        <BookmarkButton url={effectiveUrl} title={title || effectiveUrl} />
        <SplitButton tabId={tabId} />
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => bridge.openExternal(viewUrlRef.current)}
          title={text("Open in browser", "在浏览器中打开")}
        >
          <ExternalLink size={14} />
        </button>
      </div>
      <style>{`@keyframes opWebTabSpin { to { transform: rotate(360deg); } }`}</style>
      {/* Empty body — the native view is drawn here by the main
          process at the bounds reported above. */}
      <div ref={bodyRef} className={styles.webFrame} />
    </div>
  );
}

/* ---- Browser fallback: sandboxed iframe ---------------------------- */

function IframeWebTabPane({ tabId, url }: { tabId: string; url: string }) {
  const { text } = useTranslation();
  const updateWebTab = useCenterTabs((s) => s.updateWebTab);
  const title = useCenterTabs((s) => s.tabs.find((tab) => tab.id === tabId)?.title || url);
  const [address, setAddress] = useState(url);
  // Bumping remounts the iframe — that's the reload button.
  const [frameEpoch, setFrameEpoch] = useState(0);

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

  return (
    <div className={styles.webPane}>
      <div className={styles.webToolbar}>
        <input
          className={styles.webAddress}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") navigate();
          }}
          spellCheck={false}
          autoComplete="off"
          aria-label={text("Address", "地址")}
        />
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => setFrameEpoch((e) => e + 1)}
          title={text("Reload", "重新加载")}
        >
          <RotateCw size={14} />
        </button>
        <BookmarkButton url={url} title={title} />
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={openExternal}
          title={text("Open in browser", "在浏览器中打开")}
        >
          <ExternalLink size={14} />
        </button>
      </div>
      <div className={styles.webHint}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
          {text(
            "If the page stays blank, the site refuses embedding — open it externally.",
            "页面空白说明该站点拒绝内嵌，请点右上角外部打开。",
          )}
        </span>
        <button type="button" className={styles.webHintLink} onClick={openExternal}>
          <ExternalLink size={11} />
          {text("Open externally", "外部打开")}
        </button>
      </div>
      <iframe
        key={frameEpoch}
        className={styles.webFrame}
        src={url}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        referrerPolicy="no-referrer"
        title={text("Web page", "网页")}
      />
    </div>
  );
}
