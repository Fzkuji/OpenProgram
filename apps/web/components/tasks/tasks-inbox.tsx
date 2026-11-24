"use client";

/**
 * The Tasks page's Inbox: what always-on Agents and scheduled task runs left
 * for the user, across every conversation. "Waiting for your decision" holds
 * approval reports (they stay until the user writes in that conversation);
 * "New" holds findings, finished work and scheduled results (they stay until
 * the conversation is opened). A row's icon says where it came from: the
 * Agent's avatar, or the calendar for a scheduled task. Open goes to the
 * conversation, where the report is a message.
 */
import { CalendarDays } from "lucide-react";
import { InboxIcon } from "@/components/animated-icons";
import { AgentAvatarById } from "@/components/agents/agent-avatar";
import { reportKindTag } from "@/components/agents/always-on-labels";
import { Button } from "@/components/ui/button";
import { ManageEmptyState, ManagePage, ManageRow, ManageSummary, managePageStyles as shared } from "@/components/ui/manage-page";
import { groupPending, openPendingItem, type PendingItem } from "@/lib/agents/pending-inbox";
import { useTranslation } from "@/lib/i18n";
import { pendingWhen } from "./inbox-format";
import type { TasksHeader } from "./tasks-tabs";

export function TasksInbox({ header, items }: { header: TasksHeader; items: PendingItem[] }) {
  const { text } = useTranslation();
  const { decide, news } = groupPending(items);

  const row = (item: PendingItem) => (
    <ManageRow
      key={`${item.session_id}:${item.report_id}`}
      icon={item.task_id
        ? <CalendarDays size={20} aria-label={text("Scheduled task", "定时任务")} />
        : <AgentAvatarById agentId={item.agent_id} size={20} />}
      name={item.title}
      meta={<span className={`${shared.badge} ${item.kind === "approval" ? shared.badgeYellow : ""}`}>{reportKindTag(item.kind, text)}</span>}
      description={[
        item.task_id ? text("Scheduled task", "定时任务") : item.agent_name,
        item.body.split("\n")[0],
        pendingWhen(item.at, text),
      ].filter(Boolean).join(" · ")}
      onClick={() => openPendingItem(item)}
      actions={<Button type="button" variant="elevated" size="xs" onClick={() => openPendingItem(item)}>{text("Open", "打开")}</Button>}
    />
  );
  const group = (name: string, rows: PendingItem[]) => rows.length > 0 && (
    <section className="mb-[16px]">
      <h2 className={shared.sectionHeader}><span>{name}</span><span>{rows.length}</span></h2>
      {rows.map(row)}
    </section>
  );

  return (
    <ManagePage>
      {header({
        toolbar: items.length > 0
          ? <ManageSummary>{text(`${items.length} need you`, `${items.length} 项等你处理`)}</ManageSummary>
          : undefined,
      })}
      <main className={shared.body}>
        {items.length === 0 ? (
          <ManageEmptyState
            icon={<InboxIcon size={20} />}
            title={text("Nothing waiting", "没有等你处理的事")}
            description={text(
              "Decisions and findings from always-on Agents and scheduled tasks show up here until you deal with them.",
              "常驻 Agent 和定时任务要你决定的事和新发现会列在这里，处理完或看过就消失。",
            )}
          />
        ) : (
          <>
            {group(text("Waiting for your decision", "等你决定"), decide)}
            {group(text("New", "新发现"), news)}
          </>
        )}
      </main>
    </ManagePage>
  );
}
