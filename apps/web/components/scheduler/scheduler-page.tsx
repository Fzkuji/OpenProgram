"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Bell, CalendarClock, Clock3, Eye, Link2, Pause, Pencil, Play, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SearchInput } from "@/components/ui/search-input";
import { SimpleSelect } from "@/components/ui/select";
import { ManageEmptyState, ManageIconButton, ManagePage, ManagePageHeader, ManageRow, ManageSubnav, ManageSummary, managePageStyles as shared, ManageLoadingState } from "@/components/ui/manage-page";
import { PlusIcon, RefreshCwIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import styles from "./scheduler-page.module.css";
import {
  actionAccessibleName,
  calendarEmptyReason,
  calendarTaskCount,
  coerceSchedulerView,
  filterTasks,
  numberedTasks,
  SCHEDULER_VIEW_STORAGE_KEY,
  shouldShowSuggestions,
  taskCounts,
  formFromTask,
  taskPayload,
} from "./scheduler-view-model.mjs";
import { Input } from "@/components/ui/input";

// Schedule-X renders into the DOM, so the calendar never runs on the server.
const SchedulerCalendar = dynamic(() => import("./scheduler-calendar"), {
  ssr: false,
  loading: () => null,
});

type TaskType = "once" | "recurring" | "monitor" | "webhook";
type TaskFilter = "all" | TaskType;
type SchedulerView = "list" | "calendar";

function readStoredView(): SchedulerView {
  try {
    return coerceSchedulerView(window.localStorage.getItem(SCHEDULER_VIEW_STORAGE_KEY)) as SchedulerView;
  } catch {
    return "list";
  }
}

function storeView(view: SchedulerView) {
  try {
    window.localStorage.setItem(SCHEDULER_VIEW_STORAGE_KEY, view);
  } catch {
    // Storage may be blocked; the choice then lasts for this visit only.
  }
}

interface MemoryRef {
  workspace_id: string;
  memory_id: string;
  topic_path: string;
  content: string;
}

interface Task {
  id: string;
  title: string;
  type: TaskType;
  enabled: boolean;
  prompt?: string;
  command?: string;
  /** Shown to the user when it fires; with wake, the agent acts on it too. */
  reminder?: string;
  wake?: boolean;
  cron?: string;
  run_at?: string;
  memory_refs: Array<Pick<MemoryRef, "workspace_id" | "memory_id">>;
}

const EMPTY_FORM = {
  title: "",
  type: "once" as TaskType,
  prompt: "",
  runAt: "",
  cron: "0 9 * * *",
  memoryId: "",
};

export function SchedulerPage() {
  const { t, text, locale } = useTranslation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [memoryRefs, setMemoryRefs] = useState<MemoryRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [pageError, setPageError] = useState("");
  // The dialog keeps showing pendingDelete through its exit animation, so
  // open state is separate from the task being confirmed.
  const [pendingDelete, setPendingDelete] = useState<Task | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [view, setView] = useState<SchedulerView>("list");
  const [calendarTruncated, setCalendarTruncated] = useState(false);
  // The detail dialog keeps its task through the exit animation, like the
  // delete confirmation.
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  // localStorage is read after mount so the server render and the first
  // client render agree.
  useEffect(() => { setView(readStoredView()); }, []);

  function chooseView(next: SchedulerView) {
    setView(next);
    storeView(next);
  }

  function openDetail(taskId: string) {
    setDetailId(taskId);
    setDetailOpen(true);
  }

  async function reload() {
    setLoading(true);
    setPageError("");
    try {
      const response = await fetch("/api/scheduler/tasks");
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(text("Could not load scheduled tasks", "无法加载定时任务"));
      setTasks(Array.isArray(data) ? data : []);
      setLoadedOnce(true);
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : text("Could not load scheduled tasks", "无法加载定时任务"));
    } finally {
      setLoading(false);
    }
  }

  const loadTasks = useEffectEvent(reload);
  useEffect(() => {
    void loadTasks();
    fetch("/api/memory/refs?limit=200")
      .then((response) => response.ok ? response.json() : [])
      .then((data) => setMemoryRefs(Array.isArray(data) ? data : []))
      .catch(() => setMemoryRefs([]));
  }, []);

  const visible = useMemo(() => filterTasks(tasks, filter, search) as Task[], [filter, search, tasks]);
  const counts = useMemo(() => taskCounts(tasks) as Record<TaskFilter, number>, [tasks]);

  const filters: Array<{ id: TaskFilter; label: string }> = [
    { id: "all", label: text("All tasks", "全部任务") },
    { id: "once", label: text("One-time", "一次性") },
    { id: "recurring", label: text("Recurring", "周期任务") },
    { id: "monitor", label: text("Monitors", "监控任务") },
  ];

  function begin(template?: Partial<typeof EMPTY_FORM>) {
    setEditingId(null);
    setForm({ ...EMPTY_FORM, ...template });
    setError("");
    setOpen(true);
  }

  // The same form edits an existing task; the server keeps its type and the
  // kind of its body, so only its title, time, prompt and memory change.
  function beginEdit(task: Task) {
    setEditingId(task.id);
    setForm(formFromTask(task) as typeof EMPTY_FORM);
    setError("");
    setDetailOpen(false);
    setOpen(true);
  }

  async function saveTask() {
    const existing = editingId ? tasks.find((task) => task.id === editingId) ?? null : null;
    const failed = existing ? text("Save failed", "保存失败") : text("Create failed", "创建失败");
    setSaving(true);
    setError("");
    try {
      const selected = memoryRefs.find((ref) => ref.memory_id === form.memoryId);
      const payload = taskPayload(form, selected, existing);
      const response = await fetch(existing ? `/api/scheduler/tasks/${existing.id}` : "/api/scheduler/tasks", {
        method: existing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error?.message || data.error || failed);
      setTasks((current) => existing ? current.map((row) => row.id === existing.id ? data : row) : [...current, data]);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : failed);
    } finally {
      setSaving(false);
    }
  }

  async function toggle(task: Task) {
    setPageError("");
    try {
      const response = await fetch(`/api/scheduler/tasks/${task.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !task.enabled }),
      });
      const updated = await response.json().catch(() => null);
      if (!response.ok) throw new Error(text("Could not update task", "无法更新任务"));
      setTasks((current) => current.map((row) => row.id === task.id ? updated : row));
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : text("Could not update task", "无法更新任务"));
    }
  }

  async function remove(task: Task): Promise<boolean> {
    setPageError("");
    try {
      const response = await fetch(`/api/scheduler/tasks/${task.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(text("Could not delete task", "无法删除任务"));
      setTasks((current) => current.filter((row) => row.id !== task.id));
      return true;
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : text("Could not delete task", "无法删除任务"));
      return false;
    }
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    const ok = await remove(pendingDelete);
    setDeleting(false);
    if (ok) setDeleteOpen(false);
  }

  const activeCount = tasks.filter((task) => task.enabled).length;
  const filterTabs = filters.map((item) => ({ id: item.id, label: item.label, count: loadedOnce ? counts[item.id] : undefined }));
  // A reload replaces the whole list, so hold row mutations until it lands
  // instead of letting an older snapshot overwrite a newer change.
  const rowsBusy = loading || deleting;
  const detailTask = detailId ? tasks.find((task) => task.id === detailId) ?? null : null;
  const editingTask = editingId ? tasks.find((task) => task.id === editingId) ?? null : null;
  const viewTabs = [
    { id: "list", label: text("List", "列表") },
    { id: "calendar", label: text("Calendar", "日历") },
  ];
  const hiddenWebhooks = visible.length - calendarTaskCount(visible);

  return (
    <ManagePage className={styles.view}>
        <ManagePageHeader
          title={t("nav.scheduler")}
          tabs={viewTabs}
          activeTab={view}
          onTabChange={(id) => chooseView(id as SchedulerView)}
          tabLabel={text("Scheduler view", "定时任务视图")}
          panelId="scheduler-panel"
          toolbar={(
            <>
              {loadedOnce && tasks.length > 0 && (
                <ManageSummary>{text(`${activeCount} of ${tasks.length} active`, `已启用 ${activeCount}/${tasks.length} 个`)}</ManageSummary>
              )}
              <SearchInput
                className={styles.headerSearch}
                placeholder={text("Search tasks...", "搜索任务...")}
                value={search}
                onChange={setSearch}
              />
            </>
          )}
          actions={[
            { label: text("Refresh", "刷新"), onClick: () => void reload(), icon: RefreshCwIcon, iconOnly: true, disabled: loading },
            { label: text("New task", "新建任务"), onClick: () => begin(), icon: PlusIcon, primary: true, disabled: !loadedOnce || loading },
          ]}
        />
        <ManageSubnav
          tabs={filterTabs}
          activeTab={filter}
          onTabChange={(id) => setFilter(id as TaskFilter)}
          ariaLabel={text("Task types", "任务类型")}
          panelId="scheduler-panel"
        />
        {pageError && <div className={shared.errorBar} role="alert">{pageError}</div>}
        <main className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden">
          <div
            id="scheduler-panel"
            role="tabpanel"
            aria-labelledby={`scheduler-panel-tab-${filter}`}
            className={view === "calendar" && loadedOnce
              ? "flex flex-col flex-1 min-w-0 min-h-0 gap-[8px] p-[16px_24px_24px]"
              : styles.content}
          >
            {loading && !loadedOnce ? (
              <ManageLoadingState>{text("Loading…", "加载中…")}</ManageLoadingState>
            ) : !loadedOnce ? (
              <ManageEmptyState
                icon={<CalendarClock />}
                title={text("Scheduled tasks are unavailable", "暂时无法加载定时任务")}
                description={text("Check the error above, then try again.", "请查看上方错误后重试。")}
                action={<Button variant="elevated" onClick={() => void reload()}>{text("Retry", "重试")}</Button>}
              />
            ) : view === "calendar" ? (
              <>
                {hiddenWebhooks > 0 && (
                  <p className={styles.calendarNote} role="status">
                    {hiddenWebhooks === 1
                      ? text("The webhook task runs when it is called, so it has no time on the calendar.", "这个 Webhook 任务在被调用时运行，日历上没有它的时间。")
                      : text(`${hiddenWebhooks} webhook tasks run when they are called, so they have no time on the calendar.`, `${hiddenWebhooks} 个 Webhook 任务在被调用时运行，日历上没有它们的时间。`)}
                  </p>
                )}
                {calendarEmptyReason(tasks, visible, search) && (
                  <p className={styles.calendarNote} role="status">
                    {calendarEmptyReason(tasks, visible, search) === "search"
                      ? text("No matching tasks. Try a different search or task type.", "没有匹配的任务。换个关键词或任务类型试试。")
                      : calendarEmptyReason(tasks, visible, search) === "none"
                        ? text("No tasks yet. Click a day, or a time in the week or day view, to add a one-time task.", "还没有任务。点击某一天，或周视图、日视图里的某个时间，新建一个一次性任务。")
                        : text("No tasks of this type. Click a day to add a one-time task.", "没有此类型的任务。点击某一天可以新建一个一次性任务。")}
                  </p>
                )}
                {calendarTruncated && (
                  <p className={styles.calendarNote} role="status">
                    {text(
                      "Some frequent tasks run more often than the calendar can show here; it lists their first runs in this range.",
                      "部分任务运行过于频繁，日历在此范围内只显示它们最先的几次运行。",
                    )}
                  </p>
                )}
                <SchedulerCalendar
                  key={locale}
                  locale={locale}
                  tasks={visible}
                  onOpenTask={openDetail}
                  onCreateAt={(runAt) => begin({ type: "once", runAt })}
                  onTruncatedChange={setCalendarTruncated}
                />
              </>
            ) : visible.length > 0 ? (
              <section aria-label={text("Scheduled tasks", "定时任务")}>
                <div className={shared.sectionHeader}>
                  <span>{filterLabel(filter, text)}</span>
                  <span>{visible.length}</span>
                </div>
                <div className="flex flex-col gap-[2px]">
                  {numberedTasks(visible).map(({ task, number }: { task: Task; number: number }) => (
                    <ManageRow
                      key={task.id}
                      icon={<span className={styles.taskIndex}>{number}</span>}
                      name={task.title}
                      onClick={() => openDetail(task.id)}
                      description={(
                        <span className={`${styles.description} flex items-baseline gap-[8px] min-w-0`}>
                          <code className={styles.schedule}>{formatSchedule(task, text, locale)}</code>
                          {(task.prompt || task.command || task.reminder) && <span>{task.prompt || task.command || task.reminder}</span>}
                        </span>
                      )}
                      meta={(
                        <>
                          <span className={shared.badge}>{typeLabel(task.type, text)}</span>
                          {task.reminder !== undefined && (
                            <span className={shared.badge}>
                              {task.wake ? text("Reminder · wakes agent", "提醒 · 唤醒 Agent") : text("Reminder", "提醒")}
                            </span>
                          )}
                          <span className={`${shared.badge} ${task.enabled ? shared.badgeGreen : ""}`}>
                            {task.enabled ? text("Active", "已启用") : text("Paused", "已暂停")}
                          </span>
                          {!!task.memory_refs?.length && (
                            <span className={styles.memory}><Link2 aria-hidden />{text(`${task.memory_refs.length} memory ${task.memory_refs.length === 1 ? "reference" : "references"}`, `${task.memory_refs.length} 条记忆引用`)}</span>
                          )}
                        </>
                      )}
                      actions={(
                        <>
                          <ManageIconButton
                            disabled={rowsBusy}
                            onClick={() => void toggle(task)}
                            label={actionAccessibleName(task.enabled ? text("Pause", "暂停") : text("Resume", "恢复"), task.title)}
                            tooltip={task.enabled ? text("Pause", "暂停") : text("Resume", "恢复")}
                          >
                            {task.enabled ? <Pause /> : <Play />}
                          </ManageIconButton>
                          <ManageIconButton
                            danger
                            disabled={rowsBusy}
                            onClick={() => { setPendingDelete(task); setDeleteOpen(true); }}
                            label={actionAccessibleName(text("Delete", "删除"), task.title)}
                            tooltip={text("Delete", "删除")}
                          >
                            <Trash2 />
                          </ManageIconButton>
                        </>
                      )}
                    />
                  ))}
                </div>
              </section>
            ) : shouldShowSuggestions(tasks, filter, search) ? (
              <Suggestions onChoose={begin} text={text} />
            ) : (
              <ManageEmptyState
                compact
                icon={<CalendarClock />}
                title={search.trim() ? text("No matching tasks", "没有匹配的任务") : text("Nothing here yet", "这里还没有任务")}
                description={search.trim()
                  ? text("Try a different search or task type.", "换个关键词或任务类型试试。")
                  : text("Tasks of this type will appear here.", "此类型的任务会显示在这里。")}
              />
            )}
          </div>
        </main>

      <Dialog open={detailOpen && !!detailTask} onOpenChange={setDetailOpen}>
        <DialogContent>
          {detailTask && (
            <>
              <DialogHeader>
                <DialogTitle>{detailTask.title}</DialogTitle>
                <DialogDescription>
                  {typeLabel(detailTask.type, text)} · {detailTask.enabled ? text("Active", "已启用") : text("Paused", "已暂停")}
                </DialogDescription>
              </DialogHeader>
              <dl className={`${styles.detail} grid grid-cols-[max-content_minmax(0,_1fr)] gap-[8px_16px]`}>
                <dt>{detailTask.type === "once" ? text("Runs at", "执行时间") : detailTask.type === "webhook" ? text("Runs", "运行") : text("Schedule", "计划")}</dt>
                <dd>{formatSchedule(detailTask, text, locale)}</dd>
                {detailBody(detailTask, text).map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt>{label}</dt>
                    <dd className={styles.detailBody}>{value}</dd>
                  </div>
                ))}
                {!!detailTask.memory_refs?.length && (
                  <>
                    <dt>{text("Memory", "记忆")}</dt>
                    <dd>{text(`${detailTask.memory_refs.length} memory ${detailTask.memory_refs.length === 1 ? "reference" : "references"}`, `${detailTask.memory_refs.length} 条记忆引用`)}</dd>
                  </>
                )}
              </dl>
              <DialogFooter>
                <Button
                  variant="destructive"
                  disabled={rowsBusy}
                  onClick={() => { setDetailOpen(false); setPendingDelete(detailTask); setDeleteOpen(true); }}
                >
                  <Trash2 aria-hidden />{text("Delete", "删除")}
                </Button>
                <Button variant="elevated" disabled={rowsBusy} onClick={() => beginEdit(detailTask)}>
                  <Pencil aria-hidden />{text("Edit", "编辑")}
                </Button>
                <Button variant="elevated" disabled={rowsBusy} onClick={() => void toggle(detailTask)}>
                  {detailTask.enabled ? <Pause aria-hidden /> : <Play aria-hidden />}
                  {detailTask.enabled ? text("Pause", "暂停") : text("Resume", "恢复")}
                </Button>
                <Button onClick={() => setDetailOpen(false)}>{text("Done", "完成")}</Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={(next) => { if (!deleting) setDeleteOpen(next); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{text("Delete scheduled task", "删除定时任务")}</DialogTitle>
            <DialogDescription>
              {text(`“${pendingDelete?.title ?? ""}” will stop running and be removed.`, `“${pendingDelete?.title ?? ""}”将停止运行并被删除。`)}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="elevated" disabled={deleting} onClick={() => setDeleteOpen(false)}>{text("Cancel", "取消")}</Button>
            <Button variant="destructive" disabled={deleting} onClick={() => void confirmDelete()}>{deleting ? text("Deleting…", "删除中…") : text("Delete", "删除")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingTask ? text("Edit scheduled task", "编辑定时任务") : text("Create scheduled task", "创建定时任务")}</DialogTitle>
            <DialogDescription>{editingTask
              ? text(`${typeLabel(editingTask.type, text)} task. Changes apply from its next run.`, `${typeLabel(editingTask.type, text)}任务。更改从下一次运行起生效。`)
              : text("The task runs with the current owner's permissions, frozen when it is created.", "任务按创建时冻结的当前所有者权限执行。")}</DialogDescription>
          </DialogHeader>
          <div className={`${styles.form} flex flex-col gap-[13px]`}>
            <label>{text("Title", "标题")}<Input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
            {!editingTask && (
              <label>{text("Type", "类型")}<SimpleSelect label={text("Type", "类型")} className="w-full" value={form.type} onValueChange={(value) => setForm({ ...form, type: value })} options={[
                { value: "once" as TaskType, label: text("One-time", "一次性") },
                { value: "recurring" as TaskType, label: text("Recurring", "周期") },
                { value: "monitor" as TaskType, label: text("Monitor", "监控") },
              ]} /></label>
            )}
            {form.type === "webhook" ? null : form.type === "once" ? (
              <label>{text("Run at", "执行时间")}<Input type="datetime-local" value={form.runAt} onChange={(event) => setForm({ ...form, runAt: event.target.value })} /></label>
            ) : (
              <label>{text("Cron expression", "Cron 表达式")}<Input value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} placeholder="0 9 * * 1-5" /></label>
            )}
            {(!editingTask || typeof editingTask.prompt === "string") && <>
            <label>{text("Task prompt", "任务提示")}<textarea value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} rows={4} /></label>
            <label>{text("Memory context (optional)", "记忆上下文（可选）")}<SimpleSelect label={text("Memory context", "记忆上下文")} className="w-full" contentClassName="max-w-[min(520px,90vw)]" value={form.memoryId} onValueChange={(value) => setForm({ ...form, memoryId: value })} options={[
              { value: "", label: text("No Memory reference", "不引用记忆") },
              ...memoryRefs.map((ref) => ({ value: ref.memory_id, label: `${ref.topic_path} · ${ref.content.slice(0, 70)}` })),
            ]} /></label>
            </>}
            {error && <div className={styles.error} role="alert">{error}</div>}
          </div>
          <DialogFooter>
            <Button variant="elevated" onClick={() => setOpen(false)}>{text("Cancel", "取消")}</Button>
            <Button onClick={() => void saveTask()} disabled={saving}>{editingTask
              ? (saving ? text("Saving…", "保存中…") : text("Save", "保存"))
              : (saving ? text("Creating…", "创建中…") : text("Create", "创建"))}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ManagePage>
  );
}

function typeLabel(type: TaskType, text: (en: string, zh: string) => string) {
  if (type === "once") return text("One-time", "一次性");
  if (type === "monitor") return text("Monitor", "监控");
  if (type === "webhook") return text("Webhook", "Webhook");
  return text("Recurring", "周期");
}

function filterLabel(filter: TaskFilter, text: (en: string, zh: string) => string) {
  if (filter === "all") return text("All scheduled tasks", "全部定时任务");
  if (filter === "once") return text("One-time tasks", "一次性任务");
  if (filter === "monitor") return text("Monitors", "监控任务");
  return text("Recurring tasks", "周期任务");
}

// The type badge already names the kind of task; the schedule column only
// carries the time or cron expression.
function formatSchedule(task: Task, text: (en: string, zh: string) => string, locale: string) {
  if (task.type === "once") return task.run_at ? new Date(task.run_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" }) : text("Not scheduled", "未设置时间");
  if (task.type === "webhook") return text("When called", "被调用时");
  return task.cron || "";
}

// What the task does when it runs: its prompt, command or reminder.
function detailBody(task: Task, text: (en: string, zh: string) => string): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  if (task.prompt) rows.push([text("Prompt", "提示"), task.prompt]);
  if (task.command) rows.push([text("Command", "命令"), task.command]);
  if (task.reminder !== undefined) {
    rows.push([task.wake ? text("Reminder · wakes agent", "提醒 · 唤醒 Agent") : text("Reminder", "提醒"), task.reminder]);
  }
  return rows;
}

function Suggestions({ onChoose, text }: {
  onChoose: (template?: Partial<typeof EMPTY_FORM>) => void;
  text: (en: string, zh: string) => string;
}) {
  const rows = [
    { icon: <Bell />, title: text("Daily brief", "每日简报"), schedule: text("Weekdays at 8:00 AM", "工作日 8:00"), form: { type: "recurring" as TaskType, title: text("Daily brief", "每日简报"), cron: "0 8 * * 1-5", prompt: text("Summarize today's priorities.", "总结今天的优先事项。") } },
    { icon: <Clock3 />, title: text("Weekly review", "每周回顾"), schedule: text("Fridays at 4:00 PM", "周五 16:00"), form: { type: "recurring" as TaskType, title: text("Weekly review", "每周回顾"), cron: "0 16 * * 5", prompt: text("Review this week's work and open decisions.", "回顾本周工作和未决事项。") } },
    { icon: <Eye />, title: text("Follow-up monitor", "跟进监控"), schedule: text("Weekdays at 9:00 AM", "工作日 9:00"), form: { type: "monitor" as TaskType, title: text("Follow-up monitor", "跟进监控"), cron: "0 9 * * 1-5", prompt: text("Check whether the selected follow-up needs attention.", "检查选定的跟进事项是否需要处理。") } },
  ];
  return (
    <section className="w-full max-w-[960px] m-[0_auto] p-[0_0_24px]">
      <ManageEmptyState
        compact
        icon={<CalendarClock />}
        title={text("No scheduled tasks", "还没有定时任务")}
        description={text("Run a prompt once, on a schedule, or as a monitor. Create a task or start from a suggestion.", "让提示词单次、周期或以监控方式运行。创建任务，或从建议开始。")}
        action={<Button onClick={() => onChoose()}><PlusIcon size={16} aria-hidden />{text("New task", "新建任务")}</Button>}
      />
      <div className={shared.sectionHeader}>
        <span>{text("Start from a suggestion", "从建议开始")}</span>
      </div>
      <div className={`${styles.suggestionList} grid grid-cols-[repeat(3,_minmax(0,_1fr))] gap-[12px]`}>
        {rows.map((row) => (
          <article key={row.title} className={styles.suggestionRow}>
            <span className={styles.suggestionIcon} aria-hidden="true">{row.icon}</span>
            <h3>{row.title}</h3>
            <p className={styles.suggestionSchedule}>{row.schedule}</p>
            <p className={styles.suggestionPrompt}>{row.form.prompt}</p>
            <Button variant="elevated" aria-label={actionAccessibleName(text("Use", "使用"), row.title)} onClick={() => onChoose(row.form)}>{text("Use", "使用")}</Button>
          </article>
        ))}
      </div>
    </section>
  );
}
