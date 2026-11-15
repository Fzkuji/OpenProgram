"use client";

import { useTranslation } from "@/lib/i18n";
import type { PendingDecision } from "@/lib/session-store/types";
import { sendWaitAnswer } from "@/lib/chat/decisions/answer-wait";
import { useDecisionSubmissions } from "@/lib/chat/decisions/decision-submissions";

export function useWaitAnswer(q: PendingDecision | null, onResolve: (id: string) => void) {
  const { text } = useTranslation();
  const submission = useDecisionSubmissions(s => q ? s.submissions[q.id] : undefined);
  async function sendAnswer(action: "execution.wait.answer" | "execution.wait.decline", value?: unknown) {
    if (!q) return;
    return sendWaitAnswer(q, action, value, text, onResolve);
  }

  return { sendAnswer, submission, answerPending: submission?.status === "sending",
    answerLocked: Boolean(submission && ["sending", "unknown", "answered", "declined", "closed"].includes(submission.status)) };
}
