"use client";

/** The desktop web tab: a native browser view the main process positions
 *  over this pane's stage, with the toolbar, find bar and resource control. */
import { useEffect, useRef, useState } from "react";
import { canvasPanesMoving, onCanvasMotionChange } from "@/lib/tabs/canvas/canvas-motion";
import type { KeyboardEvent } from "react";
import { ArrowLeft, ArrowRight, ChevronDown, ChevronUp, Crop, ExternalLink, MessageSquarePlus, RotateCw, Search, X } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { destroyStaleWebViews, ensureWebView, installDesktopMenuHandlers, registerVisibleWebTabBounds, removeVisibleWebTabBounds, setWebTabReady, type DesktopBridge } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { appendToComposer } from "@/components/chat/messages/editing/quote-to-chat";
import { pageCommentText, pickedElementName, type PickedElement } from "@/lib/browser/capture/page-comment";
import { regionFile } from "@/lib/browser/capture/region-shot";
import { queueComposerFiles } from "@/lib/chat/composer-inbox";
import { targetChat as targetChatFor } from "@/lib/chat/resources/target-chat";
import { browserPageShortcut } from "@/lib/browser/browser-layout";
import { normalizeWebUrl, useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { collapseWebTabToPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { browserTakeoverKind, presentedControlState, revealPendingApproval, setControlNotice, toggleShowActions } from "@/lib/browser/control/browser-control";
import { useBrowserResourceStore } from "@/lib/chat/resources/session-resources";
import { isMovedByAnimation, isWebTabOccluded, measureWebTabBounds, measureWebTabCorners } from "@/lib/browser/web-tab/web-tab-bounds";
import styles from "../browser.module.css";
import { webToolbarButton } from "../toolbar/toolbar-button";
import { BookmarksLibraryButton, BrowserMenu } from "../toolbar/browser-controls";
import { ViewSourceButton } from "../toolbar/view-source-button";
import { BookmarkBar } from "../bookmark-bar";
import { AddressField, ToolbarTip } from "../toolbar/browser-chrome";
import { controlSurfaceVisible } from "@/lib/browser/control/browser-action-cue";
import { operationHistory, resumeErrorFor, showActionsEnabled, useBrowserControlStore } from "@/lib/browser/control/browser-control";
import { BookmarkButton, findButton, resourceForLiveTab, usePipCollapseTarget, CollapseToPipButton, WebPaneStage, HomeButton } from "./pane-controls";

export function DesktopWebTabPane({
  suspended,
  bridge,
  tabId,
  url,
  menuOwnerId,
}: {
  suspended: boolean;
  bridge: DesktopBridge;
  tabId: string;
  url: string;
  menuOwnerId: string;
}) {
  const { text } = useTranslation();
  const updateWebTab = useCenterTabs((s) => s.updateWebTab);
  const title = useCenterTabs((s) => s.tabs.find((tab) => tab.id === tabId)?.title || url);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.pending);
  useBrowserControlStore(s => s.resumeError);
  useBrowserControlStore(s => s.history);
  useBrowserControlStore(s => s.running);
  const connected = useBrowserResourceStore(s => s.connected);
  const control = resourceForLiveTab(tabId);
  useEffect(() => {
    const setOverlay = bridge.webTab.setControlOverlay;
    if (!setOverlay) return;
    if (!control || !controlSurfaceVisible(presentedControlState(control))) {
      setOverlay(tabId, null);
      return;
    }
    const state = presentedControlState(control);
    const takeoverKind = browserTakeoverKind(state);
    const pauseLabel = takeoverKind === "reveal"
      ? text("Review request", "查看请求")
      : text("I will operate", "我来操作");
    const status = state === "waiting" ? text("Needs your confirmation", "需要你确认")
      : state === "unknown" ? (connected
        ? text("Could not confirm status", "无法确认状态")
        : text("Connection lost", "连接已断开"))
      : text("Active", "活动中");
    const history = operationHistory(control.resourceId);
    const historyItems = history.length === 0
      ? [{ id: "empty", label: text("No operations yet.", "尚无操作。"), disabled: true }]
      : history.map(item => ({
        id: item.id,
        label: `${item.action} · ${item.phase}${item.error ? ` · ${item.error}` : ""}`,
        disabled: true,
      }));
    const payload = {
      resourceId: control.resourceId,
      generation: control.generation,
      conversationSessionId: control.conversationSessionId,
      controlState: state,
      showActions: showActionsEnabled(),
      connected: !!connected,
      status,
      pauseLabel,
      showLabel: text("Show actions", "显示操作"),
      historyLabel: text("Operation history", "操作历史"),
      notice: resumeErrorFor(control.resourceId)
        || (state === "unknown" ? status : undefined),
      pauseDisabled: state === "unknown" || !connected,
      resumeDisabled: !connected || state !== "waiting",
      showTakeover: takeoverKind === "reveal",
      expandLabel: text("Show controls", "显示操作按钮"),
      foldLabel: text("Fold", "收起"),
      dismissLabel: text("Dismiss", "关闭"),
      dragLabel: text("Small draggable Agent button. Click to expand. Drag to move.", "可拖动的 Agent 按钮。点击展开。拖动移动。"),
      historyItems,
    };
    setOverlay(tabId, payload);
  }, [bridge, tabId, control, connected, text]);
  useEffect(() => {
    const setOverlay = bridge.webTab.setControlOverlay;
    if (!setOverlay) return;
    return () => { setOverlay(tabId, null); };
  }, [bridge, tabId]);
  useEffect(() => {
    return bridge.webTab.onControlOverlayEvent?.((event) => {
      if (!control) return;
      if (event.type === "stale") {
        setControlNotice(control.resourceId, text("Status changed. Try again", "状态已更新，请重试"));
        return;
      }
      if (event.type !== "pause" && event.type !== "resume" && event.type !== "toggle-show" && event.type !== "reveal") return;
      if (event.id !== control.resourceId && event.id !== tabId) return;
      if (event.generation !== control.generation) {
        setControlNotice(control.resourceId, text("Status changed. Try again", "状态已更新，请重试"));
        return;
      }
      if (event.type === "reveal") revealPendingApproval(control);
      if (event.type === "toggle-show") toggleShowActions();
    }) ?? (() => {});
  }, [bridge, tabId, control, text]);
  // 历史遗留的白屏竞态可能把 store 里的 url 冲成空串；tab id 本身带着
  // 原始 URL（"w:<url>"），空 url 时从 id 找回，老 tab 自愈。
  const effectiveUrl =
    url || (tabId.startsWith("w:") ? tabId.slice(2) : "");
  const [address, setAddress] = useState(effectiveUrl);
  const [loading, setLoading] = useState(false);
  const [canGoBack, setCanGoBack] = useState(false);
  const [canGoForward, setCanGoForward] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findResult, setFindResult] = useState({ activeMatchOrdinal: 0, matches: 0 });
  const canFind = typeof bridge.webTab.find === "function"
    && typeof bridge.webTab.stopFind === "function";
  const canComment = typeof bridge.webTab.pickElement === "function"
    && typeof bridge.webTab.cancelPick === "function";
  // Comment mode: "picking" while the page waits for a click, then the
  // picked element and the comment being written about it.
  const [comment, setComment] = useState<{ picking: true } | { picking: false; element: PickedElement } | null>(null);
  const [commentDraft, setCommentDraft] = useState("");
  const pickSeq = useRef(0);
  const commentRef = useRef<HTMLInputElement>(null);
  const canRegion = typeof bridge.webTab.pickRegion === "function"
    && typeof bridge.webTab.cancelPick === "function";
  // Region mode: true while the page waits for the user to drag a rectangle.
  const [regionPicking, setRegionPicking] = useState(false);
  const canZoom = typeof bridge.webTab.zoom === "function";
  const canPrint = typeof bridge.webTab.print === "function";
  const collapseTarget = usePipCollapseTarget(tabId);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [frozenImage, setFrozenImage] = useState<string | null>(null);
  const [frozen, setFrozen] = useState(false);

  const addressRef = useRef<HTMLInputElement>(null);
  const findRef = useRef<HTMLInputElement>(null);
  // Last URL the native view is known to be at (our navigate calls +
  // onState reports). The store-url effect below only steers the view
  // on real drift, so onState → updateWebTab echoes never re-navigate.
  const viewUrlRef = useRef(effectiveUrl);

  // View lifecycle. Native visibility is published by the bounds effect
  // below as one collection for every mounted web pane. Destroy lives
  // elsewhere — the store subscription installed by
  // installDesktopMenuHandlers destroys views as their tabs close,
  // and the mount-time reconcile sweeps anything that slipped through.
  useEffect(() => {
    installDesktopMenuHandlers();
    destroyStaleWebViews(
      bridge,
      useCenterTabs.getState().tabs.map((t) => t.id),
    );
    ensureWebView(bridge, tabId, viewUrlRef.current);
    // A pane always shows the page at user zoom. Clear any leftover PiP
    // layout zoom here instead of relying on the PiP's unmount cleanup
    // ordering — a stale factor would render the page as a tiny replica.
    bridge.webTab.setPipZoom?.(tabId, null);
    return () => {
      setWebTabReady(tabId, false);
    };
  }, [bridge, tabId]);

  // Bounds: renderer reports the body's viewport-relative CSS px and
  // main converts them to native DIP using the sender's current zoom.
  // Preserve fractional values until main performs the final rounding.
  // Report on mount, resize, and any ancestor scroll.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    // Freezing swaps the native view for a snapshot. Keep the live view on
    // screen until the snapshot exists (or briefly times out), so a drag or
    // a narrow pane never flashes an empty "paused" card first.
    let disposed = false, capturing = false, hidden = false;
    const hide = () => {
      if (disposed || hidden || !capturing) return;
      hidden = true;
      setFrozen(true);
      removeVisibleWebTabBounds(bridge, tabId); setWebTabReady(tabId, false);
    };
    const report = () => {
      // A canvas drag freezes the page whatever its size, so it skips the
      // measure: every frame's restyle would otherwise force a layout here.
      const dragFrozen = canvasPanesMoving() || suspended;
      const bounds = dragFrozen ? null : measureWebTabBounds(el);
      if (!bounds || bounds.width < 320 || bounds.height < 172) {
        if (!capturing) {
          capturing = true;
          const timer = window.setTimeout(hide, 160);
          void Promise.resolve(bridge.webTab.capture?.(tabId, "presentation"))
            .then(image => { if (!disposed && image) setFrozenImage(image); })
            .catch(() => {})
            .finally(() => { window.clearTimeout(timer); hide(); });
        }
        if (hidden) { removeVisibleWebTabBounds(bridge, tabId); setWebTabReady(tabId, false); }
        return;
      }
      capturing = false;
      if (hidden) { hidden = false; setFrozen(false); }
      const occluded = isWebTabOccluded(
        bounds,
        document.querySelectorAll(
          '[role="dialog"], [role="menu"], [role="listbox"], [data-native-view-occluder="true"]',
        ),
      );
      if (occluded || bounds.width <= 0 || bounds.height <= 0) {
        removeVisibleWebTabBounds(bridge, tabId);
        setWebTabReady(tabId, false);
        return;
      }
      registerVisibleWebTabBounds(bridge, tabId, bounds, measureWebTabCorners(el));
      setWebTabReady(tabId, true);
    };
    // While an animation moves the pane (picking a window in the overview
    // zooms its card into place), measure every frame until it stops: its
    // last frame changes neither the DOM nor the pane's size, so nothing
    // else would report the final position and the native view would stay
    // where the animation was mid-way.
    let followFrame = 0;
    const moving = () => isMovedByAnimation(el, document.getAnimations?.() ?? []);
    const follow = () => {
      followFrame = 0;
      report();
      if (moving()) followFrame = window.requestAnimationFrame(follow);
    };
    const reportAndFollow = () => {
      report();
      if (!followFrame && moving()) followFrame = window.requestAnimationFrame(follow);
    };
    reportAndFollow();
    const ro = new ResizeObserver(reportAndFollow);
    ro.observe(el);
    const mo = new MutationObserver(reportAndFollow);
    mo.observe(document.body, { subtree: true, childList: true, attributes: true });
    // A theme switch recolours the card edge the page's corners are painted to match.
    mo.observe(document.documentElement, { attributes: true });
    const offCanvasDrag = onCanvasMotionChange(reportAndFollow);
    window.addEventListener("resize", reportAndFollow);
    window.addEventListener("scroll", reportAndFollow, true);
    return () => {
      disposed = true;
      if (followFrame) window.cancelAnimationFrame(followFrame);
      offCanvasDrag();
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", reportAndFollow);
      window.removeEventListener("scroll", reportAndFollow, true);
      removeVisibleWebTabBounds(bridge, tabId);
    };
  }, [bridge, tabId, suspended]);

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
      // 空串是有意义的（导航到无 favicon 的页面 → 清图标），不能按真值过滤。
      if (state.faviconUrl !== undefined) {
        updateWebTab(tabId, { faviconUrl: state.faviconUrl });
      }
      if (state.loading !== undefined) setLoading(state.loading);
      if (state.canGoBack !== undefined) setCanGoBack(state.canGoBack);
      if (state.canGoForward !== undefined) setCanGoForward(state.canGoForward);
    });
  }, [bridge, tabId, updateWebTab]);

  useEffect(() => bridge.webTab.onFindResult?.((result) => {
    if (result.id === tabId) setFindResult(result);
  }), [bridge, tabId]);

  useEffect(() => bridge.webTab.onCommand?.((command) => {
    if (command.id !== tabId) return;
    if (command.command === "find") {
      setFindOpen(true);
      window.requestAnimationFrame(() => findRef.current?.focus());
    } else if (command.command === "focus-address") {
      focusAddress();
    }
  }), [bridge, tabId]);

  useEffect(() => () => {
    bridge.webTab.stopFind?.(tabId, "clearSelection");
    bridge.webTab.cancelPick?.(tabId);
  }, [bridge, tabId]);

  // A page that navigates away takes its element and region pickers with it.
  useEffect(() => {
    pickSeq.current += 1;
    setComment(null);
    setRegionPicking(false);
  }, [url]);

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

  function navigateTo(nextUrl: string) {
    const normalized = normalizeWebUrl(nextUrl);
    if (!normalized) return;
    setAddress(normalized);
    viewUrlRef.current = normalized;
    bridge.webTab.navigate(tabId, normalized);
    updateWebTab(tabId, { url: normalized });
  }

  function openFind() {
    if (!canFind) return;
    setFindOpen(true);
    window.requestAnimationFrame(() => {
      findRef.current?.focus();
      findRef.current?.select();
    });
  }

  function startComment() {
    if (!canComment) return;
    setRegionPicking(false);
    const seq = ++pickSeq.current;
    setComment({ picking: true });
    setCommentDraft("");
    void bridge.webTab.pickElement?.(tabId).then((element) => {
      if (seq !== pickSeq.current) return;
      if (!element) {
        setComment(null);
        return;
      }
      setComment({ picking: false, element });
      window.requestAnimationFrame(() => commentRef.current?.focus());
    });
  }

  function closeComment() {
    pickSeq.current += 1;
    bridge.webTab.cancelPick?.(tabId);
    setComment(null);
    setCommentDraft("");
  }

  /** The conversation a comment or screenshot from this page goes to, and its title. */
  const targetChat = () => targetChatFor(tabId);

  function startRegion() {
    if (!canRegion) return;
    if (comment) closeComment();
    if (!targetChat()) {
      showToast(text("Open a conversation to add the screenshot to", "先打开一个对话，再添加截图"));
      return;
    }
    const seq = ++pickSeq.current;
    setRegionPicking(true);
    void bridge.webTab.pickRegion?.(tabId).then(async (region) => {
      if (seq !== pickSeq.current) return;
      setRegionPicking(false);
      if (!region) return;
      const chat = targetChat();
      if (!chat) {
        showToast(text("Open a conversation to add the screenshot to", "先打开一个对话，再添加截图"));
        return;
      }
      queueComposerFiles(chat.key, [await regionFile(region)]);
      showToast(chat.title
        ? text(`Region screenshot added to the message in “${chat.title}”`, `区域截图已加入“${chat.title}”的消息`)
        : text("Region screenshot added to your message", "区域截图已加入消息"));
    });
  }

  function cancelRegion() {
    pickSeq.current += 1;
    bridge.webTab.cancelPick?.(tabId);
    setRegionPicking(false);
  }

  function submitComment() {
    if (!comment || comment.picking || !commentDraft.trim()) return;
    const chat = targetChat();
    if (!chat) {
      showToast(text("Open a conversation to add the comment to", "先打开一个对话，再添加评论"));
      return;
    }
    const chatKey = chat.key;
    appendToComposer(chatKey, pageCommentText(comment.element, commentDraft), false);
    const title = chat.title;
    showToast(title
      ? text(`Comment added to the message in “${title}”`, `评论已加入“${title}”的消息`)
      : text("Comment added to your message", "评论已加入消息"));
    closeComment();
  }

  function focusAddress() {
    window.requestAnimationFrame(() => {
      addressRef.current?.focus();
      addressRef.current?.select();
    });
  }

  function closeFind() {
    bridge.webTab.stopFind?.(tabId, "clearSelection");
    setFindOpen(false);
    setFindResult({ activeMatchOrdinal: 0, matches: 0 });
  }

  function runFind(nextQuery: string, forward = true, findNext = true) {
    setFindQuery(nextQuery);
    if (!nextQuery) {
      bridge.webTab.stopFind?.(tabId, "clearSelection");
      setFindResult({ activeMatchOrdinal: 0, matches: 0 });
      return;
    }
    bridge.webTab.find?.(tabId, nextQuery, { forward, findNext });
  }

  function handleRendererShortcut(event: KeyboardEvent<HTMLDivElement>) {
    const action = browserPageShortcut(event);
    if (!action) return;
    if (action === "find" && !canFind) return;
    if ((action === "zoom-in" || action === "zoom-out" || action === "reset-zoom") && !canZoom) return;
    if (action === "print" && !canPrint) return;
    if (action === "back" && !canGoBack) return;
    if (action === "forward" && !canGoForward) return;
    event.preventDefault();
    if (action === "find") openFind();
    if (action === "focus-address") focusAddress();
    if (action === "back") bridge.webTab.goBack(tabId);
    if (action === "forward") bridge.webTab.goForward(tabId);
    if (action === "zoom-in") void bridge.webTab.zoom?.(tabId, "in");
    if (action === "zoom-out") void bridge.webTab.zoom?.(tabId, "out");
    if (action === "reset-zoom") void bridge.webTab.zoom?.(tabId, "reset");
    if (action === "print") void bridge.webTab.print?.(tabId);
  }

  return (
    <div
      className={styles.webPane}
      data-state={control ? presentedControlState(control) : undefined}
      onKeyDownCapture={handleRendererShortcut}
    >
      <div className={`${styles.webChrome} relative flex flex-col shrink-0`}>
      <div className={styles.webToolbar}>
        <ToolbarTip label={text("Back", "后退")} shortcut={{ key: "[" }}>
          <button
            type="button"
            className={webToolbarButton()}
            onClick={() => {
              bridge.webTab.goBack(tabId);
            }}
            disabled={!canGoBack}
            aria-label={text("Back", "后退")}
          >
            <ArrowLeft size={14} />
          </button>
        </ToolbarTip>
        <ToolbarTip label={text("Forward", "前进")} shortcut={{ key: "]" }}>
          <button
            type="button"
            className={webToolbarButton(styles.webToolbarForward)}
            onClick={() => {
              bridge.webTab.goForward(tabId);
            }}
            disabled={!canGoForward}
            aria-label={text("Forward", "前进")}
          >
            <ArrowRight size={14} />
          </button>
        </ToolbarTip>
        <ToolbarTip
          label={loading ? text("Stop loading", "停止加载") : text("Reload", "重新加载")}
          shortcut={loading ? undefined : { key: "R" }}
        >
          <button
            type="button"
            className={webToolbarButton(loading && styles.webStopLoading)}
            onClick={() => {
              if (loading) bridge.webTab.stop(tabId);
              else {
                bridge.webTab.reload(tabId);
              }
            }}
            aria-label={loading ? text("Stop loading", "停止加载") : text("Reload", "重新加载")}
          >
            {loading ? <X size={14} /> : <RotateCw size={14} />}
          </button>
        </ToolbarTip>
        <HomeButton tabId={tabId} />
        <AddressField
          inputRef={addressRef}
          value={address}
          onChange={setAddress}
          onSubmit={navigate}
          ariaLabel={text("Address", "地址")}
        >
          <BookmarkButton tabId={tabId} url={effectiveUrl} title={title || effectiveUrl} />
        </AddressField>
        <ViewSourceButton tabId={tabId} url={effectiveUrl} />
        <BookmarksLibraryButton />
        <ToolbarTip label={text("Open in browser", "在浏览器中打开")}>
          <button
            type="button"
            className={webToolbarButton(styles.webToolbarMedium)}
            onClick={() => bridge.openExternal(viewUrlRef.current)}
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
            forward: () => {
              bridge.webTab.goForward(tabId);
            },
            openExternal: () => bridge.openExternal(viewUrlRef.current),
            find: canFind ? openFind : undefined,
            comment: canComment ? startComment : undefined,
            regionShot: canRegion ? startRegion : undefined,
            zoomIn: canZoom ? () => { void bridge.webTab.zoom?.(tabId, "in"); } : undefined,
            zoomOut: canZoom ? () => { void bridge.webTab.zoom?.(tabId, "out"); } : undefined,
            resetZoom: canZoom ? () => { void bridge.webTab.zoom?.(tabId, "reset"); } : undefined,
            print: canPrint ? () => { void bridge.webTab.print?.(tabId); } : undefined,
            collapseToPip: collapseTarget
              ? () => { collapseWebTabToPip(tabId); }
              : undefined,
          }}
          canGoForward={canGoForward}
        />
      </div>
      <BookmarkBar ownerId={menuOwnerId} onNavigate={navigateTo} />
      {loading && (
        <div
          className={styles.webLoadingProgress}
          role="status"
          aria-label={text("Loading page", "正在加载网页")}
        />
      )}
      </div>
      {findOpen ? (
        <div className={styles.webFindBar}>
          <div className={styles.webFindPill}>
          <Search size={12} aria-hidden="true" />
          <input
            ref={findRef}
            value={findQuery}
            onChange={(event) => runFind(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeFind();
              if (event.key === "Enter" && findQuery) {
                runFind(findQuery, !event.shiftKey, false);
              }
            }}
            placeholder={text("Find in page", "在页面中查找")}
            aria-label={text("Find in page", "在页面中查找")}
          />
          <span className={styles.webFindCount} aria-live="polite">
            {findResult.matches > 0 ? `${findResult.activeMatchOrdinal} / ${findResult.matches}` : "0 / 0"}
          </span>
          <span className={styles.webFindSep} aria-hidden="true" />
          <button type="button" className={findButton()} onClick={() => runFind(findQuery, false, false)} disabled={!findQuery} aria-label={text("Previous match", "上一个匹配项")}>
            <ChevronUp size={14} />
          </button>
          <button type="button" className={findButton()} onClick={() => runFind(findQuery, true, false)} disabled={!findQuery} aria-label={text("Next match", "下一个匹配项")}>
            <ChevronDown size={14} />
          </button>
          <button type="button" className={findButton()} onClick={closeFind} aria-label={text("Close find", "关闭查找")}>
            <X size={14} />
          </button>
          </div>
        </div>
      ) : null}
      {regionPicking ? (
        <div className={styles.webFindBar}>
          <div className={styles.webFindPill}>
            <Crop size={12} aria-hidden="true" />
            <span className={styles.webCommentHint} aria-live="polite">
              {text("Drag over the area to capture · Esc to cancel", "拖动选出要截的区域 · Esc 取消")}
            </span>
            <button type="button" className={findButton()} onClick={cancelRegion} aria-label={text("Cancel region screenshot", "取消区域截图")}>
              <X size={14} />
            </button>
          </div>
        </div>
      ) : null}
      {comment ? (
        <div className={styles.webFindBar}>
          <form
            className={styles.webFindPill}
            onSubmit={(event) => {
              event.preventDefault();
              submitComment();
            }}
          >
            <MessageSquarePlus size={12} aria-hidden="true" />
            {comment.picking ? (
              <span className={styles.webCommentHint} aria-live="polite">
                {text("Click an element on the page · Esc to cancel", "点击页面上的元素 · Esc 取消")}
              </span>
            ) : (
              <>
                <span className={styles.webCommentTarget}>{pickedElementName(comment.element)}</span>
                <input
                  ref={commentRef}
                  value={commentDraft}
                  onChange={(event) => setCommentDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) return;
                    if (event.key === "Escape") {
                      event.preventDefault();
                      closeComment();
                    }
                  }}
                  placeholder={text("Comment for the agent", "给 agent 的评论")}
                  aria-label={text("Comment on this element", "评论这个元素")}
                />
                <span className={styles.webFindSep} aria-hidden="true" />
                <button type="submit" className={buttonVariants({ variant: "ghost", size: "xs" })} disabled={!commentDraft.trim()}>
                  {text("Add to message", "加入消息")}
                </button>
              </>
            )}
            <button type="button" className={findButton()} onClick={closeComment} aria-label={text("Close comment", "关闭评论")}>
              <X size={14} />
            </button>
          </form>
        </div>
      ) : null}
      <WebPaneStage>
      <div
        ref={bodyRef}
        className={styles.webFrame}
        data-web-frame={tabId}
        style={{ position: "relative", overflow: "hidden" }}
      >
        {frozen ? <button type="button" onClick={() => useCenterTabs.getState().setActive(tabId)} style={{ position:"absolute",inset:0,border:0,padding:0,background:"var(--bg-primary)",color:"var(--text-secondary)",cursor:"pointer" }}>
          {frozenImage ? <img src={frozenImage} alt={text("Frozen webpage", "网页快照")} style={{ width:"100%",height:"100%",objectFit:"cover",objectPosition:"top left" }} /> : text("Webpage paused — select to activate", "网页已暂停，点击激活")}
        </button> : null}
      </div>
      </WebPaneStage>
    </div>
  );
}
