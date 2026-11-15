"use client";

/**
 * Pending: what the always-on Agents left for the user, across every
 * conversation. "Waiting for your decision" holds approval reports (they
 * stay until the user writes in that conversation); "New" holds findings
 * and finished work (they stay until the conversation is opened). Open
 * goes to the conversation, where the report is a message from the Agent.
 */
import { InboxIcon } from "@/components/animated-icons";
import { AgentAvatarById } from "@/components/agents/agent-avatar";
import { reportKindTag } from "@/components/agents/always-on-labels";
import { Button } from "@/components/ui/button";
import { ManageEmptyState, ManageLoadingState, ManagePage, ManagePageHeader, ManageRow, ManageSummary, managePageStyles as shared } from "@/components/ui/manage-page";
import { groupPending, openPendingItem, usePendingInbox, type PendingItem } from "@/lib/agents/pending-inbox";
import { useTranslation } from "@/lib/i18n";
import { pendingWhen } from "./pending-format";

export function PendingPage() {
  const { text } = useTranslation();
  const { items, loaded } = usePendingInbox();
  const { decide, news } = groupPending(items);

  const row = (item: PendingItem) => (
    <ManageRow
      key={`${item.session_id}:${item.report_id}`}
      icon={<AgentAvatarById agentId={item.agent_id} size={20} />}
      name={item.title}
      meta={<span className={`${shared.badge} ${item.kind === "approval" ? shared.badgeYellow : ""}`}>{reportKindTag(item.kind, text)}</span>}
      description={[item.agent_name, item.body.split("\n")[0], pendingWhen(item.at, text)].filter(Boolean).join(" · ")}
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
      <ManagePageHeader
        title={text("Pending", "待处理")}
        toolbar={loaded && items.length > 0
          ? <ManageSummary>{text(`${items.length} waiting`, `${items.length} 项`)}</ManageSummary>
          : undefined}
      />
      <main className={shared.body}>
        {!loaded ? (
          <ManageLoadingState>{text("Loading…", "正在加载…")}</ManageLoadingState>
        ) : items.length === 0 ? (
          <ManageEmptyState
            icon={<InboxIcon size={22} />}
            title={text("Nothing waiting", "没有待处理的事")}
            description={text(
              "Decisions and findings from always-on Agents show up here until you deal with them.",
              "常驻 Agent 要你决定的事和新发现会列在这里，处理完或看过就消失。",
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
