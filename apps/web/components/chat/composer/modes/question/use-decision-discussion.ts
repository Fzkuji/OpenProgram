"use client";

import { useCallback, useRef } from "react";
import { useTranslation } from "@/lib/i18n";
import type { PendingDecision } from "@/lib/session-store/types";
import { postExecutionCommand, type WaitCommand } from "@/lib/net/execution-client";
import { enqueueMessage, useSendQueue } from "@/lib/chat/send-queue";
import { showToast } from "@/lib/format-utils/toast";

interface Options {
  decision: PendingDecision | null;
  thinking: string;
  dequeue(id: string): void;
}

export function useDecisionDiscussion({ decision, thinking, dequeue }: Options) {
  const { text } = useTranslation();
  const requests = useRef(new Map<string, { command: WaitCommand; message: string; feedback: string; busy: boolean; sent: boolean }>());

  return useCallback(async (feedback: string) => {
    if (!decision?.sessionId || !decision.executionId) return;
    const d = decision;
    const input = feedback.trim();
    if (!input) return;
    const context = [
      d.prompt,
      ...(d.questions ?? []).map(question => question.prompt),
      d.detail || [
        d.tool ? `Tool: ${d.tool}` : null,
        d.args ? JSON.stringify(d.args, null, 2) : null,
      ].filter(Boolean).join("\n"),
    ].filter(Boolean).join("\n");
    const message = `${input}\n\n${text("Regarding:", "讨论内容：")}\n${context}`;
    let request = requests.current.get(d.id);
    if (!request) {
      request = { busy: false, sent: false, message, feedback: input, command: {
        type: "execution.command", action: "execution.wait.decline",
        command_id: `web-discuss-${crypto.randomUUID()}`,
        execution_id: d.executionId, expected_version: d.expectedVersion,
        payload: { wait_id: d.id, generation: d.waitGeneration, reason: `${text("Discussion requested before proceeding.", "用户希望先讨论再继续。")}\n${message}` },
      } };
      requests.current.set(d.id, request);
    }
    if (request.busy || request.sent) return;
    if (request.feedback !== input) {
      showToast(text("Retry the pending discussion before changing its feedback.", "请先重试待确认的讨论，再修改反馈。"), { tone: "error" });
      return;
    }
    request.busy = true;
    try {
      const result = await postExecutionCommand(request.command, AbortSignal.timeout(15000));
      if (result.command_id !== request.command.command_id || result.status !== "applied") {
        throw new Error("Rejection was not confirmed");
      }
      // Use the existing session queue: the declined execution may not have
      // cleared in the WebSocket projection yet. Drafts and attachments stay put.
      enqueueMessage(d.sessionId, {
        text: request.message, thinking, toolsEnabled: false, webSearchEnabled: false,
        background: true,
      });
      request.sent = true;
      dequeue(d.id);
      useSendQueue.getState().drain(d.sessionId);
    } catch {
      showToast(text(
        "Could not confirm the rejection. Discussion was not sent; try again.",
        "尚未确认拒绝成功，讨论消息未发送，请重试。",
      ), { tone: "error" });
    } finally {
      request.busy = false;
    }
  }, [decision, thinking, dequeue, text]);
}
