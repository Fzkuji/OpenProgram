"use client";

/**
 * The one row list every transcript draws through: the main chat, a split
 * pane and a sub-agent's expanded view. It takes the conversation and view
 * from its caller and hands them to every row (TranscriptScopeContext), so
 * no row looks at the focused conversation. Around the rows it places what
 * hangs off them: goal records, decision receipts and rewind marks. Rows a
 * compaction covered are drawn like any other row.
 */
import { useContext, useMemo, type ReactNode } from "react";

import { selectViewRows, useSessionStore } from "@/lib/session-store";
import { rowRenderKeys } from "@/lib/chat/transcript/row-render-key";
import { placeMarks } from "@/lib/chat/rewind/rewind-logic";
import { useRewindAllowed, useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";

import { GoalDetails, useSessionGoal } from "../../badges/goal-chip";
import { RewindMarkers, useRewindMarks } from "../../rewind/rewind-marker";
import { DecisionOutputs, useDecisionAnchors } from "../runtime/decision-output";
import { MessageRow, PlainRow, RecyclableRow } from "./rows/message-rows";
import { BotViewContext, TranscriptScopeContext, type TranscriptScope } from "./transcript-view-scope";

/** The decision anchors this list places under their rows; the caller draws the rest after the list. */
export function usePlacedDecisions(sessionId: string | null, ids: readonly string[]): ReadonlySet<string> {
  const anchors = useDecisionAnchors(sessionId);
  return useMemo(() => new Set([...anchors].filter((anchor) => ids.includes(anchor))), [anchors, ids]);
}

export function TranscriptRows({
  sessionId,
  viewKey,
  ids,
  chatKey = null,
  liveRows = null,
  onMeasured,
  embedded = false,
}: {
  sessionId: string | null;
  viewKey: string | null;
  ids: readonly string[];
  /** The recycling window's key, when rows outside the viewport recycle. */
  chatKey?: string | null;
  liveRows?: ReadonlySet<string> | null;
  onMeasured?: () => void;
  embedded?: boolean;
}) {
  const scope = useMemo<TranscriptScope>(() => ({ sessionId, viewKey, embedded }), [sessionId, viewKey, embedded]);
  const botView = useContext(BotViewContext) !== null;
  const heldRows = selectViewRows(viewKey)(useSessionStore.getState());
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const running = useSessionStore((s) => !embedded && !!sessionId && s.runningTasks[sessionId] != null);
  const goal = useSessionGoal(embedded ? null : sessionId);
  const endRecords = goal?.end_records ?? [];
  const allRewindMarks = useRewindMarks(embedded ? null : sessionId, lastId, running);
  const rewindDisabled = useRewindDisabled(sessionId);
  const rewindAllowed = useRewindAllowed(sessionId);
  const rewindMarks = placeMarks(rewindAllowed && !embedded ? allRewindMarks : [], ids, rewindDisabled !== null);
  const decisionAnchors = useDecisionAnchors(embedded ? null : sessionId);
  // A sent message keeps its key when the server renames its row.
  const keyOf = rowRenderKeys(ids, heldRows);

  const nodes: ReactNode[] = [];
  const addGoalRecords = (anchors: (string | null)[]) => {
    if (!sessionId || botView || embedded) return;
    for (const record of endRecords) {
      if (anchors.includes(record.anchor_id)) nodes.push(
        <div key={`goal-end-${record.goal.goal_id}`} data-only-full-view="">
          <GoalDetails sessionId={sessionId} goal={record.goal} historical />
        </div>,
      );
    }
  };
  const addDecisionReceipts = (anchors: string[]) => {
    for (const anchor of anchors) {
      if (decisionAnchors.has(anchor)) nodes.push(
        <DecisionOutputs key={`decisions-${anchor}`} sessionId={sessionId} anchorId={anchor} />,
      );
    }
  };
  const addRewindMarks = (anchor: string) => {
    const marks = rewindMarks.after.get(anchor);
    if (marks) nodes.push(<RewindMarkers key={`rewind-${anchor}`} sessionId={sessionId} marks={marks} />);
  };
  addGoalRecords([null]);
  for (const id of ids) {
    if (embedded) nodes.push(<MessageRow key={keyOf(id)} id={id} />);
    else nodes.push(
      liveRows && chatKey ? (
        <RecyclableRow
          key={keyOf(id)}
          id={id}
          chatKey={chatKey}
          live={liveRows.has(id)}
          onMeasured={onMeasured ?? noop}
        />
      ) : (
        <PlainRow key={keyOf(id)} id={id} />
      ),
    );
    addGoalRecords([id]);
    addDecisionReceipts([id]);
    addRewindMarks(id);
  }
  return (
    <TranscriptScopeContext.Provider value={scope}>
      {nodes}
      {rewindMarks.tail.length ? <RewindMarkers sessionId={sessionId} marks={rewindMarks.tail} /> : null}
    </TranscriptScopeContext.Provider>
  );
}

function noop(): void {}
