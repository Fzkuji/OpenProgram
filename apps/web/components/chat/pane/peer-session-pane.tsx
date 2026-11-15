"use client";
import { requestSessionLoad } from "@/lib/runtime-bridge/session/session-load";
import { SelectionQuote } from "../messages/editing/quote-to-chat";

/**
 * One session pane in a split view.
 *
 * In a split view BOTH panes render this — they are symmetric. Neither is
 * the legacy `#chatView` shell: that shell is a singleton (hardcoded
 * `#chatArea` / `#chatMessages` ids read by ~10 modules under
 * `lib/runtime-bridge/`), so it can't be mounted twice. AppShell hides it
 * entirely while split, and each pane renders pure React instead: the same
 * `TranscriptRows` the main chat draws, plus a full `<Composer sessionId=… />`.
 *
 * Both panes are always live. Each composer owns its session's draft,
 * settings and run state, and sends with `background: true`, so typing in
 * one never disturbs the other. There is no click-to-activate and no
 * position swapping.
 *
 * "Focus" here is only bookkeeping — which session the URL, tab highlight,
 * right rail and DAG follow. Interacting with a pane sets it silently
 * (`setActive`), which changes no layout and interrupts no input.
 */
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";

import { selectRow, selectViewRows, useMessageIds, useSessionStore } from "@/lib/session-store";
import { HISTORY_PROTOCOL, holdsHistoryWindow, useSessionHistory } from "@/lib/chat/history/session-history";
import { COMPOSER_TUCK_SHIFT_EVENT, visibleComposerHeight } from "@/lib/chat/history/chat-scroll";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  RECYCLE_MIN_ROWS,
  collectAlwaysLive,
  decideLiveRows,
  foldKey,
  heightsFor,
  setRowHeight,
  type WindowNode,
} from "@/lib/chat/history/message-window";
import { TranscriptReadStatus } from "../messages/list/transcript-read-status";
import { Composer } from "../composer";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { useTranslation } from "@/lib/i18n";

import { useChatAreaStick } from "../messages/list/use-chat-area-stick";
import { useMessageViewport } from "../messages/list/use-message-viewport";
import { useHistoryWindow } from "../messages/list/use-history-window";
import { DecisionOutputs } from "../messages/runtime/decision-output";
import { TranscriptRows, coveringOf, useOriginalsToggle, usePlacedDecisions } from "../messages/list/transcript-rows";
import { BotViewContext } from "../messages/list/transcript-view-scope";
import { timeBreaks } from "@/lib/chat/transcript/bot-view";
import { useBotView } from "@/lib/chat/transcript/use-bot-view";

