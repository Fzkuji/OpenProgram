"use client";

/** System events in the transcript: the one-line event row and the
 *  compaction row. A compaction leaves the messages it covered in place,
 *  drawn like any other; where it ran there is one line, and its summary
 *  opens from that line. */
import { useState } from "react";
import { type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "../../bubbles/markdown";
import { formatMessageTime } from "@/lib/chat/transcript/message-time";

export const SYSTEM_EVENT_KINDS = new Set(["compaction", "snip", "event"]);

function formatEventTime(timestamp?: number): string {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
    return "";
  }
  const value = new Date(timestamp > 1e12 ? timestamp : timestamp * 1000);
  return formatMessageTime(value);
}

export function SystemEventRow({ msg }: { msg: ChatMsg }) {
  const label = msg.content;
  const hm = formatEventTime(msg.timestamp);
  return (
    <div className="message system-event" data-msg-id={msg.id} data-kind={msg.kind} data-slot={msg.slot || ""}>
      <span className="system-event-rule" aria-hidden />
      <span className="system-event-text">
        {label}{hm ? ` · ${hm}` : ""}
      </span>
      <span className="system-event-rule" aria-hidden />
    </div>
  );
}

export function CompactionCard({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  useMarkdownReady();
  const [open, setOpen] = useState(false);
  const n = msg.summarisedCount;
  const hm = formatEventTime(msg.timestamp);
  const title = typeof n === "number"
    ? text(`Compacted ${n} earlier messages`, `已压缩 ${n} 条更早的消息`)
    : text("Compacted earlier messages", "已压缩更早的消息");
  return (
    <div className="message compaction-row" data-msg-id={msg.id} data-kind="compaction">
      <div className="compaction-card-bar" data-slot="orig">
        <span className="compaction-card-info">
          {title}{hm ? ` · ${hm}` : ""}
        </span>
        <button
          type="button"
          className="text-hit compaction-summary-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? text("Hide summary", "隐藏摘要") : text("Show summary", "显示摘要")}
        </button>
      </div>
      {open ? (
        <div className="message compaction-card" data-kind="compaction" data-slot="card">
          <div className="compaction-card-body">
            <div
              className="compaction-card-md"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content || "") }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
