"use client";

import { useEffect, useMemo, useState } from "react";
import { Bell, CalendarClock, Clock3, Eye, Link2, Pause, Play, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation } from "@/lib/i18n";
import styles from "./scheduler-page.module.css";

type TaskType = "once" | "recurring" | "monitor";

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
  const { text } = useTranslation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [memoryRefs, setMemoryRefs] = useState<MemoryRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [search, setSearch] = useState("");
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

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return tasks;
    return tasks.filter((task) => [task.title, task.prompt, task.command, task.cron]
      .filter(Boolean).join(" ").toLowerCase().includes(query));
  }, [search, tasks]);

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
      <div className={styles.view}>
        <header className={styles.topbar}>
          <span className={styles.title}>{text("Scheduled tasks", "定时任务")}</span>
          <Button onClick={() => begin()}><Plus />{text("Create", "创建")}</Button>
        </header>
        <main className={styles.content}>
          <div className={styles.intro}>
            <h1>{text("Scheduled tasks", "定时任务")}</h1>
            <p>{text(
              "Schedule agent work, reminders, and recurring monitors. Tasks can read selected Memory items when they run.",
              "安排 Agent 任务、提醒和周期监控。任务执行时可以读取选定的 Memory 内容。",
            )}</p>
          </div>
          <SearchInput
            className={styles.search}
            placeholder={text("Search scheduled tasks", "搜索定时任务")}
            value={search}
            onChange={setSearch}
          />
          {pageError && <div className={styles.error} role="alert">{pageError}</div>}

          {loading ? (
            <div className={styles.empty}>{text("Loading…", "加载中…")}</div>
          ) : !loadedOnce ? null : visible.length > 0 ? (
            <section className={styles.list} aria-label={text("Scheduled tasks", "定时任务")}>
              {visible.map((task) => (
                <article className={styles.task} key={task.id}>
                  <div className={styles.rail} aria-hidden="true">{typeIcon(task.type)}</div>
                  <div className={styles.taskBody}>
                    <div className={styles.taskHeader}>
                      <h2>{task.title}</h2>
                      <span className={`${styles.state} ${task.enabled ? styles.active : ""}`}>
                        {task.enabled ? text("Active", "启用") : text("Paused", "暂停")}
                      </span>
                    </div>
                    <p className={styles.schedule}>{formatSchedule(task, text)}</p>
                    <p className={styles.prompt}>{task.prompt || task.command}</p>
                    {!!task.memory_refs?.length && (
                      <span className={styles.memory}><Link2 />{task.memory_refs.length} MemoryRef</span>
                    )}
                  </div>
                  <div className={styles.actions}>
                    <button onClick={() => void toggle(task)} type="button" aria-label={task.enabled ? text("Pause", "暂停") : text("Resume", "恢复")} title={task.enabled ? text("Pause", "暂停") : text("Resume", "恢复")}>
                      {task.enabled ? <Pause /> : <Play />}
                    </button>
                    <button onClick={() => void remove(task)} type="button" aria-label={text("Delete", "删除")} title={text("Delete", "删除")}><Trash2 /></button>
                  </div>
                </article>
              ))}
            </section>
          ) : tasks.length > 0 ? (
            <div className={styles.empty}>{text("No matching tasks", "没有匹配的任务")}</div>
          ) : (
            <Suggestions onChoose={begin} text={text} />
          )}
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

function formatSchedule(task: Task, text: (en: string, zh: string) => string) {
  if (task.type === "once" && task.run_at) return new Date(task.run_at).toLocaleString();
  if (task.type === "monitor") return `${text("Monitor", "监控")} · ${task.cron}`;
  return `${text("Recurring", "周期")} · ${task.cron}`;
}

function Suggestions({ onChoose, text }: {
  onChoose: (template: Partial<typeof EMPTY_FORM>) => void;
  text: (en: string, zh: string) => string;
}) {
  const rows = [
    { icon: <Bell />, title: text("Daily brief", "每日简报"), schedule: text("Weekdays at 8:00 AM", "工作日 8:00"), form: { type: "recurring" as TaskType, title: text("Daily brief", "每日简报"), cron: "0 8 * * 1-5", prompt: text("Summarize today's priorities.", "总结今天的优先事项。") } },
    { icon: <Clock3 />, title: text("Weekly review", "每周回顾"), schedule: text("Fridays at 4:00 PM", "周五 16:00"), form: { type: "recurring" as TaskType, title: text("Weekly review", "每周回顾"), cron: "0 16 * * 5", prompt: text("Review this week's work and open decisions.", "回顾本周工作和未决事项。") } },
    { icon: <Eye />, title: text("Follow-up monitor", "跟进监控"), schedule: text("Weekdays at 9:00 AM", "工作日 9:00"), form: { type: "monitor" as TaskType, title: text("Follow-up monitor", "跟进监控"), cron: "0 9 * * 1-5", prompt: text("Check whether the selected follow-up needs attention.", "检查选定的跟进事项是否需要处理。") } },
  ];
  return <section className={styles.suggestions}>
    <h2>{text("Suggestions", "建议")}</h2>
    {rows.map((row) => <button key={row.title} className={styles.suggestion} onClick={() => onChoose(row.form)} type="button">
      <span className={styles.suggestionIcon}>{row.icon}</span><span><strong>{row.title}</strong><small>{row.schedule}</small></span>
    </button>)}
  </section>;
}
