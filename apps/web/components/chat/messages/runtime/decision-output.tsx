"use client";

import { useMemo } from "react";
import { useSessionStore, type PendingDecision } from "@/lib/session-store";
import { useDecisionSubmissions, type DecisionSubmission } from "@/lib/chat/decisions/decision-submissions";
import { isSettled, showsReceipt } from "@/lib/chat/decisions/decision-receipts";
import { useTranslation } from "@/lib/i18n";
import { AnswerSummary } from "../../composer/modes/question/answer-summary";
import { QuestionMode } from "../../composer/modes/question/question-mode";
import styles from "./decision-output.module.css";

// Match the lifetime of session receipts: switching panes or answering a later
// parallel question must not change the order in which cards first appeared.
const firstSeen = new Map<string, number>();
function displayOrder(decision: PendingDecision): number {
  const key = JSON.stringify([decision.sessionId, decision.id]);
  if (!firstSeen.has(key)) firstSeen.set(key, firstSeen.size);
  return firstSeen.get(key)!;
}

function DecisionOutput({ decision, submission }: {
  decision: PendingDecision;
  submission?: DecisionSubmission;
}) {
  const { text } = useTranslation();
  const dequeue = useSessionStore((s) => s.dequeueDecision);
  const status = submission?.status;
  const terminal = status === "answered" || status === "declined" || status === "closed";
  const answer = submission?.command.payload.answer;
  const reason = submission?.command.payload.reason;
  return (
    <section className={styles.card} data-decision-output={decision.id}
      data-decision-status={status ?? "open"} data-session-id={decision.sessionId}
      aria-label={text("User decision", "用户决定")}>
      {terminal ? (
        <div className={styles.receipt}>
          <AnswerSummary decision={decision} answer={answer} />
          {typeof reason === "string" && reason && <div className={styles.reason}>
            <span className={styles.label}>{text("You said", "你说")}</span>{reason}
          </div>}
          {submission?.error && <div role="alert">{submission.error}</div>}
          <div role="status" className={styles.label}>
            {status === "answered" ? text("Answer confirmed", "回答已确认")
              : status === "declined" ? (typeof reason === "string" && reason
                ? text("Sent to the agent", "已交给 agent") : text("Declined", "已拒绝"))
              : text("Request closed; answer not confirmed", "请求已关闭，回答未确认")}
          </div>
        </div>
      ) : <QuestionMode decision={decision} onResolve={dequeue} />}
    </section>
  );
}

/** Session-owned output, separate from the chat draft and its submit action.
 *  `anchorId` keeps the settled cards of one reply; without it the cards
 *  whose reply is not among `placed` (asked before this window watched the
 *  run, or that reply is not loaded) collect at the end. */
function DecisionCards({ sessionId, pendingOnly, anchorId, placed }: {
  sessionId: string | null;
  pendingOnly: boolean;
  anchorId?: string;
  placed?: ReadonlySet<string>;
}) {
  const pending = useSessionStore((s) => s.pendingDecisions);
  const submissions = useDecisionSubmissions((s) => s.submissions);
  if (!sessionId) return null;
  const decisions = new Map<string, PendingDecision>();
  for (const submission of Object.values(submissions)) {
    if (submission.decision.sessionId === sessionId) {
      decisions.set(submission.decision.id, submission.decision);
    }
  }
  for (const decision of pending) {
    if (decision.sessionId === sessionId) decisions.set(decision.id, decision);
  }
  const ordered = Array.from(decisions.values());
  ordered.forEach(displayOrder);
  ordered.sort((a, b) => displayOrder(a) - displayOrder(b));
  return <>{ordered.filter(decision => {
    const submission = submissions[decision.id];
    if (pendingOnly) return !isSettled(submission);
    if (!showsReceipt(decision, submission)) return false;
    const anchor = decision.anchorId ?? submission?.decision.anchorId;
    return anchorId !== undefined ? anchor === anchorId : !(anchor && placed?.has(anchor));
  }).map((decision) => (
    <DecisionOutput key={decision.id} decision={decision} submission={submissions[decision.id]} />
  ))}</>;
}

/** Replies that hold a settled card, so the transcript can place it under them. */
export function useDecisionAnchors(sessionId: string | null): ReadonlySet<string> {
  const submissions = useDecisionSubmissions((s) => s.submissions);
  const pending = useSessionStore((s) => s.pendingDecisions);
  const key = sessionId ? Object.values(submissions)
    .filter(submission => submission.decision.sessionId === sessionId && showsReceipt(submission.decision, submission))
    .map(submission => pending.find(p => p.id === submission.decision.id)?.anchorId ?? submission.decision.anchorId ?? "")
    .filter(Boolean).sort().join("\n") : "";
  return useMemo(() => new Set(key ? key.split("\n") : []), [key]);
}

/** Completed receipts stay in conversation history, under the reply they were asked in. */
export function DecisionOutputs({ sessionId, anchorId, placed }: {
  sessionId: string | null;
  anchorId?: string;
  placed?: ReadonlySet<string>;
}) {
  return <DecisionCards sessionId={sessionId} pendingOnly={false} anchorId={anchorId} placed={placed} />;
}

/** Pending panels share the composer's width without owning its draft. */
export function PendingDecisionPanels({ sessionId }: { sessionId: string | null }) {
  return <DecisionCards sessionId={sessionId} pendingOnly />;
}
