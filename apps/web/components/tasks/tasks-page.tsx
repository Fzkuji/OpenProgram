"use client";

/**
 * Tasks: one page for both ends of unattended work, like Codex's
 * Automations. Inbox lists what always-on Agents and scheduled task runs
 * left for the user; Scheduled lists the tasks, as a list or a calendar.
 */
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ManagePage, ManagePageHeader } from "@/components/ui/manage-page";
import { ScheduledTasks } from "@/components/scheduler/scheduler-page";
import { readTasks, TASKS_KEY } from "@/components/scheduler/scheduler-reads";
import { isAbort, lastKept, readUntilAnswered } from "@/lib/abilities/kept-read";
import { usePendingInbox } from "@/lib/agents/pending-inbox";
import { useTranslation } from "@/lib/i18n";
import { TasksInbox } from "./tasks-inbox";
import { coerceTasksTab, openingTab, readRememberedTab, rememberTab, type TasksHeader, type TasksTab } from "./tasks-tabs";

export function TasksPage() {
  const { text } = useTranslation();
  const asked = coerceTasksTab(useSearchParams()?.get("tab"));
  const inbox = usePendingInbox();
  const [tab, setTab] = useState<TasksTab | null>(null);
  const [scheduledCount, setScheduledCount] = useState<number | undefined>(() => lastKept<unknown[]>(TASKS_KEY)?.length);

  // The page opens once the inbox is known; a link naming a tab moves it later too.
  useEffect(() => {
    if (asked) setTab(asked);
    else if (inbox.loaded) setTab(current => current ?? openingTab(null, inbox.items.length, readRememberedTab()));
  }, [asked, inbox.loaded, inbox.items.length]);

  // The Scheduled tab's count while the Inbox is shown.
  useEffect(() => {
    const controller = new AbortController();
    readUntilAnswered(TASKS_KEY, readTasks, controller.signal)
      .then(data => setScheduledCount((data as unknown[]).length))
      .catch(cause => { if (!isAbort(cause)) console.warn("[tasks] read failed", cause); });
    return () => controller.abort();
  }, []);

  function choose(next: string) {
    const chosen = coerceTasksTab(next);
    if (!chosen) return;
    setTab(chosen);
    rememberTab(chosen);
  }

  const header: TasksHeader = ({ toolbar, actions }) => (
    <ManagePageHeader
      title={text("Tasks", "任务")}
      tabs={[
        { id: "inbox", label: text("Inbox", "收件箱"), count: inbox.loaded ? inbox.items.length : undefined },
        { id: "scheduled", label: text("Scheduled", "定时任务"), count: scheduledCount },
      ]}
      activeTab={tab ?? undefined}
      onTabChange={choose}
      tabLabel={text("Tasks", "任务")}
      toolbar={toolbar}
      actions={actions}
    />
  );

  if (tab === "inbox") return <TasksInbox header={header} items={inbox.items} />;
  if (tab === "scheduled") return <ScheduledTasks header={header} onCountChange={setScheduledCount} />;
  return <ManagePage>{header({})}</ManagePage>;
}
