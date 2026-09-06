"use client";

import { useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import type { PendingDecision } from "@/lib/session-store/types";
import { ExecutionApiError, postExecutionCommand, type WaitCommand } from "@/lib/net/execution-client";
import { showToast } from "@/lib/format-utils/toast";

export function useWaitAnswer(q: PendingDecision | null, onResolve: (id: string) => void) {
  const { text } = useTranslation();
  const [, render] = useState(0);
  const requests = useRef(new Map<string, { command: WaitCommand; busy: boolean }>());
  async function sendAnswer(action: "execution.wait.answer" | "execution.wait.decline", value?: unknown) {
    if (!q?.executionId || !Number.isInteger(q.expectedVersion)) {
      showToast(text("The request is not ready. Reconnect and retry.", "请求尚未就绪，请重连后重试。"), { tone: "error" });
      return;
    }
    if (requests.current.get(q.id)?.busy) return;
    const request = requests.current.get(q.id) ?? { busy: false, command: {
      type: "execution.command" as const, action,
      command_id: `web-wait-${crypto.randomUUID()}`,
      execution_id: q.executionId, expected_version: q.expectedVersion,
      payload: action === "execution.wait.answer"
        ? { wait_id: q.id, generation: q.waitGeneration, answer: value }
        : { wait_id: q.id, generation: q.waitGeneration, reason: value },
    } };
    requests.current.set(q.id, request);
    request.busy = true;
    render(n => n + 1);
    try {
      const result = await postExecutionCommand(request.command, AbortSignal.timeout(15000));
      if (result.command_id === request.command.command_id && result.status === "rejected") {
        requests.current.delete(q.id);
        showToast(text("The answer was rejected. Check the current request before retrying.", "答复被拒绝，请检查当前请求后重试。"), { tone: "error" });
        return;
      }
      if (result.command_id !== request.command.command_id || result.status !== "applied") {
        throw new Error("Answer was not confirmed");
      }
      requests.current.delete(q.id);
      onResolve(q.id);
      return true;
    } catch (error) {
      if (error instanceof ExecutionApiError && error.command?.command_id === request.command.command_id
          && error.command.status === "rejected") {
        requests.current.delete(q.id);
        showToast(text("The answer was rejected. Check the current request before retrying.", "答复被拒绝，请检查当前请求后重试。"), { tone: "error" });
        return;
      }
      showToast(text("Answer not confirmed. Retry to send the same answer.", "答复尚未确认，请重试发送同一答复。"), { tone: "error" });
    } finally {
      request.busy = false;
      render(n => n + 1);
    }
  }

  return { sendAnswer, answerPending: Boolean(q && requests.current.get(q.id)?.busy), answerLocked: Boolean(q && requests.current.has(q.id)) };
}
