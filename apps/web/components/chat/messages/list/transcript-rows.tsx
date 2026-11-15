"use client";

/**
 * The one row list every transcript draws through: the main chat, a split
 * pane and a sub-agent's expanded view. It takes the conversation and view
 * from its caller and hands them to every row (TranscriptScopeContext), so
 * no row looks at the focused conversation. Around the rows it places what
 * hangs off them: covered originals folded under their compaction card,
 * task lists, goal records, decision receipts and rewind marks.
 */
import { useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { selectViewRows, useSessionStore, type ChatMsg } from "@/lib/session-store";
import { carryUnfinishedPlan, plansByRow } from "@/lib/chat/transcript/todo-plan";
import { placeMarks } from "@/lib/chat/rewind/rewind-logic";
import { useRewindAllowed, useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";

import { GoalDetails, useSessionGoal } from "../../badges/goal-chip";
import { RewindMarkers, useRewindMarks } from "../../rewind/rewind-marker";
import { DecisionOutputs, useDecisionAnchors } from "../runtime/decision-output";
import { TodoPlans, useSessionPlans } from "../cards/todo-plan-card";
import { originalsOpen, originalsSubs } from "./rows/system-event-rows";
import { MessageRow, PlainRow, RecyclableRow } from "./rows/message-rows";
import { BotViewContext, TranscriptScopeContext, type TranscriptScope } from "./transcript-view-scope";

export interface Covering {
  /** Rows a compaction card in this list folds. */
  covered: ReadonlySet<string>;
  /** Covered rows whose card keeps its originals closed. */
  hidden: ReadonlySet<string>;
}

const NO_COVERING: Covering = { covered: new Set(), hidden: new Set() };

/** Which rows fold under a compaction card: those the server marked with
 *  coveredBy a card this list holds. A chat drawn as a bot conversation has
 *  no summary cards, so everything said stays in place. */
export function coveringOf(ids: readonly string[], rows: Readonly<Record<string, ChatMsg | undefined>>, botView: boolean): Covering {
  if (botView) return NO_COVERING;
  const covered = new Set<string>();
  const hidden = new Set<string>();
  const held = new Set(ids);
  for (const id of ids) {
    const card = rows[id]?.coveredBy;
    if (!card || !held.has(card)) continue;
    covered.add(id);
    if (!originalsOpen.has(card)) hidden.add(id);
  }
  return { covered, hidden };
}

/** Redraw when a compaction card shows or hides its originals. */
export function useOriginalsToggle(): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const redraw = () => setTick((n) => n + 1);
    originalsSubs.add(redraw);
    return () => { originalsSubs.delete(redraw); };
  }, []);
}

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
  useOriginalsToggle();
  const heldRows = selectViewRows(viewKey)(useSessionStore.getState());
  const { covered, hidden } = coveringOf(ids, heldRows, botView);
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const running = useSessionStore((s) => !embedded && !!sessionId && s.runningTasks[sessionId] != null);
  const goal = useSessionGoal(embedded ? null : sessionId);
  const endRecords = goal?.end_records ?? [];
  const allRewindMarks = useRewindMarks(embedded ? null : sessionId, lastId, running);
  const rewindDisabled = useRewindDisabled(sessionId);
  const rewindAllowed = useRewindAllowed(sessionId);
  const rewindMarks = placeMarks(rewindAllowed && !embedded ? allRewindMarks : [], ids, rewindDisabled !== null);
  const decisionAnchors = useDecisionAnchors(embedded ? null : sessionId);
  // Each task list sits under the reply of the turn that last changed it.
  // A sub-agent's own lists sit under its replies (the row draws them).
  const sessionPlans = useSessionPlans(embedded ? null : sessionId);
  const plansAt = plansByRow(sessionPlans, ids, heldRows);
  carryUnfinishedPlan(plansAt, sessionPlans, ids, heldRows);
  const lastUserRow = ids.findLastIndex((id) => heldRows[id]?.role === "user");

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
  const addTodoPlans = (anchors: string[]) => {
    if (botView) return;
    for (const anchor of anchors) {
      const plans = plansAt.get(anchor);
      if (plans) nodes.push(
        <div key={`plans-${anchor}`} data-only-full-view="">
          <TodoPlans sessionId={sessionId} rowId={anchor} plans={plans}
            latest={ids.indexOf(anchor) > lastUserRow} />
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
  let i = 0;
  while (i < ids.length) {
    const id = ids[i];
    if (covered.has(id)) {
      const run: string[] = [];
      while (i < ids.length && covered.has(ids[i])) {
        run.push(ids[i]);
        i += 1;
      }
      nodes.push(
        <div
          key={`${run[0]}_fold`}
          className="compaction-orig-fold"
          data-fold-id={run[0]}
          data-open={hidden.has(run[0]) ? "0" : "1"}
        >
          <div className="compaction-orig-fold-inner">
            {run.map((cid) => (
              <div key={cid} className="covered-turn">
                <MessageRow id={cid} />
              </div>
            ))}
          </div>
        </div>,
      );
      addTodoPlans(run);
      addGoalRecords(run);
      addDecisionReceipts(run);
      run.forEach(addRewindMarks);
      continue;
    }
    if (embedded) nodes.push(<MessageRow key={id} id={id} />);
    else nodes.push(
      liveRows && chatKey ? (
        <RecyclableRow
          key={id}
          id={id}
          chatKey={chatKey}
          live={liveRows.has(id)}
          onMeasured={onMeasured ?? noop}
        />
      ) : (
        <PlainRow key={id} id={id} />
      ),
    );
    addTodoPlans([id]);
    addGoalRecords([id]);
    addDecisionReceipts([id]);
    addRewindMarks(id);
    i += 1;
  }
  return (
    <TranscriptScopeContext.Provider value={scope}>
      {nodes}
      {rewindMarks.tail.length ? <RewindMarkers sessionId={sessionId} marks={rewindMarks.tail} /> : null}
    </TranscriptScopeContext.Provider>
  );
}

function noop(): void {}
