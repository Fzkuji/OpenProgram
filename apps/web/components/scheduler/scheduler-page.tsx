"use client";

import { useEffect, useMemo, useState } from "react";
import { Bell, CalendarClock, Clock3, Eye, Link2, Pause, Play, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SearchInput } from "@/components/ui/search-input";
import { ManagePageHeader, ManageRow, managePageStyles as shared } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";
import styles from "./scheduler-page.module.css";
import {
  actionAccessibleName,
  filterTasks,
  numberedTasks,
  shouldShowSuggestions,
  taskCounts,
} from "./scheduler-view-model.mjs";

type TaskType = "once" | "recurring" | "monitor";
type TaskFilter = "all" | TaskType;

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
  const { t, text } = useTranslation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [memoryRefs, setMemoryRefs] = useState<MemoryRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [pageError, setPageError] = useState("");

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

  useEffect(() => {
    void reload();
    fetch("/api/memory/refs?limit=200")
      .then((response) => response.ok ? response.json() : [])
      .then((data) => setMemoryRefs(Array.isArray(data) ? data : []))
      .catch(() => setMemoryRefs([]));
  }, []);

  const visible = useMemo(() => filterTasks(tasks, filter, search) as Task[], [filter, search, tasks]);
  const counts = useMemo(() => taskCounts(tasks) as Record<TaskFilter, number>, [tasks]);

  const filters: Array<{ id: TaskFilter; label: string; icon: React.ReactNode }> = [
    { id: "all", label: text("All tasks", "全部任务"), icon: <CalendarClock /> },
    { id: "once", label: text("One-time", "一次性"), icon: typeIcon("once") },
    { id: "recurring", label: text("Recurring", "周期任务"), icon: <Clock3 /> },
    { id: "monitor", label: text("Monitors", "监控任务"), icon: typeIcon("monitor") },
  ];

  function begin(template?: Partial<typeof EMPTY_FORM>) {
    setForm({ ...EMPTY_FORM, ...template });
    setError("");
    setOpen(true);
  }

  async function createTask() {
    setSaving(true);
    setError("");
    try {
      const selected = memoryRefs.find((ref) => ref.memory_id === form.memoryId);
      const payload = {
        title: form.title,
        type: form.type,
        prompt: form.prompt,
        ...(form.type === "once"
          ? { run_at: form.runAt ? new Date(form.runAt).toISOString() : "" }
          : { cron: form.cron }),
        memory_refs: selected ? [{
          workspace_id: selected.workspace_id,
          memory_id: selected.memory_id,
        }] : [],
      };
      const response = await fetch("/api/scheduler/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error?.message || text("Create failed", "创建失败"));
      setTasks((current) => [...current, data]);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : text("Create failed", "创建失败"));
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

  async function remove(task: Task) {
    if (!confirm(text(`Delete “${task.title}”?`, `删除“${task.title}”？`))) return;
    setPageError("");
    try {
      const response = await fetch(`/api/scheduler/tasks/${task.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(text("Could not delete task", "无法删除任务"));
      setTasks((current) => current.filter((row) => row.id !== task.id));
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : text("Could not delete task", "无法删除任务"));
    }
  }

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <div className={`${shared.view} ${styles.view}`}>
        <ManagePageHeader
          title={t("nav.scheduler")}
          toolbar={(
            <SearchInput
              className={styles.headerSearch}
              placeholder={text("Search tasks...", "搜索任务...")}
              value={search}
              onChange={setSearch}
            />
          )}
          actions={[{ label: text("Create", "创建"), onClick: () => begin(), primary: true }]}
        />
        {pageError && <div className={shared.errorBar} role="alert">{pageError}</div>}
        <main className={styles.layout}>
          <nav className={styles.nav} aria-label={text("Task types", "任务类型")}>
            {filters.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`${shared.tabBtn} ${styles.navButton} ${filter === item.id ? shared.active : ""}`}
                onClick={() => setFilter(item.id)}
                aria-current={filter === item.id ? "page" : undefined}
              >
                <span className={shared.tabIcon} aria-hidden="true">{item.icon}</span>
                <span className={styles.filterLabel}>{item.label}</span>
                <span className={shared.tabCount}>{counts[item.id]}</span>
              </button>
            ))}
          </nav>
          <div className={styles.content}>

            {loading ? (
              <div className={shared.empty}>{text("Loading…", "加载中…")}</div>
            ) : !loadedOnce ? null : visible.length > 0 ? (
              <section aria-label={text("Scheduled tasks", "定时任务")}>
                <div className={styles.sectionHeader}>
                  <span>{filterLabel(filter, text)}</span>
                  <span>{visible.length}</span>
                </div>
                <div className={styles.list}>
                  {numberedTasks(visible).map(({ task, number }: { task: Task; number: number }) => (
                    <ManageRow
                      key={task.id}
                      icon={<span className={styles.taskIndex}>{number}</span>}
                      name={task.title}
                      description={(
                        <span className={styles.description}>
                          <code className={styles.schedule}>{formatSchedule(task, text)}</code>
                          {(task.prompt || task.command) && <span>{task.prompt || task.command}</span>}
                        </span>
                      )}
                      meta={(
                        <>
                          <span className={shared.badge}>{typeLabel(task.type, text)}</span>
                          <span className={`${shared.badge} ${task.enabled ? shared.badgeGreen : ""}`}>
                            {task.enabled ? text("Active", "启用") : text("Paused", "暂停")}
                          </span>
                          {!!task.memory_refs?.length && (
                            <span className={styles.memory}><Link2 />{task.memory_refs.length} MemoryRef</span>
                          )}
                        </>
                      )}
                      actions={(
                        <div className={styles.actions}>
                          <button onClick={() => void toggle(task)} type="button" aria-label={actionAccessibleName(task.enabled ? text("Pause", "暂停") : text("Resume", "恢复"), task.title)} title={task.enabled ? text("Pause", "暂停") : text("Resume", "恢复")}>
                            {task.enabled ? <Pause /> : <Play />}
                          </button>
                          <button onClick={() => void remove(task)} type="button" aria-label={actionAccessibleName(text("Delete", "删除"), task.title)} title={text("Delete", "删除")}><Trash2 /></button>
                        </div>
                      )}
                    />
                  ))}
                </div>
              </section>
            ) : shouldShowSuggestions(tasks, filter, search) ? (
              <Suggestions onChoose={begin} text={text} />
            ) : (
              <div className={shared.empty}>{text("No matching tasks", "没有匹配的任务")}</div>
            )}
          </div>
        </main>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{text("Create scheduled task", "创建定时任务")}</DialogTitle>
            <DialogDescription>{text("The task runs under the current owner's frozen permission boundary.", "任务在当前 owner 的冻结权限边界内执行。")}</DialogDescription>
          </DialogHeader>
          <div className={styles.form}>
            <label>{text("Title", "标题")}<input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
            <label>{text("Type", "类型")}<select value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value as TaskType })}>
              <option value="once">{text("One time", "一次性")}</option>
              <option value="recurring">{text("Recurring", "周期")}</option>
              <option value="monitor">{text("Monitor", "监控")}</option>
            </select></label>
            {form.type === "once" ? (
              <label>{text("Run at", "执行时间")}<input type="datetime-local" value={form.runAt} onChange={(event) => setForm({ ...form, runAt: event.target.value })} /></label>
            ) : (
              <label>{text("Cron expression", "Cron 表达式")}<input value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} placeholder="0 9 * * 1-5" /></label>
            )}
            <label>{text("Task prompt", "任务提示")}<textarea value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} rows={4} /></label>
            <label>{text("Memory context (optional)", "Memory 上下文（可选）")}<select value={form.memoryId} onChange={(event) => setForm({ ...form, memoryId: event.target.value })}>
              <option value="">{text("No Memory reference", "不引用 Memory")}</option>
              {memoryRefs.map((ref) => <option value={ref.memory_id} key={ref.memory_id}>{ref.topic_path} · {ref.content.slice(0, 70)}</option>)}
            </select></label>
            {error && <div className={styles.error} role="alert">{error}</div>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>{text("Cancel", "取消")}</Button>
            <Button onClick={() => void createTask()} disabled={saving}>{saving ? text("Creating…", "创建中…") : text("Create", "创建")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function typeIcon(type: TaskType) {
  if (type === "once") return <Bell />;
  if (type === "monitor") return <Eye />;
  return <CalendarClock />;
}

function typeLabel(type: TaskType, text: (en: string, zh: string) => string) {
  if (type === "once") return text("One-time", "一次性");
  if (type === "monitor") return text("Monitor", "监控");
  return text("Recurring", "周期");
}

function filterLabel(filter: TaskFilter, text: (en: string, zh: string) => string) {
  if (filter === "all") return text("All scheduled tasks", "全部定时任务");
  if (filter === "once") return text("One-time tasks", "一次性任务");
  if (filter === "monitor") return text("Monitors", "监控任务");
  return text("Recurring tasks", "周期任务");
}

function formatSchedule(task: Task, text: (en: string, zh: string) => string) {
  if (task.type === "once" && task.run_at) return new Date(task.run_at).toLocaleString();
  if (task.type === "monitor") return `${text("Monitor", "监控")} · ${task.cron}`;
  return `${text("Recurring", "周期")} · ${task.cron}`;
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
    <section className={styles.suggestions}>
      <div className={styles.emptyIntro}>
        <CalendarClock aria-hidden="true" />
        <div>
          <strong>{text("No scheduled tasks", "还没有定时任务")}</strong>
          <span>{text("Create a task or start from a suggestion.", "创建任务，或从建议开始。")}</span>
        </div>
        <Button onClick={() => onChoose()}>{text("Create task", "创建任务")}</Button>
      </div>
      <div className={styles.sectionHeader}>
        <span>{text("Start from a suggestion", "从建议开始")}</span>
      </div>
      <div className={styles.suggestionList}>
        {rows.map((row) => (
          <article key={row.title} className={styles.suggestionRow}>
            <span className={styles.suggestionIcon} aria-hidden="true">{row.icon}</span>
            <h3>{row.title}</h3>
            <p className={styles.suggestionSchedule}>{row.schedule}</p>
            <p className={styles.suggestionPrompt}>{row.form.prompt}</p>
            <Button size="sm" variant="outline" aria-label={actionAccessibleName(text("Use", "使用"), row.title)} onClick={() => onChoose(row.form)}>{text("Use", "使用")}</Button>
          </article>
        ))}
      </div>
    </section>
  );
}
