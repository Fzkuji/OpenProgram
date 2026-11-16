"use client";
import { TranscriptReadStatus } from "./transcript-read-status";

/**
 * Message list — the React message stream.
 *
 * Portaled into `#messages-mount` (a `display:contents` host inside the
 * legacy `#chatMessages` container), so each rendered bubble becomes a
 * direct flex child of `#chatMessages` — the same layout the legacy
 * renderer produced.
 *
 * The active conversation comes from the store's `currentSessionId`,
 * kept in sync by the `chat_ack` reducer and the route effect in
 * `app-shell.tsx`. Each `MessageRow` subscribes to its own message
 * entry so a streaming delta re-renders only the affected bubble.
 */
import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useMessageViewport } from "./use-message-viewport";
import { useHistoryWindow } from "./use-history-window";
import { useChatAreaStick } from "./use-chat-area-stick";
import { ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";

import { selectRow, selectViewRows, useMessageIds, useSessionStore } from "@/lib/session-store";

import { useTranslation } from "@/lib/i18n";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { RECYCLE_MIN_ROWS, collectAlwaysLive, decideLiveRows, foldKey, heightsFor, noteChatWidth, setRowHeight, type WindowNode } from "@/lib/chat/history/message-window";

import { DecisionOutputs } from "../runtime/decision-output";
import { SystemAccessWaits } from "../runtime/system-access-waits";
import { MessageRail } from "../navigation/message-rail";
import { ConversationFind } from "../navigation/conversation-find";
import { AgentBranchBanner } from "../cards/agent-branch-banner";
import { WorktreeBranchMarker } from "../cards/worktree-branch-marker";
import { BranchedFromBanner } from "../cards/branched-from-banner";
import { SelectionQuote } from "../editing/quote-to-chat";
import { TranscriptRows, coveringOf, useOriginalsToggle, usePlacedDecisions } from "./transcript-rows";
import { BotTyping } from "../bubbles/bot-message";
import { BotViewContext } from "./transcript-view-scope";
import { timeBreaks } from "@/lib/chat/transcript/bot-view";
import { useBotView } from "@/lib/chat/transcript/use-bot-view";
import { useBotViewTransition } from "./use-bot-view-transition";
import { OperationStatusIndicator, PendingReplyIndicator, TranscriptSkeleton } from "./rows/list-states";
import { operationStatusText } from "@/lib/chat/run/run-operation";

const JUMP_LATEST_FADE_MS = 280;

/** The view a chat pane draws: its conversation, else the New chat's own
 *  provisional key, which holds the sent first message until chat_ack. */
export function transcriptViewKey(sessionId: string | null, chatKey: string | null): string | null {
  return sessionId ?? (chatKey && chatKey !== "__new__" ? chatKey : null);
}

export const MessageList = memo(function MessageList({
  paintRows = true,
}: {
  paintRows?: boolean;
}) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const chatKey = useSessionStore((s) => s.activeChatKey);
  // The rows drawn: the conversation's, or a New chat's own before the
  // server has acknowledged it. A first message is shown the moment it is
  // sent; until its chat_ack the draft has no conversation id, and a slow
  // acknowledgement must not leave the page blank while the turn runs.
  const viewKey = transcriptViewKey(sessionId, chatKey);
  useHistoryWindow(sessionId, paintRows);
  const ids = useMessageIds(viewKey);
  useOriginalsToggle();
  const { shown: botView, markerRef: viewMarker, labelledRows } = useBotViewTransition(viewKey, useBotView(viewKey));
  const heldRows = selectViewRows(viewKey)(useSessionStore.getState());
  const { covered: coveredSet, hidden: hiddenCovered } = coveringOf(ids, heldRows, botView);
  // Read as the three things drawn from it: the task object is replaced by
  // every status frame of a run.
  const isRunning = useSessionStore((s) => !!viewKey && s.runningTasks[viewKey] != null);
  const runStartedAt = useSessionStore((s) => (viewKey ? s.runningTasks[viewKey]?.started_at : undefined));
  // An operation that is not a reply (/compact, a rewind, a turn's own
  // compaction) is named in the indicator's place.
  const operationText = useSessionStore((s) => operationStatusText(viewKey ? s.runningTasks[viewKey] : null, text));
  // Pin to #chatView (column, not the scroller). Portal into #chatArea
  // puts absolute-bottom on the scroll content, so the button is off
  // screen exactly when we want it visible.
  const jumpHost = typeof document !== "undefined"
    ? document.getElementById("chatView")
    : null;
  // Only the LAST row's role matters here (see ``showPending`` below).
  // Subscribing to the whole row map would re-render this
  // component — and re-map every id — on every single streaming delta,
  // because each delta writes a fresh map object.
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const pendingAnchor = runtimeState._pendingExpandAttach?.anchor;
  const lastRole = useSessionStore((s) =>
    !selectRow(viewKey, lastId)(s)?.steeringReplyId ? (selectRow(viewKey, lastId)(s)?.role ?? null) : null,
  );
  const loadingId = useSessionStore((s) => s.transcriptLoadingId);
  const readStatus = useSessionStore(s => sessionId ? s.transcriptReadStatus[sessionId] : undefined);
  const { showJump, jumpToLatest } = useChatAreaStick(
    chatKey,
    lastId,
    paintRows,
  );
  const [railTarget, setRailTarget] = useState<string | null>(null);
  const [findTarget, setFindTarget] = useState<string | null>(null);
  const alwaysLive = collectAlwaysLive(
    ids,
    (id) => heldRows[id],
    [pendingAnchor, railTarget, findTarget],
  );
  const {view, notifyMeasured} = useMessageViewport(chatKey, ids.length, paintRows);
  const windowNodes: WindowNode[] = [];
  {
    let i = 0;
    while (i < ids.length) {
      const id = ids[i];
      if (coveredSet.has(id)) {
        const first = id;
        while (i < ids.length && coveredSet.has(ids[i])) i += 1;
        windowNodes.push({ kind: "fold", id: first });
      } else {
        windowNodes.push({ kind: "row", id });
        i += 1;
      }
    }
  }
  if (paintRows && chatKey && typeof document !== "undefined") {
    const areaW = document.getElementById("chatArea")?.clientWidth ?? 0;
    if (areaW > 0) noteChatWidth(chatKey, areaW);
  }
  const liveSet = paintRows && chatKey
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
  useLayoutEffect(() => {
    if (!paintRows || !chatKey || !liveSet) return;
    const root = document.getElementById("chatMessages");
    if (!root) return;
    let changed = false;
    root.querySelectorAll<HTMLElement>("[data-fold-id]").forEach((el) => {
      const fid = el.dataset.foldId;
      if (!fid) return;
      if (setRowHeight(chatKey, foldKey(fid), el.offsetHeight) !== "same") {
        changed = true;
      }
    });
    if (changed) notifyMeasured();
  });
  const [jumpShown, setJumpShown] = useState(false);
  const [jumpLeaving, setJumpLeaving] = useState(false);
  useEffect(() => {
    const want = paintRows && showJump && ids.length > 0;
    if (want) {
      setJumpLeaving(false);
      setJumpShown(true);
      return;
    }
    if (!jumpShown) return;
    setJumpLeaving(true);
    const t = window.setTimeout(() => {
      setJumpShown(false);
      setJumpLeaving(false);
    }, JUMP_LATEST_FADE_MS);
    return () => window.clearTimeout(t);
  }, [showJump, ids.length, jumpShown, paintRows]);

  // Parent-return actions: once the reload has rows on screen, reveal the
  // original sub-agent timeline row. Collapsed strips unmount their children,
  // so first open the exact strip indexed by branch head, then locate the row.
  // The flag is consumed only when the reveal actually runs, so a
  // mid-hydration re-render just reschedules it.
  useEffect(() => {
    if (!paintRows || !runtimeState._pendingExpandAttach || ids.length === 0) return;
    const esc = (v: string) =>
      window.CSS && CSS.escape ? CSS.escape(v) : v;
    const t = window.setTimeout(() => {
      const pend = runtimeState._pendingExpandAttach;
      if (!pend) return;
      runtimeState._pendingExpandAttach = null;
      const anchorEl = document.querySelector(
        '[data-msg-id="' + esc(pend.anchor) + '"], [data-msg-ids~="'
        + esc(pend.anchor) + '"]',
      ) as HTMLElement | null;
      anchorEl?.scrollIntoView({ block: "center" });
      const strip = anchorEl?.querySelector(
        '.tl[data-subagent-heads~="' + esc(pend.head) + '"]',
      ) as HTMLElement | null;
      if (strip?.getAttribute("data-open") === "0") {
        (strip.querySelector('.tl-toggle') as HTMLElement | null)?.click();
      }
      window.setTimeout(() => {
        const row = document.querySelector(
          '.tl-step[data-head-id="' + esc(pend.head) + '"]',
        ) as HTMLElement | null;
        const target = row || anchorEl;
        if (!target) return;
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("dag-flash");
        window.setTimeout(() => target.classList.remove("dag-flash"), 1400);
      }, 280);
    }, 250);
    return () => window.clearTimeout(t);
  }, [ids, paintRows]);

  // Fade the transcript in once per session switch, and only when its rows
  // arrive after the switch (the skeleton showed first). A session already
  // in the store swaps in at once: fading cached rows up from nothing only
  // reads as a flash. The ref remembers which session already settled, so
  // streaming updates (ids.length growing) don't re-trigger the animation.
  const lastFadedSession = useRef<string | null>(null);
  const waitedSession = useRef<string | null>(null);
  if (paintRows && sessionId && ids.length === 0) waitedSession.current = sessionId;
  useEffect(() => {
    if (!paintRows || !sessionId || ids.length === 0) return;
    if (lastFadedSession.current === sessionId) return;
    lastFadedSession.current = sessionId;
    if (waitedSession.current !== sessionId) return;
    const el = document.getElementById("chatMessages");
    if (!el) return;
    el.classList.add("session-enter");
    const t = setTimeout(() => el.classList.remove("session-enter"), 220);
    return () => {
      clearTimeout(t);
      el.classList.remove("session-enter");
    };
  }, [sessionId, ids.length, paintRows]);

  // Show the standalone indicator while we're still waiting on the
  // turn — either:
  //   * the assistant placeholder hasn't landed yet (last msg is the
  //     user turn we just sent), OR
  //   * the placeholder exists but is still empty (chat_ack landed
  //     but no text/thinking/tool deltas yet)
  // Once the bubble has ANY content, that bubble's own
  // TypingIndicator / streaming text takes over and we hide.
  // Only show the STANDALONE indicator when there's no assistant
  // placeholder yet — i.e. between user send and ``chat_ack``. Once
  // the assistant bubble exists (even empty), its own
  // ``TypingIndicator`` handles the empty-streaming state. Without
  // this guard the user sees two stacked "Agentic" rows: the real
  // placeholder bubble + the standalone, double-rendering.
  const showPending = isRunning && lastRole === "user";
  const timeLabels = useMemo(() => timeBreaks(ids.flatMap((id) => {
    const message = selectRow(viewKey, id)(useSessionStore.getState());
    return message ? [message] : [];
  })), [ids, viewKey]);
  labelledRows.current = timeLabels;
  const botContext = useMemo(() => botView ? { breaks: timeLabels } : null, [botView, timeLabels]);
  const placedDecisionAnchors = usePlacedDecisions(sessionId, ids);

  // Session switch with nothing cached yet: skeleton placeholder
  // instead of an empty area / welcome flash. Minimap etc. wait too.
  if (sessionId && (readStatus === "loading" || (!readStatus && loadingId === sessionId)) && ids.length === 0) {
    return <TranscriptSkeleton />;
  }

  return (
    <BotViewContext.Provider value={botContext}>
      <span ref={viewMarker} hidden />
      <TranscriptReadStatus sessionId={sessionId} />
      <SelectionQuote key={sessionId} sessionId={sessionId} />
      <BranchedFromBanner />
      <AgentBranchBanner />
      <WorktreeBranchMarker />
      {paintRows ? (
        <MessageRail hiddenKey={[...hiddenCovered].join("\n")} onSeek={setRailTarget} />
      ) : null}
      {paintRows ? <ConversationFind sessionId={sessionId} onTarget={setFindTarget} /> : null}
      {paintRows ? (
        <TranscriptRows
          sessionId={sessionId}
          viewKey={viewKey}
          ids={ids}
          chatKey={chatKey}
          liveRows={liveSet}
          onMeasured={notifyMeasured}
        />
      ) : null}
      {paintRows && operationText && !botView ? (
        <OperationStatusIndicator label={operationText} timestamp={runStartedAt} />
      ) : paintRows && showPending ? (
        botView ? <BotTyping /> : <PendingReplyIndicator timestamp={runStartedAt} />
      ) : null}
      {paintRows ? <DecisionOutputs key={`decisions-${sessionId}`} sessionId={sessionId} placed={placedDecisionAnchors} /> : null}
      {paintRows && botView && heldRows[ids[ids.length - 1]]?.role === "assistant" ? <div className="bot-end-room" aria-hidden /> : null}
      {paintRows ? <SystemAccessWaits key={sessionId} sessionId={sessionId} /> : null}
      {paintRows && jumpShown && jumpHost
        ? createPortal(
            <div className={[
              "jump-latest-anchor",
              isRunning ? "is-live" : "",
              jumpLeaving ? "is-leaving" : "",
            ].filter(Boolean).join(" ")}>
              <Button
                type="button"
                variant="elevated"
                className="jump-latest text-primary ring-1 ring-primary/45"
                onClick={jumpToLatest}
              >
                {isRunning ? (
                  <span className="jump-latest-live" data-icon="inline-start" aria-hidden>
                    <i />
                    <i />
                    <i />
                  </span>
                ) : (
                  <ArrowDown data-icon="inline-start" aria-hidden />
                )}
                {text("Jump to latest", "跳到最新")}
              </Button>
            </div>,
            jumpHost,
          )
        : null}
    </BotViewContext.Provider>
  );
});
