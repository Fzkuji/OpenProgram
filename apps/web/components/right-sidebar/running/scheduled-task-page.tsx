"use client";

/** A Scheduler task set from this conversation: what it is, then its runs. */
import { useState } from "react";
import type { Routine } from "@/lib/chat/conversation-work";
import { navigate } from "@/lib/nav/navigate";
import { updatedTime } from "../debugger/debugger-presentation";
import type { ActivityPageProps, ActivityPanelContext } from "./activity-page-types";
import { Calendar, Copy } from "lucide-react";
import { activityGoneFrame, ActivityPageFrame } from "./activity-page";
import { axisFor, type ActivityAxis } from "./activity-page-geometry";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityNote, ActivityRow } from "./activity-page-row";
import { RunList } from "./run-list";
import { Glyph } from "./running-rows";
import { routineRunLabel, routineSchedule } from "./running-format";

const taskGlyph = (task: Routine | undefined) => <Glyph name={task?.type === "webhook" ? "link" : "calendar"} tone="plan" large />;

/** The row's line: what the task does, in as few words as it has. */
function taskLine(task: Routine, context: ActivityPanelContext): string {
  const { text } = context;
  if (task.reminder) return `${text("Reminder", "提醒")}: ${task.reminder}`;
  if (task.prompt) return `${text("Task", "任务")}: ${task.prompt}`;
  if (task.command) return `${text("Command", "命令")}: ${task.command}`;
  return text("Task details", "任务详情");
}

function TaskDetails({ task, context }: { task: Routine; context: ActivityPanelContext }) {
  const { text, copy, copiedKey } = context;
  const body = task.prompt || task.reminder || "";
  const copied = text("Copied", "已复制");
  return <>
    {task.hook_url ? <ActivityActionRow icon={Copy} label={text("Copy address", "复制地址")} note={copiedKey === "url" ? copied : task.hook_url} onClick={() => copy("url", task.hook_url!)} /> : null}
    {task.hook_key ? <ActivityActionRow icon={Copy} label={text("Copy key", "复制密钥")} note={copiedKey === "key" ? copied : text("The key is not shown here", "这里不显示密钥")} onClick={() => copy("key", task.hook_key!)} /> : null}
    <ActivityDetail>
      {task.type === "webhook" ? <ActivityNote>{text(
        "Send the key in the X-OpenProgram-Key header, or give it to GitHub as the webhook's secret. The request body reaches the run as data, never as instructions.",
        "在 X-OpenProgram-Key 请求头里带上密钥，或把它作为 GitHub webhook 的 secret。请求内容只作为数据交给这次运行，不会被当成指令。",
      )}</ActivityNote> : null}
      {task.watch ? <ActivityKeyValue label={text("Runs when this check's output changes", "这个检查的输出变化时运行")} value={task.watch} mono /> : null}
      {body ? <ActivityKeyValue label={task.reminder ? text("Reminder", "提醒") : text("Task", "任务")} value={body} /> : null}
      {task.command ? <ActivityKeyValue label={text("Command", "命令")} value={task.command} mono /> : null}
      {task.notes ? <ActivityKeyValue label={text("Notes", "备注")} value={task.notes} /> : null}
      <ActivityKeyValue label={text("Created", "创建时间")} value={updatedTime(task.created_at)} />
      <ActivityKeyValue label={text("Task ID", "任务 ID")} value={task.id} />
    </ActivityDetail>
  </>;
}

export function ScheduledTaskPage({ target, context }: ActivityPageProps<"routine">) {
  const { text, work } = context;
  const [axis, setAxis] = useState<ActivityAxis>(() => axisFor([], []));
  const task = work?.routines.find(entry => entry.id === target.taskId);
  if (!task) return work ? activityGoneFrame(text) : <ActivityPageFrame kind={taskGlyph(task)} title={text("Scheduled task", "定时任务")} status={text("Loading…", "正在读取…")} axis={null}>{null}</ActivityPageFrame>;
  const last = task.runs?.[0];
  return <ActivityPageFrame kind={taskGlyph(task)} title={task.title} status={[routineSchedule(task, text), last && routineRunLabel(last, text)].filter(Boolean).join(" · ")} tone={last?.status === "failed" ? "warn" : "muted"} axis={axis}>
    <ActivityActionRow icon={Calendar} label={text("Open in Scheduler", "在定时任务中打开")} note={text("Change or pause this task", "修改或暂停这个任务")} onClick={() => navigate("/scheduler")} />
    <ActivityRow id="task-details" status={taskLine(task, context)}>{() => <TaskDetails task={task} context={context} />}</ActivityRow>
    <RunList source={{ kind: "task", taskId: task.id }} context={context} onAxis={setAxis} />
  </ActivityPageFrame>;
}
