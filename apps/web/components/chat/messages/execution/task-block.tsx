"use client";

/**
 * Task block — one named stretch of work inside a reply
 * (docs/reference/design/ui/chat/task-blocks.html). Three levels, each one
 * indent further in: the header with the state on its glyph; opened, the
 * steps the agent set, on their own rail; a step the reader opens, the work
 * done for it: the same step rows and text the reply draws outside a block.
 * Steps stay folded until clicked. Finished, the block folds in place to one
 * line with its result.
 * What belongs to the block is decided in lib/chat/transcript/task-blocks.ts.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUp, ChevronRight, CircleCheck, CircleDashed, CircleEllipsis, CircleMinus, CircleX } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { useTranslation } from "@/lib/i18n";
import { ELAPSED_CADENCE, formatElapsed } from "@/lib/format-utils/elapsed";
import { useNow } from "@/lib/clock/use-now";
import { stepOpenKey, taskOpenKey, useTaskOpen } from "@/lib/prefs/task-block-open";
import {
  autoOpen,
  currentStepId,
  progressOf,
  type DrawnState,
  type TaskPart,
  type TaskStep,
  type WorkItem,
} from "@/lib/chat/transcript/task-blocks";
import { revealTask } from "@/lib/chat/transcript/use-row-tasks";

import { CodeRainText, useCodeRainStyle } from "../../welcome/code-rain-style";
import { Collapse } from "./execution-strip";
import type { ToolGroupSummary } from "./tool-group-summary";

type Text = (en: string, zh: string) => string;

/** Fold with ArrowLeft, open with ArrowRight, as the Activity roster does. */
function foldKeys(open: boolean, toggle: () => void, activates: boolean) {
  return (event: KeyboardEvent) => {
    if (event.altKey || event.metaKey || event.ctrlKey) return;
    const arrow = (event.key === "ArrowLeft" && open) || (event.key === "ArrowRight" && !open);
    if (arrow || (activates && (event.key === "Enter" || event.key === " "))) {
      event.preventDefault();
      toggle();
    }
  };
}

function DiffBadge({ summary }: { summary: ToolGroupSummary | null }) {
  if (!summary || summary.added === null || summary.removed === null) return null;
  return (
    <span className="turn-files-diff tl-diff">
      <span className="turn-files-stat is-add"><span>+</span>{summary.added}</span>
      <span className="turn-files-stat is-del"><span>−</span>{summary.removed}</span>
    </span>
  );
}

type StepState = "done" | "run" | "todo" | "drop" | "held" | "fail";

function stepState(step: TaskStep, drawn: DrawnState, current: boolean): StepState {
  if (step.status === "completed") return "done";
  if (step.status === "dropped") return "drop";
  if (step.status === "pending") return "todo";
  if (drawn === "running") return "run";
  return drawn === "failed" && current ? "fail" : "held";
}

function StepGlyph({ state, active }: { state: StepState; active: boolean }) {
  if (state === "done") return <CircleCheck size={16} className="tb-checked" aria-hidden="true" />;
  if (state === "run" && active) return <Spinner aria-hidden="true" />;
  if (state === "todo") return <CircleDashed size={16} aria-hidden="true" />;
  if (state === "drop") return <CircleMinus size={16} aria-hidden="true" />;
  if (state === "fail") return <CircleX size={16} aria-hidden="true" />;
  return <CircleEllipsis size={16} aria-hidden="true" />;
}

function stepStateLabel(state: StepState, text: Text): string {
  switch (state) {
    case "done": return text("Done", "已完成");
    case "run": return text("In progress", "进行中");
    case "todo": return text("To do", "待办");
    case "drop": return text("Dropped", "已放弃");
    case "fail": return text("Failed", "失败");
    case "held": return text("Not finished", "未完成");
  }
}

function TaskStepRow({ rowId, taskId, step, items, drawn, active, current, renderItem, summarize, livePhrase, tail }: {
  rowId: string;
  taskId: string;
  step: TaskStep;
  items: WorkItem[];
  drawn: DrawnState;
  /** The reply is working now, not waiting on the person. */
  active: boolean;
  /** The step the block is on, or was on when it stopped. */
  current: boolean;
  renderItem: (item: WorkItem) => ReactNode;
  summarize: (items: WorkItem[]) => ToolGroupSummary;
  livePhrase: (items: WorkItem[]) => string;
  tail?: ReactNode;
}) {
  const { text } = useTranslation();
  const [chosen, setChosen] = useTaskOpen(rowId, stepOpenKey(taskId, step.id));
  const state = stepState(step, drawn, current);
  const hasWork = items.length > 0 || !!tail;
  // Folded until the reader opens it, the step being worked on included.
  const open = hasWork && chosen === true;
  const summary = items.length ? summarize(items) : null;
  const note = state === "done" ? step.result ?? ""
    : state === "run" ? (active ? livePhrase(items) : "")
    : state === "fail" ? (summary?.failed ? text(`${summary.failed} failed`, `${summary.failed} 个失败`) : "")
    : state === "held" ? (drawn === "stopped" || drawn === "interrupted"
      ? text("stopped here", "停在这里")
      : drawn === "displaced" ? text("not finished", "没做完") : "")
    : "";
  const toggle = () => setChosen(!open);
  return (
    <div className="tb-step" data-state={state} data-open={open ? "1" : "0"} data-step-id={step.id}>
      <div
        className={"tb-step-head" + (hasWork ? "" : " is-static")}
        role={hasWork ? "button" : undefined}
        tabIndex={hasWork ? 0 : undefined}
        aria-expanded={hasWork ? open : undefined}
        onClick={hasWork ? toggle : undefined}
        onKeyDown={hasWork ? foldKeys(open, toggle, true) : undefined}
      >
        <span className="tb-step-ico"><StepGlyph state={state} active={active} /></span>
        <span className="sr-only">{stepStateLabel(state, text)}: </span>
        <span className="tb-step-title">{step.subject}</span>
        <span className={"tb-step-note" + (state === "run" && note ? " tl-label-shimmer" : "")}>{note}</span>
        <DiffBadge summary={summary} />
        {hasWork ? <ChevronRight className="tb-step-chev" size={14} aria-hidden="true" /> : null}
      </div>
      {hasWork ? (
        <Collapse open={open}>
          <div className="tl-sub">{items.map(renderItem)}{tail}</div>
        </Collapse>
      ) : null}
    </div>
  );
}

