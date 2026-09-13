"use client";

/** Pending text stays in the queue until dispatch is acknowledged. */
import { useTranslation } from "@/lib/i18n";
import { useSendQueue, type QueuedMessage } from "@/lib/chat/send-queue";
import { steerQueuedMessage } from "@/lib/chat/steer-message";
import { XIcon } from "@/components/animated-icons";
import { CornerDownRight } from "lucide-react";

const EMPTY: QueuedMessage[] = [];

export function QueuedMessages({
  sessionId,
}: {
  sessionId: string | null;
}) {
  const { text } = useTranslation();
  const rows = useSendQueue((s) => sessionId ? s.queues[sessionId] ?? EMPTY : EMPTY);
  const remove = useSendQueue((s) => s.remove);
  if (!sessionId || rows.length === 0) return null;

  return (
    <>
      {rows.map((row) => (
        <div key={row.id} className="message user" data-queued="true">
          <div className="message-content">{row.text}</div>
          <div className="message-actions-footer">
            <div className="queued-badge" role="status">
              {row.injecting
                ? text("Adding to current turn…", "正在补充到当前轮…")
                : row.steerError === "unconfirmed"
                  ? text("Delivery unconfirmed — retry to check", "发送结果待确认，请重试查询")
                  : row.steerCommand && !row.steerError
                    ? text("Adding at the next safe point…", "等待当前操作结束后补充…")
                    : row.steerError === "too_long"
                    ? text("Queued — too long to add to the current turn", "排队中，内容过长，无法补充到当前轮")
                    : row.steerError === "unavailable"
                      ? text("Queued — current turn cannot accept input", "排队中，当前轮暂不接受补充")
                      : row.steerError === "retry"
                        ? text("Queued — could not add input, retry available", "排队中，补充失败，可以重试")
                        : text("Queued", "排队中")}
            </div>
            <div className="message-actions" data-queued-actions="true">
              <span
                className="message-timestamp"
                title={new Date(row.queuedAt).toLocaleString()}
              >
                {new Date(row.queuedAt).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
              <button
                type="button"
                className="message-action-btn"
                disabled={row.injecting || (!!row.steerCommand && !row.steerError)}
                onClick={() => void steerQueuedMessage(sessionId, row.id)}
                title={row.steerCommand ? text("Retry delivery confirmation", "重试确认发送结果") : text("Add to current turn", "补充到当前轮")}
                aria-label={row.steerCommand ? text("Retry delivery confirmation", "重试确认发送结果") : text("Add to current turn", "补充到当前轮")}
              >
                <CornerDownRight size={14} aria-hidden />
              </button>
              <button
                type="button"
                className="message-action-btn"
                disabled={row.injecting || !!row.steerCommand}
                onClick={() => remove(sessionId, row.id)}
                title={text("Remove from queue", "撤回")}
                aria-label={text("Remove from queue", "撤回")}
              >
                <XIcon size={14} />
              </button>
            </div>
          </div>
        </div>
      ))}
    </>
  );
}
