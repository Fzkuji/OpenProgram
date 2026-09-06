"use client";

import { useCallback, useRef } from "react";
import { useTranslation } from "@/lib/i18n";
import type { PendingDecision } from "@/lib/session-store/types";
import { postExecutionCommand, type WaitCommand } from "@/lib/net/execution-client";
import { enqueueMessage, useSendQueue } from "@/lib/state/send-queue";
import { showToast } from "@/lib/format-utils/toast";

interface Options {
  decision: PendingDecision | null;
  thinking: string;
  dequeue(id: string): void;
}

export function useDecisionDiscussion({ decision, thinking, dequeue }: Options) {
  const { text } = useTranslation();
  const requests = useRef(new Map<string, { command: WaitCommand; busy: boolean; sent: boolean }>());

  return useCallback(async () => {
    if (!decision?.sessionId || !decision.executionId) return;
    const d = decision;
    const message = text(
      `I reject this request${d.tool ? ` (${d.tool})` : ""} and want to discuss it. Do not execute or retry it. Please respond to me first and wait for my next message.`,
      `我拒绝这次申请${d.tool ? `（${d.tool}）` : ""}，希望先讨论。不要执行或重试这次操作。请先回复我，再等待我的下一条消息。`,
    );
    let request = requests.current.get(d.id);
    if (!request) {
      request = { busy: false, sent: false, command: {
        type: "execution.command", action: "execution.wait.decline",
        command_id: `web-discuss-${crypto.randomUUID()}`,
        execution_id: d.executionId, expected_version: d.expectedVersion,
        payload: { wait_id: d.id, generation: d.waitGeneration, reason: message },
      } };
      requests.current.set(d.id, request);
    }
    if (request.busy || request.sent) return;
    request.busy = true;
    try {
      const result = await postExecutionCommand(request.command, AbortSignal.timeout(15000));
      if (result.command_id !== request.command.command_id || result.status !== "applied") {
        throw new Error("Rejection was not confirmed");
      }
      // Use the existing session queue: the declined execution may not have
      // cleared in the WebSocket projection yet. Drafts and attachments stay put.
      enqueueMessage(d.sessionId, {
        text: message, thinking, toolsEnabled: false, webSearchEnabled: false,
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
