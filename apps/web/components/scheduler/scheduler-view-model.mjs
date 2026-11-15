import { CronExpressionParser } from "cron-parser";

export function filterTasks(tasks, filter, search) {
  const query = search.trim().toLowerCase();
  return tasks.filter((task) => {
    if (filter !== "all" && task.type !== filter) return false;
    if (!query) return true;
    return [task.title, task.prompt, task.command, task.reminder, task.cron]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

export function taskCounts(tasks) {
  return {
    all: tasks.length,
    once: tasks.filter((task) => task.type === "once").length,
    recurring: tasks.filter((task) => task.type === "recurring").length,
    monitor: tasks.filter((task) => task.type === "monitor").length,
  };
}

export function numberedTasks(tasks) {
  return tasks.map((task, index) => ({ task, number: index + 1 }));
}

export function shouldShowSuggestions(tasks, filter, search) {
  return tasks.length === 0 && filter === "all" && !search.trim();
}

export function actionAccessibleName(action, title) {
  return `${action} ${title}`;
}

// ── Calendar view ───────────────────────────────────────────────────────────
// The calendar shows each scheduled run as an entry: a one-time task at its
// run_at, a recurring task or monitor at every cron occurrence inside the
// visible range. Webhooks run when called, so they have no place on it.

export const SCHEDULER_VIEWS = ["list", "calendar"];
export const SCHEDULER_VIEW_STORAGE_KEY = "openprogram.scheduler.view";

/** The stored view choice, or "list" for anything unknown. */
export function coerceSchedulerView(value) {
  return SCHEDULER_VIEWS.includes(value) ? value : "list";
}

// The worker (openprogram/programs/tools/jobs/cron/worker.py) expands the
// same macros before matching; @reboot fires at worker start, not on a clock.
const CRON_MACROS = {
  "@yearly": "0 0 1 1 *",
  "@annually": "0 0 1 1 *",
  "@monthly": "0 0 1 * *",
  "@weekly": "0 0 * * 0",
  "@daily": "0 0 * * *",
  "@midnight": "0 0 * * *",
  "@hourly": "0 * * * *",
};

/** Five-field form of a cron expression, or null when it has no clock time. */
export function expandCronMacro(expression) {
  const value = String(expression || "").trim();
  const lower = value.toLowerCase();
  if (!value || lower === "@reboot") return null;
  return CRON_MACROS[lower] || value;
}

/**
 * Times in [start, end) at which `expression` fires, in the machine's local
 * time zone (the worker matches local wall-clock minutes), at most `limit`.
 * An expression the parser rejects yields no times.
 */
export function cronOccurrences(expression, start, end, limit = 200) {
  const cron = expandCronMacro(expression);
  if (!cron || cron.split(/\s+/).length !== 5 || !(end > start) || limit <= 0) return [];
  let iterator;
  try {
    // currentDate is exclusive; step back a millisecond so a run exactly
    // at the start of the range is included.
    iterator = CronExpressionParser.parse(cron, {
      currentDate: new Date(start.getTime() - 1),
      endDate: end,
    });
  } catch {
    return [];
  }
  const out = [];
  while (out.length < limit && iterator.hasNext()) {
    const next = iterator.next().toDate();
    if (next >= end) break;
    out.push(next);
  }
  return out;
}

/** Times in [start, end) at which one task runs, at most `limit`. A paused
 *  task runs at none of them, so it stays off the calendar until resumed:
 *  one paused six-hourly monitor would otherwise bury every day's real runs. */
export function taskOccurrences(task, start, end, limit = 200) {
  if (!task || task.type === "webhook" || task.enabled === false || limit <= 0) return [];
  if (task.type === "once") {
    if (!task.run_at) return [];
    const at = new Date(task.run_at);
    if (Number.isNaN(at.getTime())) return [];
    return at >= start && at < end ? [at] : [];
  }
  return cronOccurrences(task.cron, start, end, limit);
}

/** Minutes an entry spans on the time grid; a run has no end of its own. */
export const CALENDAR_ENTRY_MINUTES = 30;

/**
 * Calendar entries for the tasks in [start, end), sorted by time. Each task
 * contributes at most `perTask` runs and the whole range at most `total`, so
 * an every-minute cron cannot flood a month; `truncated` says a cap was hit.
 */
export function calendarEntries(tasks, start, end, { perTask = 200, total = 2000 } = {}) {
  const entries = [];
  let truncated = false;
  for (const task of tasks) {
    const times = taskOccurrences(task, start, end, perTask + 1);
    if (times.length > perTask) {
      truncated = true;
      times.length = perTask;
    }
    for (const at of times) {
      entries.push({
        id: `${task.id}-${at.getTime()}`,
        taskId: task.id,
        title: task.title,
        type: task.type,
        enabled: task.enabled !== false,
        start: at,
        end: new Date(at.getTime() + CALENDAR_ENTRY_MINUTES * 60_000),
      });
    }
  }
  entries.sort((a, b) => a.start - b.start || String(a.title).localeCompare(String(b.title)));
  if (entries.length > total) {
    truncated = true;
    entries.length = total;
  }
  return { entries, truncated };
}

/** How many of the tasks the calendar can place (active, not webhooks). */
export function calendarTaskCount(tasks) {
  return tasks.filter((task) => task.type !== "webhook" && task.enabled !== false).length;
}

/** Why the calendar has nothing to place: "search" (nothing matches the search), "none" (no tasks at all), "type" (none of this type), or null when it has tasks. */
export function calendarEmptyReason(tasks, visible, search) {
  if (visible.length > 0) return null;
  if (search.trim()) return "search";
  return tasks.length === 0 ? "none" : "type";
}

function pad(value) {
  return String(value).padStart(2, "0");
}

/** A `datetime-local` input value (YYYY-MM-DDTHH:mm) for a wall-clock time. */
export function dateTimeLocalValue(year, month, day, hour = 9, minute = 0) {
  return `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}`;
}

/** The edit form for an existing task; a one-time run is shown in local time. */
export function formFromTask(task) {
  const run = task.run_at ? new Date(task.run_at) : null;
  const runAt = run && !Number.isNaN(run.getTime())
    ? dateTimeLocalValue(run.getFullYear(), run.getMonth() + 1, run.getDate(), run.getHours(), run.getMinutes())
    : "";
  return {
    title: task.title || "",
    type: task.type || "recurring",
    prompt: task.prompt || "",
    runAt,
    cron: task.cron || "0 9 * * *",
    memoryId: task.memory_refs?.[0]?.memory_id || "",
  };
}

/**
 * What the page sends for a form: everything for a new task; for an existing
 * one only what the server lets change (its type and the kind of its body stay).
 * @param {{title: string, type: string, prompt: string, runAt: string, cron: string}} form
 * @param {{workspace_id: string, memory_id: string} | undefined} memoryRef
 * @param {{type?: string, prompt?: string} | null} [existing]
 */
export function taskPayload(form, memoryRef, existing = null) {
  const schedule = form.type === "once"
    ? { run_at: form.runAt ? new Date(form.runAt).toISOString() : "" }
    : { cron: form.cron };
  const memory_refs = memoryRef ? [{ workspace_id: memoryRef.workspace_id, memory_id: memoryRef.memory_id }] : [];
  if (!existing) return { title: form.title, type: form.type, prompt: form.prompt, ...schedule, memory_refs };
  const payload = { title: form.title };
  if (existing.type !== "webhook") Object.assign(payload, schedule);
  if (typeof existing.prompt === "string") Object.assign(payload, { prompt: form.prompt, memory_refs });
  return payload;
}
