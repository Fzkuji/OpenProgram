"use client";

/** A scheduled task in Agents: one row however many times it ran. Its
 *  title, its runs in short ("178 runs · 9 didn't finish · last 23:00") and
 *  a strip of its last 24 runs, one cell each, coloured by how it came out.
 *  Its runs are on its page. */
import type { Routine, TaskRunOutcome } from "@/lib/chat/conversation-work";
import { Glyph, Row, type Text } from "./running-rows";
import { taskOutcomeTone, taskSummaryLine } from "./running-format";
import styles from "./running-panel.module.css";

/** The last runs, oldest on the left, one cell each. */
export function TaskStrip({ recent }: { recent: TaskRunOutcome[] }) {
  if (!recent.length) return null;
  return <span className="mt-[5px] grid grid-cols-[repeat(24,minmax(0,1fr))] gap-[1.5px]" aria-hidden="true">
    {recent.map((outcome, index) => <i key={index} className={styles.stripCell} data-tone={taskOutcomeTone(outcome)} />)}
  </span>;
}

export function TaskRow({ task, text, onOpen }: { task: Routine; text: Text; onOpen: () => void }) {
  const summary = task.summary ?? { runs: 0, unfinished: 0, notified: 0, last_at: null, recent: [] };
  return <Row glyph={<Glyph name={task.type === "webhook" ? "link" : "calendar"} tone="plan" large />} title={task.title}
    status={[taskSummaryLine(summary, text), task.enabled === false ? text("Paused", "已暂停") : ""].filter(Boolean).join(" · ")}
    tone="muted" extra={<TaskStrip recent={summary.recent} />} onOpen={onOpen} />;
}