export const PeerSessionPane = memo(function PeerSessionPane({
  tabId,
  sessionId,
  title,
  showTitle = true,
}: {
  tabId: string;
  sessionId: string | null;
  title: string;
  showTitle?: boolean;
}) {
  const { text } = useTranslation();
  const setActive = useCenterTabs((s) => s.setActive);
  const activeId = useCenterTabs((s) => s.activeId);
  const ids = useMessageIds(sessionId);
  const readStatus = useSessionStore(s => sessionId ? s.transcriptReadStatus[sessionId] : undefined);
  const historyLoaded = useSessionHistory(s => Boolean(sessionId && s.pages[sessionId]));
  const areaRef = useRef<HTMLDivElement | null>(null);
  const scrollKey = sessionId ? `peer:${sessionId}` : null;
  useHistoryWindow(sessionId, true, areaRef, scrollKey);

  // Interacting with a pane makes it the focused one for bookkeeping
  // purposes (URL, tab highlight, right rail, DAG). Silent: no layout
  // change, no swap, and the other pane's composer keeps its state and
  // focus. Skipped when already focused so typing doesn't churn the store.
  const claimFocus = useCallback(() => {
    if (activeId !== tabId) setActive(tabId);
  }, [activeId, tabId, setActive]);

  // Both visible panes own a read on mount and reconnect; the shared owner
  // coalesces a simultaneous route load for the focused pane.
  // A draft has nothing on the server to read until its first message is
  // acknowledged; loading its provisional key would invent a conversation.
  const draft = useCenterTabs((s) => s.tabs.find((tab) => tab.id === tabId)?.draft === true);
  useEffect(() => {
    if (!sessionId || draft) return;
    const read = () => requestSessionLoad({ action: "load_session", session_id: sessionId, history_version: HISTORY_PROTOCOL });
    // Rows without a window are what an earlier pane left behind.
    if (!holdsHistoryWindow(useSessionHistory.getState().pages[sessionId])) read();
    const reconnect = (event: Event) => {
      if ((event as CustomEvent).detail?.connected) read();
    };
    window.addEventListener("op:browser-connection", reconnect);
    return () => window.removeEventListener("op:browser-connection", reconnect);
  }, [sessionId, draft]);

  // Reserve the composer's real height as scroller padding. The composer is
  // `position:absolute; bottom:0` and floats over the transcript (same as
  // the main shell, which reserves a flat 25vh). Ours varies with the input
  // row / attachment chips / fn-form, so measure it and publish the value
  // as a CSS var the .chat-messages padding reads.
  const composerHostRef = useRef<HTMLDivElement | null>(null);
  const paneRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const host = composerHostRef.current;
    const pane = paneRef.current;
    if (!host || !pane) return;
    // Measure the composer ROOT, not the host. The host is
    // `position:absolute` and its only child (`.inputArea`) is absolutely
    // positioned too, so the host's own box collapses to 0 height —
    // measuring it yielded a useless 24px. `.inputArea` is the Composer's
    // root element and the provider around it renders no DOM, so the
    // host's first element child IS that root.
    const target = host.firstElementChild as HTMLElement | null;
    if (!target) return;
    const apply = () => {
      if (target.offsetHeight > 0) {
        const h = visibleComposerHeight(target);
        pane.style.setProperty("--peer-composer-h", `${Math.round(h) + 24}px`);
      }
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(target);
    target.addEventListener(COMPOSER_TUCK_SHIFT_EVENT, apply);
    return () => {
      ro.disconnect();
      target.removeEventListener(COMPOSER_TUCK_SHIFT_EVENT, apply);
    };
  }, [sessionId]);

  const columnRef = useRef<HTMLDivElement | null>(null);
  const lastId = ids.at(-1) ?? null;
  const { detached, jumpToLatest } = useChatAreaStick(scrollKey, lastId, true, {
    sessionId,
    areaRef,
    columnRef,
    composerRootRef: composerHostRef,
  });
  const chatKey = scrollKey;
  // The pane draws its conversation as the main chat would, bot view included.
  const botView = useBotView(sessionId);
  useOriginalsToggle();
  const heldRows = selectViewRows(sessionId)(useSessionStore.getState());
  const { covered } = coveringOf(ids, heldRows, botView);
  const timeLabels = useMemo(() => timeBreaks(ids.flatMap((id) => {
    const message = selectRow(sessionId, id)(useSessionStore.getState());
    return message ? [message] : [];
  })), [ids, sessionId]);
  const botContext = useMemo(() => botView ? { breaks: timeLabels } : null, [botView, timeLabels]);
  const placedDecisions = usePlacedDecisions(sessionId, ids);
  const alwaysLive = collectAlwaysLive(ids, (id) => heldRows[id]);
  const { view, notifyMeasured } = useMessageViewport(chatKey, ids.length, true, areaRef);
  const windowNodes: WindowNode[] = [];
  for (let i = 0; i < ids.length;) {
    if (!covered.has(ids[i])) {
      windowNodes.push({ kind: "row", id: ids[i] });
      i += 1;
      continue;
    }
    windowNodes.push({ kind: "fold", id: ids[i] });
    while (i < ids.length && covered.has(ids[i])) i += 1;
  }
  const liveSet = chatKey
    ? decideLiveRows({
        nodes: windowNodes,
        heights: heightsFor(chatKey),
        scrollTop: view.top,
        viewH: view.h,
        overscan: view.overscan,
        always: alwaysLive,
        listLen: ids.length,
        recycleMin: RECYCLE_MIN_ROWS,
      })
    : null;
  // A fold of covered originals recycles as one node, measured like a row.
  useLayoutEffect(() => {
    const column = columnRef.current;
    if (!chatKey || !liveSet || !column) return;
    let changed = false;
    column.querySelectorAll<HTMLElement>("[data-fold-id]").forEach((el) => {
      const fid = el.dataset.foldId;
      if (fid && setRowHeight(chatKey, foldKey(fid), el.offsetHeight) !== "same") changed = true;
    });
    if (changed) notifyMeasured();
  });

  // `flex: 1` (not just height) — the .center-split-* wrapper is a flex row
  // with no explicit height, so the pane fills its slot by flexing.
  return (
    <div
      ref={paneRef}
      className="peer-session-pane"
      data-pane-focused={activeId === tabId ? "true" : "false"}
      onFocusCapture={claimFocus}
      onPointerDownCapture={claimFocus}
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <TranscriptReadStatus sessionId={sessionId} />
      {showTitle ? <div
        className="peer-session-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          fontSize: "var(--fs-xs)",
          color: "var(--text-secondary)",
          borderBottom: "1px solid var(--border)",
          flex: "0 0 auto",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {showTitle ? title : null}
        </span>
      </div> : null}
      {/* `minWidth: 0` on both the scroller and the column: without it a
          flex child refuses to shrink below its content's intrinsic
          width, and the bubbles collapse instead of wrapping. */}
      <div
        ref={areaRef}
        tabIndex={0}
        className="chat-area peer-session-area"
        style={{ flex: 1, minHeight: 0, minWidth: 0, overflowY: "auto" }}
      >
        <div
          ref={columnRef}
          className="chat-messages"
          style={{ minWidth: 0, minHeight: "100%" }}
        >
          <SelectionQuote key={sessionId} sessionId={sessionId} />
          {ids.length === 0 ? (
            <div
              style={{
                margin: "auto",
                fontSize: "var(--fs-sm)",
                opacity: 0.55,
                textAlign: "center",
              }}
            >
              {readStatus === "error" || readStatus === "disconnected" ? null : sessionId && !historyLoaded
                ? text("Loading conversation…", "加载会话中…")
                : text("Send a message to start", "发送消息以开始会话")}
            </div>
          ) : (
            <BotViewContext.Provider value={botContext}>
              <TranscriptRows
                sessionId={sessionId}
                viewKey={sessionId}
                ids={ids}
                chatKey={chatKey}
                liveRows={liveSet}
                onMeasured={notifyMeasured}
              />
            </BotViewContext.Provider>
          )}
          <DecisionOutputs key={sessionId} sessionId={sessionId} placed={placedDecisions} />
        </div>
      </div>
      {detached ? (
        <div className="jump-latest-anchor" style={{ bottom: "calc(var(--peer-composer-h, 0px) + 12px)" }}>
          <Button type="button" variant="elevated" size="xs" className="jump-latest" onClick={jumpToLatest}>
            <ArrowDown data-icon="inline-start" aria-hidden="true" />
            {text("Jump to latest", "跳到最新")}
          </Button>
        </div>
      ) : null}
      {/* Full composer, scoped to this pane's session. The scope is what the
          composer and its control hooks (draft, run state, thinking effort,
          tools, permission mode, /context panel) read, so everything here
          targets this session rather than the focused one. */}
      {sessionId ? (
        <div ref={composerHostRef} className="peer-session-composer-host">
          <SessionScopeProvider sid={sessionId}>
            <Composer sessionId={sessionId} />
          </SessionScopeProvider>
        </div>
      ) : null}
    </div>
  );
});
