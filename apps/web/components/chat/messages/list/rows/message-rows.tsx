"use client";

/** One transcript row: picks the bubble, card or event a message renders
 *  as, with the recyclable wrapper the virtualized list mounts. */
import { BotViewContext, TranscriptScopeContext, type TranscriptScope } from "../transcript-view-scope";
import { memo, useContext, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { BotMessage } from "../../bubbles/bot-message";
import { selectRow, useSessionStore, type ChatMsg } from "@/lib/session-store";
import { messageArrivedWithin } from "@/lib/session-store/messages/message-arrivals";
import { getRowHeight, setRowHeight } from "@/lib/chat/history/message-window";
import { AgentNoticeRows, ReceivedMessageRow } from "../../cards/agent-event-row";
import { AssistantBubble } from "../../bubbles/assistant-bubble";
import { AttachCard } from "../../cards/attach-card";
import { PromptTurn } from "../../cards/sub-agent-prompt";
import { TodoPlans } from "../../cards/todo-plan-card";
import { SubAgentStrip } from "../../execution/execution-strip";
import { StepExpansionScope } from "../../execution/step-expansion";
import { isAgentLive } from "@/lib/chat/transcript/row-status";
import { RuntimeBlock } from "../../runtime/runtime-block";
import { SpawnedFromCard } from "../../cards/spawned-from-card";
import { UserBubble } from "../../bubbles/user-bubble";
import { MessageTimestamp } from "../../bubbles/message-actions";
import { readReceipt, type Receipt } from "@/lib/chat/transcript/agent-notices";
import type { RowKind } from "@/lib/chat/transcript/wire-types.generated";
import { SystemEventRow, CompactionCard } from "./system-event-rows";

/** A row mounted this soon after its message arrived still plays msgAppear. */
const APPEAR_WINDOW_MS = 1000;

/** The receipt a delivered message shows. The server decided the row is
 *  one; a delivery written without its receipt line shows just the body. */
function receiptOf(msg: ChatMsg): Receipt {
  const content = msg.content || "";
  return readReceipt(content) ?? { kind: "message", from: "", address: "", body: content };
}

/** One branch per row kind the server sends, and per local row kind. */
function dispatch(kind: RowKind, msg: ChatMsg, scope: TranscriptScope): ReactNode {
  const sessionId = scope.sessionId ?? undefined;
  const viewKey = scope.viewKey ?? undefined;
  switch (kind) {
    case "silent":
      return null;
    case "user":
      return scope.embedded ? <PromptTurn msg={msg} /> : <UserBubble msg={msg} sessionIdOverride={sessionId} />;
    case "received_message":
      return scope.embedded ? <PromptTurn msg={msg} /> : <ReceivedMessageRow msg={msg} receipt={receiptOf(msg)} />;
    case "spawned_root":
      return scope.embedded ? <PromptTurn msg={msg} /> : (
        <>
          <SpawnedFromCard msg={msg} />
          <UserBubble msg={msg} sessionIdOverride={sessionId} />
        </>
      );
    case "agent_notice":
      return scope.embedded ? <PromptTurn msg={msg} /> : <AgentNoticeRows msg={msg} items={msg.notice ?? []} />;
    case "assistant":
      // A goal check whose verdict landed before the server's row did stays hidden too.
      if (msg.goalVerification) return null;
      return scope.embedded ? (
        <>
          <AssistantBubble msg={msg} sessionIdOverride={sessionId} viewKey={viewKey} embedded />
          {/* The sub-agent's own plan sits under its own reply. */}
          <TodoPlans sessionId={scope.sessionId} viewKey={viewKey} rowId={msg.id} />
        </>
      ) : <AssistantBubble msg={msg} sessionIdOverride={sessionId} viewKey={viewKey} />;
    case "function_run":
      // 手动函数运行（fn-form / /run）：RuntimeBlock 内部已统一为
      // 时间线组件（根行 + 递归子树，子节点逐层展开）。
      return (
        <div className="runtime-card-host">
          <RuntimeBlock msg={msg} sessionIdOverride={sessionId} />
        </div>
      );
    case "sub_agent_step":
      // A sub-agent of this conversation draws as a step wherever it was
      // started from; only a reference to something else keeps the card.
      return (
        <div className="message assistant" data-msg-id={msg.id}>
          <div className="chat-stream-body">
            <StepExpansionScope view={viewKey} row={msg.id}>
              <SubAgentStrip cards={[msg]} streaming={isAgentLive(msg.attach?.status)} expansionId="subagents" />
            </StepExpansionScope>
          </div>
        </div>
      );
    case "external_reference":
      return (
        <div className="attach-row" data-msg-id={msg.id}>
          <AttachCard msg={msg} />
        </div>
      );
    case "compaction_card":
      return <CompactionCard msg={msg} />;
    case "system_event":
      return <SystemEventRow msg={msg} />;
    case "system_text":
      return (
        <div className="message system" data-msg-id={msg.id}>
          {msg.content}
          <div className="message-actions-footer">
            <div className="message-actions">
              <MessageTimestamp timestamp={msg.timestamp} />
            </div>
          </div>
        </div>
      );
  }
  const unknown: never = kind;
  return unknown;
}

/** The scope a row is drawn in: its row list's, or for a row drawn on its
 *  own, the conversation it was given. */
function useScope(sessionIdOverride?: string): TranscriptScope {
  const listed = useContext(TranscriptScopeContext);
  const focused = useSessionStore((s) => s.currentSessionId);
  if (listed) return listed;
  const sessionId = sessionIdOverride ?? focused;
  return { sessionId, viewKey: sessionId, embedded: false };
}

export const MessageRow = memo(function MessageRow({
  id,
  sessionIdOverride,
}: {
  id: string;
  sessionIdOverride?: string;
}) {
  const scope = useScope(sessionIdOverride);
  const msg = useSessionStore(selectRow(scope.viewKey, id));
  // A reply a restart cut off is drawn by the reply that continues it.
  const continued = useSessionStore((s) => !!selectRow(scope.viewKey, msg?.continuedBy)(s));
  const bot = useContext(BotViewContext) !== null;
  if (!msg || continued || (!bot && msg.steeringReplyId)) return null;
  return (
    <TranscriptScopeContext.Provider value={scope}>
      {bot ? <BotMessage msg={msg} sessionIdOverride={scope.sessionId ?? undefined} /> : dispatch(msg.rowKind, msg, scope)}
    </TranscriptScopeContext.Provider>
  );
});

/** A row outside the recycling window (short transcripts). Like a slot, it
 *  plays msgAppear only when its message has just arrived. */
export const PlainRow = memo(function PlainRow({
  id,
  sessionIdOverride,
}: {
  id: string;
  sessionIdOverride?: string;
}) {
  const [appear] = useState(() => messageArrivedWithin(id, APPEAR_WINDOW_MS));
  return (
    <div className="msg-row" data-appear={appear ? "" : undefined} style={{ display: "contents" }}>
      <MessageRow id={id} sessionIdOverride={sessionIdOverride} />
    </div>
  );
});

export const RecyclableRow = memo(function RecyclableRow({
  id,
  chatKey,
  live,
  onMeasured,
  sessionIdOverride,
}: {
  id: string;
  chatKey: string;
  live: boolean;
  onMeasured: () => void;
  sessionIdOverride?: string;
}) {
  const slotRef = useRef<HTMLDivElement>(null);
  // Only a message that just arrived plays msgAppear; the same row mounted
  // again (a split, the shell coming back into view) shows as it is.
  const [appear] = useState(() => messageArrivedWithin(id, APPEAR_WINDOW_MS));
  const height = getRowHeight(chatKey, id);
  const show = live || height == null;
  // The height a placeholder stood at before this row rendered. It may be
  // an estimate from another width, see noteChatWidth.
  const placeholderRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = slotRef.current;
    if (!el) return;
    if (!show) {
      el.dataset.seen = "1";
      placeholderRef.current = getRowHeight(chatKey, id) ?? null;
      return;
    }
    const estimate = placeholderRef.current;
    placeholderRef.current = null;
    if (estimate != null) {
      // A row coming in above the view: the chat area has no native scroll
      // anchoring, so take up the estimate's error here or the visible
      // rows jump by it.
      const next = el.offsetHeight;
      const area = el.closest<HTMLElement>(".chat-area");
      if (area && next > 0 && next !== estimate
        && el.getBoundingClientRect().top + estimate <= area.getBoundingClientRect().top) {
        area.scrollTop += next - estimate;
      }
    }
    const write = () => {
      const next = el.offsetHeight;
      if (next > 0 && setRowHeight(chatKey, id, next) === "first") onMeasured();
    };
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    return () => ro.disconnect();
  }, [show, id, chatKey, onMeasured]);
  return (
    <div
      ref={slotRef}
      data-msg-slot={id}
      data-appear={appear ? "" : undefined}
      className="msg-slot"
      style={!show && height != null ? { height } : undefined}
    >
      {show ? (
        <div style={{ display: "contents" }}>
          <MessageRow id={id} sessionIdOverride={sessionIdOverride} />
        </div>
      ) : null}
    </div>
  );
});
