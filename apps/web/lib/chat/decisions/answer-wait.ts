import { showToast } from "@/lib/format-utils/toast";
import { useSessionStore } from "@/lib/session-store";
import type { PendingDecision } from "@/lib/session-store/types";
import { ExecutionApiError, postExecutionCommand, type WaitCommand } from "@/lib/net/execution/execution-client";
import { isSettled } from "./decision-receipts";
import { useDecisionSubmissions, type DecisionSubmission } from "./decision-submissions";

type Text = (en: string, zh: string) => string;
export type WaitAction = "execution.wait.answer" | "execution.wait.decline";

/** The oldest open request of a session: its run waits on it, so text typed
 *  in the composer answers it instead of starting another turn. */
export function openDecisionFor(sessionId: string | null | undefined): PendingDecision | null {
  if (!sessionId) return null;
  const submissions = useDecisionSubmissions.getState().submissions;
  return useSessionStore.getState().pendingDecisions.find(decision =>
    decision.sessionId === sessionId && decision.executionId
    && !isSettled(submissions[decision.id]) && submissions[decision.id]?.status !== "sending") ?? null;
}

/** Answer or decline one wait. True once the server applied it. */
export async function sendWaitAnswer(q: PendingDecision, action: WaitAction, value: unknown,
  text: Text, onResolve: (id: string) => void): Promise<true | undefined> {
  if (!q.executionId || !Number.isInteger(q.expectedVersion)) return;
  const save = (command: WaitCommand, status: DecisionSubmission["status"], error?: string) =>
    useDecisionSubmissions.getState().setSubmission(q.id, { decision: q, command, status, error });
  async function reconcileRejected(command: WaitCommand) {
    try {
      const response = await fetch(`/api/questions?session_id=${encodeURIComponent(q.sessionId)}`, { signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error("Unable to check request");
      const data = await response.json();
      if (!Array.isArray(data.questions)) throw new Error("Invalid request list");
      const current = data.questions.find((item: { id: string }) => item.id === q.id);
      if (!current) {
        save(command, "closed", text("This request has ended. This answer was not accepted.", "请求已结束，本次回答未被接受。"));
        onResolve(q.id);
        return;
      }
      useSessionStore.getState().enqueueDecision({ ...q,
        expectedVersion: Number(current.expected_version),
        waitGeneration: Number(current.wait_generation),
      });
    } catch { /* Keep the rejected receipt; never imply successful delivery. */ }
    save(command, "rejected", text("The answer was rejected. Review the request and submit again.", "回答被拒绝，请检查问题后重新提交。"));
  }
  const previous = useDecisionSubmissions.getState().submissions[q.id];
  if (previous && ["sending", "answered", "declined", "closed"].includes(previous.status)) return;
  const command: WaitCommand = previous?.status === "unknown" ? previous.command : {
    type: "execution.command", action,
    command_id: `web-wait-${crypto.randomUUID()}`,
    execution_id: q.executionId, expected_version: q.expectedVersion,
    payload: action === "execution.wait.answer"
      ? { wait_id: q.id, generation: q.waitGeneration, answer: value }
      : { wait_id: q.id, generation: q.waitGeneration, reason: value },
  };
  save(command, "sending");
  try {
    const result = await postExecutionCommand(command, AbortSignal.timeout(15000));
    if (result.command_id === command.command_id && result.status === "rejected") {
      await reconcileRejected(command);
      return;
    }
    if (result.command_id !== command.command_id || result.status !== "applied") {
      throw new Error("invalid_command_receipt");
    }
    save(command, command.action === "execution.wait.decline" ? "declined" : "answered");
    if (result.execution?.status === "paused" && result.execution.reason_code === "continuation_contract_mismatch") {
      const message = text("Your answer was saved, but execution could not resume. The operation has not run.", "答复已保存，但执行未能恢复，操作尚未执行。");
      save(command, command.action === "execution.wait.decline" ? "declined" : "answered", message);
      showToast(message, { tone: "error" });
    }
    onResolve(q.id);
    return true;
  } catch (error) {
    if (error instanceof ExecutionApiError && error.command?.command_id === command.command_id
        && error.command.status === "rejected") {
      await reconcileRejected(command);
      return;
    }
    const detail = error instanceof ExecutionApiError
      ? `${error.code}${error.status ? ` (HTTP ${error.status})` : ""}`
      : error instanceof Error ? error.message : "request_failed";
    save(command, "unknown", `${text("Delivery not confirmed. Retry sends the same answer.", "尚未确认送达。重试会发送同一份回答。")} ${detail}`);
  }
}

/** Whether a run of this session waits on an open request. */
export function useAwaitingAnswer(sessionId: string | null | undefined): boolean {
  const pending = useSessionStore((s) => s.pendingDecisions);
  const submissions = useDecisionSubmissions((s) => s.submissions);
  return Boolean(sessionId) && pending.some(decision =>
    decision.sessionId === sessionId && decision.executionId
    && !isSettled(submissions[decision.id]) && submissions[decision.id]?.status !== "sending");
}