/** Now in seconds, renewed when the block's "2m 14s" since `startedAt`
 *  (seconds) changes while `active`; otherwise it stays where it was. */
function useSeconds(active: boolean, startedAt: number): number {
  return useNow(active ? ELAPSED_CADENCE : null, startedAt * 1000) / 1000;
}

function stateLabel(drawn: DrawnState, text: Text): string {
  switch (drawn) {
    case "running": return text("In progress", "进行中");
    case "done": return text("Done", "已完成");
    case "stopped": return text("Stopped", "已停止");
    case "interrupted": return text("Interrupted", "已中断");
    case "failed": return text("Failed", "失败");
    case "displaced":
    case "carried": return text("Not finished", "未完成");
  }
}

export function TaskBlock({ rowId, part, drawn, active = true, endedAt, last, agentsRunning = false, earlierAnchor, renderItem, summarize, livePhrase, status, tail }: {
  /** The reply the block is drawn under: the key of the reader's choices. */
  rowId: string;
  part: TaskPart;
  drawn: DrawnState;
  /** The reply is working now. False while a running block waits (on the
   *  person, or on a retry): it stays open and shows no motion. */
  active?: boolean;
  /** When the reply ended, in seconds: where the clock of a block that was
   *  stopped, failed or cut off ends, since its segment was never closed. */
  endedAt?: number;
  /** No later task block follows in the reply. */
  last: boolean;
  /** A sub-agent started from this block is still running. */
  agentsRunning?: boolean;
  /** The loaded reply holding this task's previous segment. */
  earlierAnchor?: string;
  /** One piece of work: an existing step row, or text said during the step. */
  renderItem: (item: WorkItem) => ReactNode;
  summarize: (items: WorkItem[]) => ToolGroupSummary;
  /** The call in progress among these, in the present tense; "" for none. */
  livePhrase: (items: WorkItem[]) => string;
  /** A live state after the name, such as a retry (retry-status.tsx). */
  status?: ReactNode;
  /** A row after the block's last piece of work (the retry's own row). */
  tail?: ReactNode;
}) {
  const { text } = useTranslation();
  const codeRain = useCodeRainStyle();
  const segment = part.segment;
  const legacy = segment.legacy;
  const [chosen, setChosen] = useTaskOpen(rowId, taskOpenKey(segment.taskId));
  const open = chosen ?? autoOpen(drawn, last, agentsRunning, legacy);
  const running = drawn === "running";
  const moving = running && active;
  const now = useSeconds(running && segment.startedAt > 0, segment.startedAt);
  // A block seen running on this page keeps the last second it showed.
  const ranHere = useRef(false);
  if (running) ranHere.current = true;
  const progress = progressOf(segment);
  const currentId = currentStepId(segment, drawn);
  const currentStep = currentId ? segment.steps.find((step) => step.id === currentId) : undefined;
  const summary = part.items.length ? summarize(part.items) : null;
  const title = legacy ? text("Todos", "待办") : segment.title || text("Task", "任务");

  let out = "";
  if (legacy) out = "";
  else if (drawn === "done") out = segment.outcome || summary?.label || "";
  else if (drawn === "displaced") {
    out = progress
      ? text(`stopped at ${progress.done}/${progress.total}`, `停在 ${progress.done}/${progress.total}`)
      : text("not finished", "没做完");
  } else if (drawn === "carried" && progress && progress.done > 0) {
    const from = part.earlier + 1;
    out = part.earlier > 0 && progress.done > from
      ? text(`steps ${from}–${progress.done} this turn`, `本轮第 ${from}–${progress.done} 步`)
      : part.earlier > 0 && progress.done === from
        ? text(`step ${from} this turn`, `本轮第 ${from} 步`)
        : text(`reached step ${progress.done} this turn`, `本轮做到第 ${progress.done} 步`);
  }
  const showCurrent = drawn === "running" || drawn === "stopped" || drawn === "failed" || drawn === "interrupted";
  const meta: string[] = [];
  if (progress) {
    if (drawn === "done" && !legacy) {
      meta.push(progress.total === 1 ? text("1 step", "1 步") : text(`${progress.total} steps`, `${progress.total} 步`));
    } else if (drawn !== "displaced") {
      // Running: the step it is on. Stopped or carried: the steps it finished.
      meta.push(`${running ? progress.on : progress.done}/${progress.total}`);
    }
  }
  let timeAt = -1;
  if (!legacy && segment.startedAt > 0) {
    // The reply's own end when the row carries it; until it does, a block
    // seen running here holds the last second it showed.
    const end = running ? now : segment.endedAt ?? endedAt ?? (ranHere.current ? now : undefined);
    if (end !== undefined && end >= segment.startedAt) {
      timeAt = meta.length;
      meta.push(formatElapsed(end - segment.startedAt, text));
    }
  }
  const tag = drawn === "stopped" ? text("Stopped", "已停止")
    : drawn === "interrupted" ? text("Interrupted", "已中断")
    : drawn === "failed" ? text("Failed", "失败") : "";
  const toggle = () => setChosen(!open);
  // The retry's own row goes under the last step that has work.
  const tailStep = part.flat ? -1 : part.steps.findLastIndex((row) => row.items.length > 0);
  const jump = () => {
    if (earlierAnchor) revealTask(segment.taskId, earlierAnchor);
  };
  return (
    <section
      className="tb"
      data-state={drawn}
      data-open={open ? "1" : "0"}
      data-task-id={segment.taskId}
      data-anchor={rowId}
      data-legacy={legacy ? "" : undefined}
    >
      {/* No hover tip: the row is transcript content. */}
      <button type="button" className="tb-head" aria-expanded={open} onClick={toggle} onKeyDown={foldKeys(open, toggle, false)}>
        {moving ? <Spinner className="tb-ico" aria-hidden="true" />
          : drawn === "done" ? <CircleCheck size={16} className="tb-ico tb-checked" aria-hidden="true" />
          : drawn === "failed" ? <CircleX size={16} className="tb-ico" aria-hidden="true" />
          : <CircleEllipsis size={16} className="tb-ico" aria-hidden="true" />}
        <span className="sr-only">{stateLabel(drawn, text)}: </span>
        {moving && codeRain
          ? <CodeRainText className="tb-name" text={title} />
          : <span className={"tb-name" + (moving ? " tl-label-shimmer" : "")}>{title}</span>}
        {out ? <span className="tb-out">— {out}</span> : null}
        {showCurrent && currentStep ? <span className="tb-cur">· {currentStep.subject}</span> : null}
        {status}
        {meta.map((item, at) => <span key={item} className={"tb-meta" + (at === timeAt ? " tb-time" : "")}>{item}</span>)}
        <DiffBadge summary={summary} />
        {tag ? <span className={"tl-stopped" + (drawn === "failed" ? " is-fail" : "")}>{tag}</span> : null}
        <ChevronRight className="tl-chev" size={14} aria-hidden="true" />
      </button>
      <Collapse open={open}>
        <div className="tb-body">
          {segment.purpose ? <p className="tb-purpose">{segment.purpose}</p> : null}
          {part.earlier > 0 || part.steps.length > 0 ? (
            <div className="tb-steps">
              {part.earlier > 0 ? (
                <div className="tb-step tb-jump">
                  <div
                    className={"tb-step-head" + (earlierAnchor ? "" : " is-static")}
                    role={earlierAnchor ? "button" : undefined}
                    tabIndex={earlierAnchor ? 0 : undefined}
                    onClick={earlierAnchor ? jump : undefined}
                    onKeyDown={earlierAnchor ? (event) => {
                      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); jump(); }
                    } : undefined}
                  >
                    <span className="tb-step-ico"><ArrowUp size={14} aria-hidden="true" /></span>
                    <span className="tb-step-title">
                      {part.earlier === 1
                        ? text("1 earlier step done", "前 1 步已完成")
                        : text(`${part.earlier} earlier steps done`, `前 ${part.earlier} 步已完成`)}
                    </span>
                  </div>
                </div>
              ) : null}
              {part.steps.map((row, at) => (
                <TaskStepRow
                  key={row.step.id}
                  rowId={rowId}
                  taskId={segment.taskId}
                  step={row.step}
                  items={row.items}
                  drawn={drawn}
                  active={active}
                  current={row.step.id === currentId}
                  renderItem={renderItem}
                  summarize={summarize}
                  livePhrase={livePhrase}
                  tail={at === tailStep ? tail : undefined}
                />
              ))}
            </div>
          ) : null}
          {part.flat && (part.items.length > 0 || tail) ? (
            <div className="tl-sub tb-flat">{part.items.map(renderItem)}{tail}</div>
          ) : !part.flat && tailStep < 0 && tail ? <div className="tl-sub tb-flat">{tail}</div> : null}
        </div>
      </Collapse>
    </section>
  );
}
