"use client";

// The agent's task list, drawn under the reply of the turn that last
// changed it (lib/chat/transcript/todo-plan.ts). It follows the agent while
// that turn runs and goes away once the work is done. Work a stopped or
// failed turn left unfinished stays under the latest turn, so the reader
// sees where it stopped.

import { useEffect, useState } from "react";
import { CircleCheck, CircleDashed, CircleEllipsis } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { selectRow, useSessionStore } from "@/lib/session-store";
import {
  planShown,
  planWindow,
  runningAgentCount,
  turnState,
  useTodoStore,
  type PlacedPlan,
  type TodoStatus,
  type TurnState,
} from "@/lib/chat/transcript/todo-plan";
import { useTranslation } from "@/lib/i18n";
import styles from "./todo-plan-card.module.css";

const EMPTY: PlacedPlan[] = [];

// The length of the exit animation in the stylesheet.
const EXIT_MS = 400;

/** How the turn whose reply `rowId` is stands, read the way the reply
 *  bubble reads it. */
function useTurnState(sessionId: string, viewKey: string, rowId: string): TurnState {
  return useSessionStore((s) => turnState(
    selectRow(viewKey, rowId)(s),
    Object.values(s.executionUpdateOrders).some((order) =>
      order.terminal && order.sessionId === sessionId && order.messageIds.includes(rowId)),
  ));
}

function StatusIcon({ status, spin }: { status: TodoStatus; spin: boolean }) {
  if (status === "completed") {
    return <CircleCheck size={16} className={`${styles.icon} ${styles.checked}`} data-status="completed" aria-hidden="true" />;
  }
  if (status === "in_progress") {
    return spin
      ? <Spinner className={`${styles.icon} ${styles.current}`} data-status="in_progress" data-live="" aria-hidden="true" />
      : <CircleEllipsis size={16} className={`${styles.icon} ${styles.current}`} data-status="in_progress" aria-hidden="true" />;
  }
  return <CircleDashed size={16} className={`${styles.icon} ${styles.todo}`} data-status="pending" aria-hidden="true" />;
}

function PlanItems({ plan, spin, agents }: { plan: PlacedPlan; spin: boolean; agents: number }) {
  const { text } = useTranslation();
  const statusLabel: Record<TodoStatus, string> = {
    completed: text("Done", "已完成"),
    in_progress: text("In progress", "进行中"),
    pending: text("To do", "待办"),
  };
  const { visible, hiddenBefore, openAfter } = planWindow(plan.todos);
  // The count sits on the step waiting for those agents, else the last one.
  const countOn = agents > 0
    ? (visible.find((item) => item.status === "in_progress") ?? visible.at(-1))?.id
    : undefined;
  return (
    <div className="flex flex-col gap-[4px] p-[16px]">
      {plan.stale ? <p className={styles.note}>{text("Plan from before your last message", "你上一条消息之前的计划")}</p> : null}
      {hiddenBefore > 0 ? (
        <p className={`${styles.note} ${styles.indented}`}>
          {text(`${hiddenBefore} earlier ${hiddenBefore === 1 ? "step" : "steps"}`, `前面还有 ${hiddenBefore} 步`)}
        </p>
      ) : null}
      <ol className="m-0 flex list-none flex-col gap-[8px] p-0">
        {visible.map((item) => (
          <li key={item.id} className={`${styles.item} flex min-w-0 items-start gap-[4px]`} data-status={item.status}>
            <StatusIcon status={item.status} spin={spin} />
            <span className="sr-only">{statusLabel[item.status]}: </span>
            <span className={`${styles.subject} min-w-0`}>
              {item.subject}
              {item.id === countOn ? (
                <span className={styles.detail}>{text(`${agents} running ${agents === 1 ? "task" : "tasks"}`, `${agents} 个后台任务在跑`)}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
      {openAfter > 0 ? <p className={`${styles.note} ${styles.indented}`}>{text(`${openAfter} more to go`, `后面还有 ${openAfter} 步`)}</p> : null}
    </div>
  );
}

function TodoPlan({ sessionId, viewKey, rowId, plan, latest }: { sessionId: string; viewKey: string; rowId: string; plan: PlacedPlan; latest: boolean }) {
  const { text } = useTranslation();
  const state = useTurnState(sessionId, viewKey, rowId);
  const running = useSessionStore((s) => runningAgentCount(selectRow(viewKey, rowId)(s)));
  const shown = planShown(plan, state, latest, running);
  // A list that was showing folds away; one already gone at load never draws.
  const [wasShown, setWasShown] = useState(shown);
  const [closing, setClosing] = useState(false);
  if (wasShown !== shown) {
    setWasShown(shown);
    setClosing(!shown);
  }
  useEffect(() => {
    if (!closing) return;
    const timer = setTimeout(() => setClosing(false), EXIT_MS);
    return () => clearTimeout(timer);
  }, [closing]);
  if (!shown && !closing) return null;
  const spin = state === "running";
  const card = (
    <section
      className={`${styles.card} w-full min-w-0 shrink-0`}
      data-todo-plan={plan.planId}
      data-anchor={rowId}
      data-live={spin ? "" : undefined}
      aria-label={text("Task list", "任务清单")}
    >
      <PlanItems plan={plan} spin={spin} agents={spin ? 0 : running} />
    </section>
  );
  if (!closing) return card;
  return (
    <div className={styles.exit} inert>
      <div className="min-h-0 min-w-0">{card}</div>
    </div>
  );
}

/** The plans placed under the reply `rowId`, given or looked up. */
export function TodoPlans({ sessionId, viewKey, rowId, plans, latest = true }: {
  sessionId: string | null;
  /** The view holding the row, when it is not the conversation itself. */
  viewKey?: string;
  rowId: string;
  plans?: readonly PlacedPlan[];
  /** No later message from the user follows this turn. */
  latest?: boolean;
}) {
  const stored = useTodoStore((s) => (sessionId && !plans ? s.plansBySession[sessionId] : undefined) ?? EMPTY);
  if (!sessionId) return null;
  const list = plans ?? stored.filter((plan) => plan.anchorId === rowId);
  if (!list.length) return null;
  return <>{list.map((plan) => <TodoPlan key={`${plan.anchorId}:${plan.planId}`} sessionId={sessionId} viewKey={viewKey ?? sessionId} rowId={rowId} plan={plan} latest={latest} />)}</>;
}

/** Every session's placed plans, for the transcript to look up by row. */
export function useSessionPlans(sessionId: string | null): readonly PlacedPlan[] {
  return useTodoStore((s) => (sessionId ? s.plansBySession[sessionId] : undefined) ?? EMPTY);
}
