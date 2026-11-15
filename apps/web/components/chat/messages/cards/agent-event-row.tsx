"use client";

/**
 * One-line rows for what agents pass to each other (styles in
 * agent-event-row.css). A background-agent notice draws one row per
 * finished agent ("Scout 已完成，结果交回主 agent", "分支「X」回复了"),
 * a message delivered to a branch draws one row there ("主对话发来消息"),
 * and so does a message or reply another conversation's agent sent.
 * None is something the user typed, so none is a user bubble.
 * Opening a row shows the reply, the task, or the message it carried; a
 * find match inside a closed row opens it.
 */
import { useEffect, useState } from "react";

import type { ChatMsg, NoticeItem } from "@/lib/session-store";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import {
  MAIN_CONVERSATION,
  noticeDetail,
  type Receipt,
} from "@/lib/chat/transcript/agent-notices";
import { onFindReveal } from "@/lib/chat/transcript/conversation-find";

import { renderMarkdown, useMarkdownReady } from "../bubbles/markdown";
import { useRowSessionId } from "../list/transcript-view-scope";

function AgentEventRow({ msgId, findId, icon, label, detail, failed = false }: {
  msgId: string;
  /** The stored message this row is part of, as find names it. */
  findId: string;
  icon: SolarIconName;
  label: string;
  detail: string;
  failed?: boolean;
}) {
  useMarkdownReady();
  const [open, setOpen] = useState(false);
  useEffect(() => (detail ? onFindReveal(findId, () => setOpen(true)) : undefined), [findId, detail]);
  const head = (
    <>
      <SolarIcon name={icon} size={14} motionPreset="none" />
      <span className="agent-event-label">{label}</span>
    </>
  );
  return (
    <div className="message agent-event" data-msg-id={msgId} data-failed={failed ? "1" : "0"}>
      {detail ? (
        <button
          type="button"
          className="agent-event-head"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {head}
          <SolarIcon name={open ? "alt-arrow-down" : "alt-arrow-right"} size={12} motionPreset="none" />
        </button>
      ) : (
        <div className="agent-event-head">{head}</div>
      )}
      {open && detail ? (
        <div
          className="agent-event-detail chat-text"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(detail) }}
        />
      ) : null}
    </div>
  );
}

function shortAddress(address: string): string {
  const [sid, head] = address.split(":");
  return head ? `${sid.slice(0, 8)}:${head.slice(0, 8)}` : address.slice(0, 17);
}

export function AgentNoticeRows({ msg, items }: { msg: ChatMsg; items: NoticeItem[] }) {
  const { text } = useTranslation();
  const sessionId = useRowSessionId();
  return (
    <>
      {items.map((item, i) => {
        const name = item.name || (item.kind === "report" || item.kind === "stalled" ? "" : shortAddress(item.address));
        let label: string;
        let icon: SolarIconName;
        if (item.kind === "report") {
          icon = item.failed ? "danger-triangle" : "ghost";
          const who = name || text("A sub-agent", "子 agent");
          label = item.failed
            ? text(`${who} failed; the error went back to the agent`, `${who} 失败了，错误交回主 agent`)
            : text(`${who} finished; its report went back to the agent`, `${who} 已完成，结果交回主 agent`);
        } else if (item.kind === "reply") {
          icon = item.failed ? "danger-triangle" : "chat-round-dots";
          // An address in another conversation is that conversation's agent, not a branch here.
          const elsewhere = !!sessionId && !!item.address && item.address.split(":")[0] !== sessionId;
          if (elsewhere) {
            label = item.failed
              ? text(`“${name}” failed to reply`, `「${name}」回复失败`)
              : text(`“${name}” replied`, `「${name}」回复了`);
          } else {
            label = item.failed
              ? text(`Branch “${name}” failed to reply`, `分支「${name}」回复失败`)
              : text(`Branch “${name}” replied`, `分支「${name}」回复了`);
          }
        } else if (item.kind === "stalled") {
          icon = "danger-triangle";
          const who = name || text("A sub-agent", "子 agent");
          label = text(`${who} has stalled; it is still running`, `${who} 停滞了，仍在运行`);
        } else {
          icon = "check-circle";
          label = text(`Branch “${name}” is idle`, `分支「${name}」空闲了`);
        }
        return (
          <AgentEventRow
            key={`${msg.id}:${i}`}
            msgId={i === 0 ? msg.id : `${msg.id}:${i}`}
            findId={msg.id}
            icon={icon}
            label={label}
            detail={noticeDetail(msg.content || "", items, i)}
            failed={item.failed || item.kind === "stalled"}
          />
        );
      })}
    </>
  );
}

export function ReceivedMessageRow({ msg, receipt }: { msg: ChatMsg; receipt: Receipt }) {
  const { text } = useTranslation();
  const task = receipt.kind === "task";
  let label: string;
  if (receipt.kind === "reply") {
    label = receipt.from
      ? text(`“${receipt.from}” replied`, `「${receipt.from}」回复了`)
      : text("Received a reply", "收到回复");
  } else if (receipt.from === MAIN_CONVERSATION) {
    label = task
      ? text("Task from the main conversation", "主对话派来任务")
      : text("Message from the main conversation", "主对话发来消息");
  } else if (receipt.from) {
    label = task
      ? text(`Task from “${receipt.from}”`, `「${receipt.from}」派来任务`)
      : text(`Message from “${receipt.from}”`, `「${receipt.from}」发来消息`);
  } else {
    label = task ? text("Received a task", "收到任务") : text("Received a message", "收到消息");
  }
  return (
    <AgentEventRow
      msgId={msg.id}
      findId={msg.id}
      icon={task ? "checklist" : receipt.kind === "reply" ? "chat-round-dots" : "plain-2"}
      label={label}
      detail={receipt.body.trim()}
    />
  );
}
