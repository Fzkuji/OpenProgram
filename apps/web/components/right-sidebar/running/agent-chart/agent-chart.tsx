"use client";

/** A time chart by topic: an Agent's own, or the conversation's with
 *  every agent's work in its colour. React draws the blocks; where they sit is written
 *  straight on the elements. The sidebar's width decides how many whole
 *  columns show; moving between columns slides the track by its transform
 *  alone, with the blocks already laid out for where it ends. A block with
 *  more under it leads one column right; a block that is one call or one
 *  message opens its details in place. */
import { ChevronLeft } from "lucide-react";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent, type MutableRefObject, type RefObject } from "react";
import { Button } from "@/components/ui/button";
import { availableExecutionActions, type ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import { selectViewRows, useSessionStore } from "@/lib/session-store";
import type { ChatMsg } from "@/lib/session-store/types";
import type { ActivityRun } from "@/lib/net/execution/activity-runs-client";
import { processIsActive } from "@/lib/net/execution/process-client";
import type { ActivityPanelContext, PagePull, Translate } from "../pages/activity-page-types";
import { ActivityKeyValue, ActivityPanelReact, ActivityRowStoreReact, createActivityRowStore, type ActivityRowStore } from "../pages/activity-page-row";
import { useActivityPageKeys } from "../pages/use-activity-page-keys";
import { stepTitle } from "../pages/activity-run-wording";
import { COLUMN_WIDTH, HEADER_HEIGHT, barColor, buildChart, buildWorkChart, chartWindow, closedRoom, columnsFor, focusOn, levelsOf, readableInput, forEachNode, opensInPlace, rootHeight, sizeChart, stepFocus, travelMs, type ChartAgent, type ChartNode, type ChartOptions, type ChartRoot, type LivePhase, type WorkAgent, type WorkTask } from "./agent-chart-model";
import { HoverTip } from "@/components/ui/tooltip";
import { useConversationAgentShape } from "@/components/agents/agent-avatar";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import { AgentGlyph } from "../running-rows";
import { useExecutionControls } from "../runs/execution-controls";
import { EventDetail } from "../runs/run-insides";
import { observeSwipe, rubberBand, stillMotion, swipeTurns, tweenTo } from "../sideways";
import { useActivityRuns } from "../runs/use-activity-runs";
import { useRunInspection } from "../runs/use-run-inspection";
import pageStyles from "../pages/activity-page.module.css";
import styles from "./agent-chart.module.css";

/** How far a sideways swipe goes before lifting the fingers turns the column. */
const SWIPE_TURNS = 48;
/** How long a block takes to open over its details or close over them: --motion-slow. */
const GROW_MS = 240;
const NO_ROWS_OPEN = () => false;

interface AgentChartProps {
  head: string | null;
  context: ActivityPanelContext;
  box: RefObject<HTMLDivElement | null>;
  reportName: (run: ActivityRun) => string | null;
  nextWake: { at: number; line: string; strong?: boolean } | null;
  onOpenNextWake: () => void;
  /** Set to what moves the chart one column back, for the page's Back. */
  stepBack?: MutableRefObject<(() => boolean) | null>;
  /** A swipe past the first column pulls the page itself: the way back. */
  onPull?: PagePull;
}

/** The one thing a block asks of the reader's hand: the answer it waits
 *  for, or a pause to lift. A run under way asks for nothing here: the
 *  composer stops it, and what is typed while it runs is added to it. */
function RunActions({ snapshot, context }: { snapshot: ExecutionSnapshot; context: ActivityPanelContext }) {
  const { text, connected } = context;
  const inspection = useRunInspection(snapshot, context.sessionId, context.active);
  const controls = useExecutionControls(snapshot, inspection, context);
  const decision = context.waitingFor(snapshot);
  const ask = (label: string, onClick: () => void, busy = false) => <span className={styles.asks}>
    <Button type="button" size="xs" disabled={!connected || busy}
      onClick={(event: MouseEvent) => { event.stopPropagation(); onClick(); }} onKeyDown={event => event.stopPropagation()}>{label}</Button>
  </span>;
  if (decision?.answerLabel) return ask(decision.approval ? text("Review", "授权") : text("Answer", "回答"), () => context.answerInChat(snapshot));
  if (!decision && availableExecutionActions(snapshot).includes("continue")) return ask(text("Continue", "继续"), () => controls.run("continue"), controls.pendingActions.has("execution.continue"));
  return null;
}

/** What a block with no next column shows when it is opened: the same words
 *  the run list gives the event. Its height is reported so the chart makes room. */
function BlockInsides({ node, context, onMeasure }: { node: ChartNode; context: ActivityPanelContext; onMeasure: (id: string, height: number) => void }) {
  const { text } = context;
  const element = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const box = element.current;
    if (!box) return;
    const measure = () => onMeasure(node.id, box.offsetHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [node.id, onMeasure]);
  const { event, run } = node;
  // Text here is for reading and copying, so a click in it leaves the block open.
  return <div ref={element} className={styles.insides} onClick={click => click.stopPropagation()}>
    {event?.kind === "subagent" ? <>
      {event.brief.trim() ? <ActivityKeyValue label={text("Asked", "提问")} value={event.brief} /> : null}
      {event.error?.trim() ? <ActivityKeyValue label={text("Error", "报错")} value={event.error} /> : null}
      {event.result?.trim() ? <ActivityKeyValue label={text("Reply", "回复")} value={event.result} /> : null}
    </> : event?.kind === "step" ? <>
      {readableInput(event.input) ? <ActivityKeyValue label={text("Input", "输入")} value={readableInput(event.input)} mono /> : null}
      {event.result?.trim() ? <ActivityKeyValue label={event.failed ? text("Error", "报错") : text("Result", "结果")} value={event.result.trim()} mono /> : null}
      {!readableInput(event.input) && !event.result?.trim() ? <ActivityKeyValue label="" value={text("No input and no result.", "没有输入，也没有结果。")} /> : null}
    </> : event ? <EventDetail event={event} context={context} />
      : run ? <>
        {run.asked_preview.trim() ? <ActivityKeyValue label={text("Asked", "提问")} value={run.asked_preview} /> : null}
        {run.reply.trim() ? <ActivityKeyValue label={text("Reply", "回复")} value={run.reply} /> : null}
      </> : null}
  </div>;
}

/** What the panel knows of an agent on the chart: what its glyph says when
 *  pointed at, how many things wait for the reader there, where a click on
 *  it goes, and, while sub-agents it started are going, how to stop them. */
export interface AgentMark { tip: string; waiting: number; onOpen?: () => void; stop?: { busy: boolean; run: () => void } }
export type AgentMarks = (agent: ChartAgent) => AgentMark;

/** An agent's glyph on the one block that wears it: its name when pointed
 *  at, a red count for what waits on the reader, and a click to the agent. */
function AgentMarkGlyph({ agent, shape, mark, live }: { agent: ChartAgent; shape: string; mark: AgentMark | null; live?: boolean }) {
  const glyph = <span className={live ? styles.live : undefined} data-live={live ? "1" : undefined}><AgentGlyph shape={shape} color={agent.color} small /></span>;
  const count = mark && mark.waiting > 0 ? <span className={styles.count}>{mark.waiting > 99 ? "99+" : mark.waiting}</span> : null;
  const label = mark?.tip || agent.name;
  if (!mark?.onOpen) return <HoverTip label={label}><span className={styles.mark}>{glyph}{count}</span></HoverTip>;
  const onOpen = mark.onOpen;
  return <HoverTip label={label}>
    <Button type="button" variant="ghost" className={styles.mark} aria-label={label} onClick={event => { event.stopPropagation(); onOpen(); }} onKeyDown={event => event.stopPropagation()}>{glyph}{count}</Button>
  </HoverTip>;
}

interface BlockProps {
  node: ChartNode; context: ActivityPanelContext; executionOf: (run: ActivityRun) => ExecutionSnapshot | null; open: boolean; mainShape: string;
  marks?: AgentMarks; onOpen: (id: string) => void; onBack: () => void; onMeasure: (id: string, height: number) => void;
}

/** Everything a closed block draws, as one string: a chart is built anew
 *  every few seconds while something runs, and most of its blocks say the
 *  same as before. */
const blockFace = (node: ChartNode): string => [
  node.id, node.role, node.time, node.title ?? "", node.words ?? "", node.line, node.strong ? 1 : 0, node.live ? 1 : 0, node.detail ?? "", barColor(node),
  node.kids?.length ? 1 : 0, opensInPlace(node) ? 1 : 0, node.hidden ? 1 : 0,
].join("\u0000");

/** A block is drawn again when what it shows changed. One with buttons, an
 *  agent's glyph or its details open reads the conversation as it is now, every time. */
const sameBlock = (before: BlockProps, after: BlockProps): boolean =>
  !after.node.hasActions && !before.node.hasActions && !after.node.agent && !before.node.agent && !after.open && !before.open
  && before.mainShape === after.mainShape && before.context.text === after.context.text
  && before.onOpen === after.onOpen && before.onBack === after.onBack && before.onMeasure === after.onMeasure
  && blockFace(before.node) === blockFace(after.node);

/** A title that arrives on a block already drawn fades in, and the lines
 *  under it ride down to make room, in the time the block grows. */
function useTitleArrives(title: string | undefined, label: RefObject<HTMLSpanElement | null>) {
  const had = useRef(Boolean(title));
  useLayoutEffect(() => {
    const before = had.current;
    had.current = Boolean(title);
    const box = label.current;
    if (before || !title || !box || stillMotion() || typeof box.animate !== "function") return;
    const [first, ...rest] = [...box.children] as HTMLElement[];
    if (!first) return;
    const timing: KeyframeAnimationOptions = { duration: GROW_MS, easing: EASE_STANDARD };
    const room = first.getBoundingClientRect().height + 1;
    first.animate([{ opacity: 0 }, { opacity: 1 }], timing);
    rest.forEach(line => line.animate([{ transform: `translateY(${-room}px)` }, { transform: "none" }], timing));
  }, [title, label]);
}

const Block = memo(function Block({ node, context, executionOf, open, mainShape, marks, onOpen, onBack, onMeasure }: BlockProps) {
  const label = useRef<HTMLSpanElement>(null);
  useTitleArrives(node.title, label);
  // A topic not named yet is room for its messages, and nothing to read or press.
  if (node.hidden) return <div className={styles.block} data-chart-id={node.id} data-hidden="1" aria-hidden="true" />;
  const travels = Boolean(node.kids?.length);
  const acts = travels || opensInPlace(node);
  const snapshot = node.hasActions && node.run ? executionOf(node.run) : null;
  const { agent } = node;
  const mark = agent && marks ? marks(agent) : null;
  const stop = node.stops ? mark?.stop : undefined;
  // A step that started a sub-agent is one line: its glyph, its name and how it went.
  const oneLine = Boolean(agent) && node.role >= 2;
  const lead = (who: ChartAgent) => <span className={styles.lead}><AgentMarkGlyph agent={who} shape={who.sub ? subAgentShape(who.name) : mainShape} mark={mark} live={node.live} /></span>;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); onBack(); return; }
    if (event.key === "Enter" || event.key === " " || (travels && event.key === "ArrowRight")) { event.preventDefault(); onOpen(node.id); }
  };
  return <div className={styles.block} data-chart-id={node.id} data-small={node.role >= 2 ? "1" : undefined} data-open={open ? "1" : undefined}
    {...acts ? { role: "button", tabIndex: -1, "data-row-id": node.id, onClick: () => onOpen(node.id), onKeyDown } : {}}
    {...acts && !travels ? { "aria-expanded": open } : {}}>
    <span className={styles.time}>{node.time}</span>
    <span className="flex flex-col items-center"><i className={styles.bar} data-bar="1" style={{ background: barColor(node) }} /></span>
    <span className={styles.card}><span className={styles.label} ref={label}>
      {agent && (node.title || oneLine) ? <span className="flex min-w-0 items-center gap-1.5">
        <AgentMarkGlyph agent={agent} shape={agent.sub ? subAgentShape(agent.name) : mainShape} mark={mark} live={node.live} />
        <span className={`${styles.title} min-w-0`}>{node.title}</span>
        {oneLine && node.line ? <span className={styles.after} data-strong={node.strong ? "1" : undefined}>{`· ${node.line}`}</span> : null}
      </span> : node.title ? <span className={styles.title}>{node.title}</span> : null}
      {/* With no title yet, the agent's glyph leads the first line there is. */}
      {node.words ? <span className={styles.line}>{agent && !node.title && !oneLine ? lead(agent) : null}{node.words}</span> : null}
      {oneLine ? null : <span className={styles.line} data-strong={node.strong ? "1" : undefined}>{agent && !node.title && !node.words ? lead(agent) : null}{node.line}</span>}
      {node.detail && !open ? <span className={styles.detail}>{node.detail}</span> : null}
      {snapshot ? <RunActions snapshot={snapshot} context={context} /> : null}
      {stop ? <span className="flex pt-1">
        <Button type="button" variant="ghost" className={styles.action} disabled={stop.busy || !context.connected} onClick={event => { event.stopPropagation(); stop.run(); }}>{context.text("Stop sub-agents", "停止子代理")}</Button>
      </span> : null}
    </span>{open ? <BlockInsides node={node} context={context} onMeasure={onMeasure} /> : null}</span>
  </div>;
}, sameBlock);

/** The chart's elements, found once per chart: a layout writes to all of
 *  them, and looking them up again cost more than the writes. */
interface ChartParts {
  blocks: Map<string, HTMLElement>;
  bars: Map<string, HTMLElement>;
  cells: Map<string, HTMLElement>;
  others: Map<string, HTMLElement[]>;
  columnElements: HTMLElement[];
  crumbs: HTMLElement[];
}

function findParts(content: HTMLElement): ChartParts {
  const all = (selector: string) => [...content.querySelectorAll<HTMLElement>(selector)];
  const blocks = new Map(all("[data-chart-id]").map(element => [element.dataset.chartId!, element]));
  const bars = new Map<string, HTMLElement>();
  blocks.forEach((element, id) => { const bar = element.querySelector<HTMLElement>("[data-bar]"); if (bar) bars.set(id, bar); });
  const others = new Map<string, HTMLElement[]>();
  all("[data-chart-root]").forEach(element => others.set(element.dataset.chartRoot!, [...others.get(element.dataset.chartRoot!) ?? [], element]));
  return { blocks, bars, others, cells: new Map(all("[data-chart-cell]").map(element => [element.dataset.chartCell!, element])), columnElements: all("[data-chart-column]"), crumbs: all("[data-chart-crumb]") };
}

/** The columns whose blocks are kept in the layout: the ones the track
 *  shows, one more on each side for a move under way, and none deeper than
 *  the level the chart is laid out at (those are never seen). */
function columnsInReach(shift: number, width: number, count: number, level: number, levels: number): { lo: number; hi: number } {
  const first = -shift / (width || COLUMN_WIDTH);
  return { lo: Math.max(0, Math.floor(first + 0.001) - 1), hi: Math.min(level < 0 ? levels - 1 : level, Math.ceil(first + count - 0.001)) };
}

/** The chart's nodes by column, as many columns as these roots have: a
 *  caller made for an earlier chart still lays this one out whole. */
function nodesByDepth(roots: ChartRoot[]): ChartNode[][] {
  const columns: ChartNode[][] = Array.from({ length: levelsOf(roots) }, () => []);
  for (const root of roots) if (root.kind === "topic") forEachNode(root.node, node => columns[node.depth].push(node));
  return columns;
}

/** The standard curve (--ease-standard), the one the track slides on. */
const EASE_STANDARD = "cubic-bezier(0.25, 0.1, 0.25, 1)";

/** Where an element shows now, its bar's height with it, by what the page
 *  measures: a move still under way is caught where it is. */
interface Seen {
  element: HTMLElement; bar: HTMLElement | null; top: number; barHeight: number;
  /** The id of the block it draws; none for a cell or a block outside the topics. */
  id: string | null;
  /** Its time and words, which stick to the top of the view: where each showed. */
  pinned: { element: HTMLElement; top: number }[];
}

/** A block's time and words: they stick at the top of the view while the block runs on under it. */
const pinnedOf = (block: HTMLElement): { element: HTMLElement; top: number }[] =>
  [...block.querySelectorAll<HTMLElement>(`:scope > .${CSS.escape(styles.time)}, :scope > .${CSS.escape(styles.card)} > .${CSS.escape(styles.label)}`)].map(element => ({ element, top: element.getBoundingClientRect().top }));

/** Glides what a new layout moved from where it was seen to where it is
 *  now, in `ms` on the track's curve: a block by its transform, its bar by
 *  scaling from its top. Both run off the main thread, so nothing is laid
 *  out again while they play. Only what is on screen before or after moves.
 *  `more` are the moves already worked out, of blocks nobody saw before. */
function glide(seen: Seen[], view: DOMRect, ms: number, more: [HTMLElement, string][]): Animation[] {
  const timing: KeyframeAnimationOptions = { duration: ms, easing: EASE_STANDARD };
  const shown = (y: number, height: number) => y < view.bottom && y + height > view.top;
  // Every place is read before any move starts: a move changes what the next read sees, and makes it cost a layout.
  const moves: [HTMLElement, string][] = [...more];
  for (const { element, bar, top, barHeight, pinned } of seen) {
    if (element.style.display === "none") continue;
    const now = element.getBoundingClientRect();
    if (!shown(top, now.height) && !shown(now.top, now.height)) continue;
    const by = top - now.top;
    if (Math.abs(by) >= 0.5) moves.push([element, `translateY(${by}px)`]);
    const height = bar?.getBoundingClientRect().height ?? 0;
    if (bar && height > 0 && Math.abs(barHeight - height) >= 0.5) moves.push([bar, `scaleY(${barHeight / height})`]);
    // What sticks moves by its own distance: the block's move, taken back, plus where it stuck before and now.
    for (const stuck of pinned) {
      const own = stuck.top - stuck.element.getBoundingClientRect().top - by;
      if (Math.abs(own) >= 0.5) moves.push([stuck.element, `translateY(${own}px)`]);
    }
  }
  return moves.map(([element, from]) => element.animate([{ transform: from }, { transform: "none" }], timing));
}

/** What a chart reads of the conversation to word its blocks. */
/** What a reply under way is doing, from the conversation's own copy of
 *  it: retrying, writing its text, or thinking before its next step. */
function phaseOf(message: ChatMsg, text: Translate): string {
  const retry = message.retryStatus;
  if (retry) return `retrying:${retry.attempt}:${retry.maxAttempts ?? ""}`;
  const tools = message.tools ?? [];
  for (let at = tools.length - 1; at >= 0; at -= 1) {
    const call = tools[at];
    if (call.status !== "running" || call.tool === "agent") continue;
    let input: unknown = call.input;
    try { input = JSON.parse(call.input); } catch { /* the raw text */ }
    const command = input && typeof input === "object" ? (input as { command?: unknown; cmd?: unknown }).command ?? (input as { cmd?: unknown }).cmd : undefined;
    const flat = (words: string) => words.replace(/\s+/g, " ").trim().slice(0, 200);
    return typeof command === "string" && command.trim() ? `command:${flat(command)}` : `step:${flat(stepTitle(call.tool, call.input, text))}`;
  }
  const last = message.blocks?.at(-1);
  return last?.type === "text" && last.text?.trim() ? "writing" : "thinking";
}

function readPhase(word: string | undefined): LivePhase | null {
  if (!word) return null;
  if (word === "writing" || word === "thinking") return { kind: word };
  if (word.startsWith("command:")) return { kind: "tool", command: word.slice(8) };
  if (word.startsWith("step:")) return { kind: "tool", step: word.slice(5) };
  const [, attempt, max] = word.split(":");
  return { kind: "retrying", attempt: Number(attempt) || 1, max: Number(max) || undefined };
}

function useChartOptions(context: ActivityPanelContext, runs: ActivityRun[]) {
  const { text, now } = context;
  const executionOf = useCallback((run: ActivityRun) => run.execution_id ? context.controller.executions.find(item => item.execution_id === run.execution_id) ?? null : null, [context.controller.executions]);
  // A run just started has no reply saved yet: its execution names the reply it is writing.
  const replyOf = useCallback((run: ActivityRun) => run.message_id ?? executionOf(run)?.display?.assistant_message_id ?? null, [executionOf]);
  // One line per reply still going, so the chart is built again when what one does changes, not on every streamed word.
  const phases = useSessionStore(useCallback(state => {
    const rows = selectViewRows(context.sessionId)(state);
    let words = "";
    for (const run of runs) {
      const id = run.ended_at === null ? replyOf(run) : null;
      const message = id ? rows[id] : undefined;
      if (message) words += `${id}\t${phaseOf(message, text)}\n`;
    }
    return words;
  }, [runs, context.sessionId, replyOf, text]));
  const phaseByMessage = useMemo(() => new Map(phases.split("\n").filter(Boolean).map(line => line.split("\t") as [string, string])), [phases]);
  const waiting = runs.some(run => { const snapshot = executionOf(run); return snapshot ? context.waitingFor(snapshot) !== null : false; });
  const anyLive = waiting || runs.some(run => run.ended_at === null);
  // While something runs its time counts up by the second, on its own clock; idle, nothing moves.
  const [clock, setClock] = useState(0);
  useEffect(() => {
    if (!anyLive) return;
    const timer = window.setInterval(() => setClock(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [anyLive]);
  const beat = anyLive ? Math.floor(Math.max(now, clock)) : 0;
  const options: Omit<ChartOptions, "reportName" | "nextWake" | "earlier"> = {
    text, now: anyLive ? beat : now,
    waitOf: run => { const snapshot = executionOf(run); return snapshot ? context.waitingFor(snapshot) : null; },
    hasActions: run => executionOf(run) !== null,
    livePhase: run => { const id = replyOf(run); return readPhase(id ? phaseByMessage.get(id) : undefined); },
    liveCalls: run => {
      if (!run.execution_id || run.ended_at !== null) return [];
      const saved = new Set((run.steps ?? []).flatMap(event => event.kind === "step" && event.tool_call_id ? [event.tool_call_id] : []));
      const calls = (context.processes.calls ?? []).filter(call => call.execution_id === run.execution_id && call.status === "running" && call.name !== "agent" && !(call.tool_call_id && saved.has(call.tool_call_id)))
        .map(call => ({ id: call.id, at: call.started_at, label: call.description || stepTitle(call.name, undefined, text) }));
      const programs = context.processes.items.filter(item => item.execution_id === run.execution_id && processIsActive(item) && item.status !== "unknown")
        .map(item => ({ id: item.id, at: item.started_at, label: item.title?.trim() || item.command.trim() }));
      return [...calls, ...programs];
    },
  };
  return { options, executionOf, beat, waiting, phases };
}

export function AgentChart({ head, context, box, reportName, nextWake, onOpenNextWake, stepBack, onPull }: AgentChartProps) {
  const { text } = context;
  const source = useMemo(() => ({ kind: "branch" as const, head }), [head]);
  const { runs, total, loaded, showMore } = useActivityRuns({ sessionId: context.sessionId, source, active: context.active, isOpen: NO_ROWS_OPEN, openVersion: 0, chart: true });
  const { options, executionOf, beat, waiting, phases } = useChartOptions(context, runs);
  const roots = useMemo<ChartRoot[]>(() => buildChart(runs, { ...options, reportName, nextWake, earlier: Math.min(50, total - runs.length) }),
    // `now` is `beat` while anything runs; while idle a new render needs no new chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [runs, total, beat, waiting, phases, text, executionOf, nextWake?.at, nextWake?.line, nextWake?.strong, context.processes.calls, context.processes.items]);
  const names = [text("Topics", "话题"), text("Messages", "消息"), text("Steps", "步骤"), text("Inside a step", "步骤里面")];
  return <ChartView roots={roots} names={names} context={context} box={box} executionOf={executionOf} showMore={showMore}
    empty={loaded && runs.length === 0 ? text("No runs yet.", "还没有运行记录。") : null}
    onOpenNextWake={onOpenNextWake} stepBack={stepBack} onPull={onPull} />;
}

export interface WorkChartScope { key: string; head: string | null; name: string; color: string | null; current: boolean }

/** What the conversation's chart keeps while a page rests over it: the
 *  column the reader went to, how far down, and the blocks left open. */
export interface WorkChartMemory { focus: number; top: number; store: ActivityRowStore }

/** The conversation's chart on Activity's first page: a row for each topic,
 *  then the messages of every agent in it, each titled by its task, and the steps of each,
 *  a column deeper for what a sub-agent did. Each agent's glyph is on one
 *  block, and `marks` says what the panel knows of it.
 *  The panel reads the runs and holds them, so the chart comes back as it was. */
export function WorkChart({ scopes: given, runsByHead, tasks, context, box, reportName, memory, marks, stoppable, nextWake, onOpenNextWake }: {
  scopes: WorkChartScope[]; runsByHead: ActivityRun[][]; tasks: ReadonlyMap<string, WorkTask>; context: ActivityPanelContext;
  box: RefObject<HTMLDivElement | null>; reportName: (run: ActivityRun) => string | null;
  memory: MutableRefObject<WorkChartMemory | null>;
  marks: AgentMarks;
  /** The main agents whose sub-agents are going and can be stopped, by branch. */
  stoppable: readonly string[];
  nextWake: { at: number; line: string; strong?: boolean } | null;
  onOpenNextWake: () => void;
}) {
  const { text } = context;
  // The panel names its branches anew on every render; the chart is built again only when one changed.
  const scopeKey = given.map(scope => `${scope.key}\u0000${scope.head ?? ""}\u0000${scope.name}\u0000${scope.color ?? ""}\u0000${scope.current ? 1 : 0}`).join("\n");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const scopes = useMemo(() => given, [scopeKey]);
  const all = useMemo(() => runsByHead.flat(), [runsByHead]);
  const { options, executionOf, beat, waiting, phases } = useChartOptions(context, all);
  const stopKey = stoppable.join("\n");
  const roots = useMemo<ChartRoot[]>(() => {
    const agents: WorkAgent[] = scopes.map((scope, index) => ({ key: scope.key, name: scope.name, color: scope.color, current: scope.current, runs: runsByHead[index] ?? [] }));
    const stops = new Set(stopKey ? stopKey.split("\n") : []);
    return buildWorkChart(agents, { ...options, tasks, reportName, nextWake, earlier: 0, stoppable: key => stops.has(key) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopes, runsByHead, tasks, beat, waiting, phases, text, executionOf, stopKey, nextWake?.at, nextWake?.line, nextWake?.strong, context.processes.calls, context.processes.items]);
  // A column past the fourth is what a sub-agent's own sub-agent did: inside a step still.
  const names = [text("Topics", "话题"), text("Messages", "消息"), text("Steps", "步骤"), text("Inside a step", "步骤里面")];
  // One Tab stop among the blocks, arrows between them, and Esc one column
  // back; on the first column Esc is left to whatever is around the panel.
  const stepBack = useRef<(() => boolean) | null>(null);
  useActivityPageKeys(box, box, () => stepBack.current?.() ?? false);
  return <ChartView roots={roots} names={names} context={context} box={box} executionOf={executionOf} empty={null} stepBack={stepBack} backInHeader memory={memory} marks={marks} onOpenNextWake={onOpenNextWake} />;
}

function ChartView({ roots, names, context, box, executionOf, showMore, empty, onOpenNextWake, stepBack, onPull, backInHeader, memory, marks }: {
  roots: ChartRoot[];
  /** The columns' names; a column past the last one named is called the same as it. */
  names: string[]; context: ActivityPanelContext; box: RefObject<HTMLDivElement | null>;
  executionOf: (run: ActivityRun) => ExecutionSnapshot | null; showMore?: () => Promise<void>; empty: string | null;
  onOpenNextWake?: () => void; stepBack?: MutableRefObject<(() => boolean) | null>; onPull?: PagePull;
  /** A chart with no page around it steps back from its own column header. */
  backInHeader?: boolean;
  /** Where the chart was when it last left the document, and where it says so when it leaves again. */
  memory?: MutableRefObject<WorkChartMemory | null>;
  marks?: AgentMarks;
}) {
  const levels = useMemo(() => levelsOf(roots), [roots]);
  // While no topic has a name yet the first column has nothing to show: the chart starts one column in.
  const floor = useMemo(() => {
    const topics = roots.filter(root => root.kind === "topic");
    return levels > 1 && topics.length > 0 && topics.every(root => root.node.hidden) ? 1 : 0;
  }, [roots, levels]);
  const floorNow = useRef(floor);
  floorNow.current = floor;
  const mainShape = useConversationAgentShape(context.sessionId);
  const inner = useRef<HTMLDivElement>(null);
  const parts = useRef<ChartParts | null>(null);
  const rootsNow = useRef(roots);
  rootsNow.current = roots;
  const columnsNow = useRef<ChartNode[][]>([]);
  // The steps whose rows scroll in a cell of their own, one column on.
  const foldsNow = useRef<ChartNode[]>([]);
  // The columns: how many show, how wide each is, the deepest one the reader
  // went to, the level the blocks are laid out at, and where the track sits.
  const count = useRef(1);
  const columnWidth = useRef(COLUMN_WIDTH);
  const focus = useRef(memory?.current?.focus ?? 0);
  const drawn = useRef(-1);
  const shift = useRef(0);
  // A move between columns, or fingers on the trackpad, owns the track.
  const moving = useRef(false);
  const stopTravel = useRef<() => void>(() => {});
  const frame = useRef(0);
  // Where each element was last put, and which columns were last in reach:
  // a block is written when it comes into reach, not when its place changes.
  const written = useRef(new WeakMap<HTMLElement, string>());
  const reach = useRef("");
  const revealNow = useRef<() => void>(() => {});
  // The glides of the last layout that moved blocks; a new one starts from where they are.
  const gliding = useRef<Animation[]>([]);
  // Blocks out of reach in the new layout that showed in the old one: they stay until their glide has carried them away.
  const lingering = useRef(new Set<HTMLElement>());
  // Which blocks are open, and how tall each one's details measured.
  const [store] = useState(() => memory?.current?.store ?? createActivityRowStore());
  useSyncExternalStore(store.subscribe, store.version, store.version);
  const extras = useRef(new Map<string, number>());
  const applyExtras = useCallback(() => {
    for (const root of rootsNow.current) if (root.kind === "topic") forEachNode(root.node, node => { node.extra = extras.current.get(node.id) ?? 0; });
    sizeChart(rootsNow.current);
  }, []);

  const setShift = useCallback((x: number) => {
    shift.current = x;
    const content = inner.current;
    if (!content) return;
    content.style.transform = x ? `translate3d(${x}px,0,0)` : "";
    // The column the view starts on carries the way back, when it is not the first.
    const lead = Math.max(0, Math.round(-x / columnWidth.current));
    if (content.dataset.chartLead !== String(lead)) {
      content.dataset.chartLead = String(lead);
      content.querySelector(":scope > [data-lead]")?.removeAttribute("data-lead");
      content.children[lead]?.setAttribute("data-lead", "1");
    }
    // A column the track brings into reach gets its blocks before it shows.
    const { lo, hi } = columnsInReach(x, columnWidth.current, count.current, drawn.current, levels);
    if (reach.current !== `${lo}:${hi}`) revealNow.current();
  }, [levels]);

  /** Puts the blocks the reader can reach where the layout has them, takes
   *  the rest out of the document's layout, and names each column's parent.
   *  In reach: near the view, in a column the track shows or is about to.
   *  It moves nothing on screen, so it runs on every frame of a scroll. */
  const reveal = useCallback(() => {
    const scroller = box.current;
    const found = parts.current;
    if (!scroller || !found) return;
    const columns = columnsNow.current;
    const at = scroller.scrollTop;
    const view = scroller.clientHeight || 0;
    let { lo, hi } = columnsInReach(shift.current, columnWidth.current, count.current, drawn.current, columns.length);
    reach.current = `${lo}:${hi}`;
    // The column of the row that has the focus stays in the layout: a row
    // taken out of it would drop the focus, and the keys with it.
    const held = document.activeElement instanceof HTMLElement && scroller.contains(document.activeElement)
      ? document.activeElement.closest<HTMLElement>("[data-chart-column]") : null;
    if (held) { const depth = Number(held.dataset.chartColumn); lo = Math.min(lo, depth); hi = Math.max(hi, depth); }
    // A box with no size yet cannot say what is near: everything is written.
    const blind = view <= 0;
    const near = (node: ChartNode) => blind || (node.top < at + 2 * view && node.top + node.height > at - view);
    const write = (element: HTMLElement | undefined, node: ChartNode, bar?: HTMLElement) => {
      if (!element) return;
      const key = `${node.top}:${node.height}`;
      if (written.current.get(element) === key) return;
      written.current.set(element, key);
      element.style.top = `${node.top}px`;
      element.style.height = `${node.height}px`;
      if (bar) bar.style.height = `${Math.max(2, node.height - 6)}px`;
    };
    const show = (element: HTMLElement | undefined, on: boolean) => {
      if (!element) return;
      const display = on || lingering.current.has(element) ? "" : "none";
      if (element.style.display !== display) element.style.display = display;
    };
    for (let depth = 0; depth < columns.length; depth += 1) {
      const live = blind || (depth >= lo && depth <= hi);
      for (const node of columns[depth] ?? []) {
        // A row inside a cell goes where its cell does.
        if (node.parent?.folds) continue;
        const element = found.blocks.get(node.id);
        const on = live && near(node);
        if (on) write(element, node, found.bars.get(node.id));
        show(element, on);
      }
    }
    for (const node of foldsNow.current) {
      const cell = found.cells.get(node.id);
      const on = (blind || (node.depth + 1 >= lo && node.depth + 1 <= hi)) && near(node);
      show(cell, on);
      if (!cell || !on) continue;
      write(cell, node);
      let below = 0;
      for (const kid of node.kids!) { write(found.blocks.get(kid.id), kid, found.bars.get(kid.id)); below += kid.height; }
      const scrolls = drawn.current > node.depth && below > node.height + 1;
      // Setting scrollTop lays the page out, so only a cell that did scroll is reset.
      if (scrolls) { if (!cell.dataset.scrolls) cell.dataset.scrolls = "1"; }
      else if (cell.dataset.scrolls) { delete cell.dataset.scrolls; cell.scrollTop = 0; }
    }
    found.crumbs.forEach(crumb => {
      const list = columns[Number(crumb.dataset.chartCrumb)] ?? [];
      // A row inside a cell is where its cell is.
      const top = list.find(node => { const placed = node.parent?.folds ? node.parent : node; return placed.top + placed.height > at + HEADER_HEIGHT; });
      const name = top?.parent && !top.parent.hidden ? top.parent.title ?? top.parent.line : "";
      if (crumb.textContent !== name) crumb.textContent = name;
    });
  }, [box]);
  revealNow.current = reveal;

  /** Lays every block out at one level, at once: where each goes is worked
   *  out for all of them, and `reveal` writes the ones in reach. The block
   *  given as `held`, or else the one at the top of the view, keeps its place on screen.
   *  With `ms`, what moved glides there from where it was, in that time. */
  const place = useCallback((level: number, held: ChartNode | null = null, ms = 0, fresh = false) => {
    const scroller = box.current;
    const content = inner.current;
    if (!scroller || !content) return;
    // What shows now, in the columns that show after too, read before anything is written.
    let seen: Seen[] = [];
    // Where the old layout had every block that glides, shown or not, by id:
    // one far off comes in from there, and one it did not have fades in.
    const before = new Map<string, [number, number]>();
    // A page that cannot animate (a test's) lays out at once.
    const glides = ms > 0 && parts.current !== null && drawn.current >= 0 && !stillMotion() && typeof content.animate === "function";
    const keep = Math.min(drawn.current, level);
    if (glides) {
      const found = parts.current!;
      const look = (element: HTMLElement | undefined, node: ChartNode | null, bar: HTMLElement | null = null, sticks = false) => {
        if (node) before.set(node.id, [node.top, node.height]);
        // A block the new chart dropped is already out of the page.
        if (!element || !element.isConnected || element.style.display === "none") return;
        seen.push({ element, bar, id: node?.id ?? null, top: element.getBoundingClientRect().top, barHeight: bar?.getBoundingClientRect().height ?? 0, pinned: sticks ? pinnedOf(element) : [] });
      };
      columnsNow.current.slice(0, keep + 1).forEach(list => list.forEach(node => {
        if (node.parent?.folds) return;
        look(found.blocks.get(node.id), node, found.bars.get(node.id) ?? null, true);
      }));
      // A cell of rows moves whole; the rows in it keep their places in it.
      foldsNow.current.forEach(node => { if (node.depth + 1 <= keep) look(found.cells.get(node.id), null); });
      found.others.forEach(list => list.forEach(element => look(element, null, element.querySelector<HTMLElement>("[data-bar]"), true)));
    }
    lingering.current.clear();
    gliding.current.forEach(animation => animation.cancel());
    gliding.current = [];
    // A new chart drew new blocks: they are found again, after the old ones were read.
    if (fresh) parts.current = null;
    const put = (element: HTMLElement | undefined, top: number, height: number) => { if (element) { element.style.top = `${top}px`; element.style.height = `${height}px`; } };
    const { others, columnElements } = parts.current ??= findParts(content);
    const columns = columnsNow.current = nodesByDepth(rootsNow.current);
    foldsNow.current = columns.flat().filter(node => node.folds);
    // Read the box before any write below, or each read costs a layout.
    const at = scroller.scrollTop;
    let anchor: ChartNode | undefined = held ?? undefined;
    const offset = held ? held.top - at : 0;
    let fraction = 0;
    if (!anchor && at >= 1) {
      // The first three columns hold no cell: their blocks are placed on the chart itself.
      const depth = Math.min(2, drawn.current < 0 ? level : Math.min(drawn.current, level));
      anchor = columns[depth].find(node => node.height > 0 && node.top + node.height > at);
      if (anchor) fraction = (at - anchor.top) / anchor.height;
    }

    const placeNode = (node: ChartNode, top: number): number => {
      const height = node.heights[level];
      node.top = top; node.height = height;
      if (!node.kids) return height;
      // Rows in a cell are counted from its top.
      let below = node.folds ? 0 : top;
      for (const kid of node.kids) below += placeNode(kid, below);
      return height;
    };

    let y = HEADER_HEIGHT;
    for (const root of rootsNow.current) {
      if (root.kind === "topic") { y += placeNode(root.node, y); continue; }
      const height = rootHeight(root);
      others.get(root.id)?.forEach(element => put(element, y, height));
      if (root.kind === "next") { const bar = others.get(root.id)?.[0]?.querySelector<HTMLElement>("[data-bar]"); if (bar) bar.style.height = `${height - 6}px`; }
      y += height;
    }
    content.style.height = `${y + 12}px`;
    columnElements.forEach((column, depth) => {
      column.style.height = `${y}px`;
      // A column deeper than the level is not laid out for reading: it is
      // never seen, even while it slides out.
      const opacity = depth > level ? "0" : "";
      if (column.style.opacity !== opacity) column.style.opacity = opacity;
    });
    drawn.current = level;
    reach.current = "";
    if (anchor && anchor.height > 0) {
      const top = held ? anchor.top - offset : anchor.top + fraction * anchor.height;
      if (Math.abs(scroller.scrollTop - top) >= 1) scroller.scrollTop = top;
    }
    reveal();
    if (!glides) return;
    const found = parts.current;
    const byId = new Map<string, ChartNode>();
    columns.forEach(list => list.forEach(node => byId.set(node.id, node)));
    const shown = new Set<HTMLElement>();
    // A block that showed and is now out of reach is put where it goes and stays until it gets there.
    for (const { element, id } of seen) {
      shown.add(element);
      const node = id ? byId.get(id) : undefined;
      if (!node || element.style.display !== "none") continue;
      const writeAt = (target: HTMLElement | undefined, where: ChartNode) => {
        if (!target) return;
        target.style.top = `${where.top}px`;
        target.style.height = `${where.height}px`;
        written.current.set(target, `${where.top}:${where.height}`);
        const bar = found.bars.get(where.id);
        if (bar) bar.style.height = `${Math.max(2, where.height - 6)}px`;
      };
      writeAt(element, node);
      element.style.display = "";
      lingering.current.add(element);
    }
    // A block that was too far off to be shown comes in from where the old
    // layout had it, worked out, not read; one the old chart did not have fades in.
    const now = scroller.scrollTop;
    const room = scroller.clientHeight;
    const more: [HTMLElement, string][] = [];
    const fades: HTMLElement[] = [];
    const arrive = (element: HTMLElement | undefined, node: ChartNode, bar: HTMLElement | undefined) => {
      if (!element || shown.has(element) || element.style.display === "none") return;
      const to = node.top - now;
      const old = before.get(node.id);
      if (!old) { if (to < room && to + node.height > 0) fades.push(element); return; }
      const [top, height] = old;
      const from = top - at;
      if ((from >= room || from + height <= 0) && (to >= room || to + node.height <= 0)) return;
      if (Math.abs(from - to) >= 0.5) more.push([element, `translateY(${from - to}px)`]);
      const was = Math.max(2, height - 6);
      const is = Math.max(2, node.height - 6);
      if (bar && Math.abs(was - is) >= 0.5) more.push([bar, `scaleY(${was / is})`]);
    };
    columns.slice(0, keep + 1).forEach(list => list.forEach(node => { if (!node.parent?.folds) arrive(found.blocks.get(node.id), node, found.bars.get(node.id)); }));
    foldsNow.current.forEach(node => { if (node.depth + 1 <= keep) arrive(found.cells.get(node.id), node, undefined); });
    const batch = gliding.current = [
      ...glide(seen, scroller.getBoundingClientRect(), ms, more),
      ...fades.map(element => element.animate([{ opacity: 0 }, { opacity: 1 }], { duration: ms, easing: EASE_STANDARD })),
    ];
    seen = [];
    if (lingering.current.size) {
      const away = () => { if (gliding.current === batch) { lingering.current.clear(); reveal(); } };
      if (batch.length) Promise.all(batch.map(animation => animation.finished)).then(away, () => {});
      else away();
    }
  }, [box, reveal]);

  /** How many columns the width holds and how wide each is. True when that changed. */
  const fit = useCallback((): boolean => {
    const scroller = box.current;
    const content = inner.current;
    const width = scroller?.clientWidth ?? 0;
    if (!scroller || !content || width <= 0) return false;
    const columns = columnsFor(width, levels);
    const each = width / columns;
    if (columns === count.current && each === columnWidth.current && content.style.getPropertyValue("--chart-col")) return false;
    count.current = columns;
    columnWidth.current = each;
    content.style.setProperty("--chart-col", `${each}px`);
    return true;
  }, [box, levels]);

  /** The view the focus asks for, at once: a width change lands in its own frame. */
  const rest = useCallback(() => {
    stopTravel.current();
    moving.current = false;
    if (inner.current) inner.current.style.willChange = "";
    const { first, last } = chartWindow(focus.current, count.current, levels);
    setShift(-first * columnWidth.current);
    if (last !== drawn.current) place(last);
  }, [place, setShift, levels]);

  // A new chart glides what moved from where it was, and fades in what is new:
  // a title or a topic that arrives, a message that starts.
  useLayoutEffect(() => {
    if (extras.current.size) applyExtras();
    fit();
    // A chart that lost its deepest column shows the deepest one left.
    focus.current = Math.max(floor, Math.min(focus.current, levels - 1));
    const { first, last } = chartWindow(focus.current, count.current, levels);
    if (!moving.current) setShift(-first * columnWidth.current);
    if (drawn.current < 0) { parts.current = null; place(last); return; }
    place(Math.min(drawn.current, levels - 1), null, GROW_MS, true);
  }, [roots, place, fit, setShift, applyExtras, levels, floor]);

  // A chart that left the document comes back where the reader was, and
  // says where it is when it leaves.
  useLayoutEffect(() => {
    const scroller = box.current;
    if (!memory || !scroller) return;
    const top = memory.current?.top ?? 0;
    if (top) { scroller.scrollTop = top; reveal(); }
    return () => { memory.current = { focus: focus.current, top: scroller.scrollTop, store }; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once in, once out
  }, []);

  // An opened block grows to its details and shrinks away from them in one
  // move of the slow tier, the same as a column's; the blocks below ride along.
  const growing = useRef(new Map<string, () => void>());
  const wanted = useRef(new Map<string, number>());
  const closing = useRef(new Set<string>());
  const growTo = useCallback((id: string, to: number, onDone?: () => void) => {
    let from = extras.current.get(id);
    if (from === undefined) {
      // A step that failed already shows two lines of its error: the details grow out of those.
      from = 0;
      for (const root of rootsNow.current) if (root.kind === "topic") forEachNode(root.node, node => { if (node.id === id) from = closedRoom(node); });
    }
    growing.current.get(id)?.();
    growing.current.set(id, tweenTo(from, to, GROW_MS, value => {
      extras.current.set(id, value);
      applyExtras();
      place(drawn.current);
    }, () => { growing.current.delete(id); onDone?.(); }));
  }, [applyExtras, place]);
  const onMeasure = useCallback((id: string, height: number) => {
    if (!store.has(id) || closing.current.has(id) || wanted.current.get(id) === height) return;
    wanted.current.set(id, height);
    growTo(id, height);
  }, [store, growTo]);
  useLayoutEffect(() => {
    const running = growing.current;
    return () => { running.forEach(stop => stop()); running.clear(); };
  }, []);

  /** Moves the view to `target`, the deepest column to show. The blocks are
   *  laid out for where it ends before it starts, so the move is the track
   *  sliding and nothing else; `held` keeps its place on screen. */
  const travel = useCallback((target: number, held: ChartNode | null) => {
    const content = inner.current;
    if (!content) return;
    stopTravel.current();
    focus.current = target;
    const { first, last } = chartWindow(target, count.current, levels);
    const from = shift.current;
    const to = -first * columnWidth.current;
    // The heights change with the slide, in the same time on the same curve.
    if (last !== drawn.current) place(last, held, travelMs(to - from, columnWidth.current));
    const done = () => { moving.current = false; content.style.willChange = ""; stopTravel.current = () => {}; };
    if (Math.abs(to - from) < 0.5) { setShift(to); done(); return; }
    moving.current = true;
    content.style.willChange = "transform";
    stopTravel.current = tweenTo(from, to, travelMs(to - from, columnWidth.current), setShift, done);
  }, [place, setShift, levels]);

  const pull = useRef(onPull);
  pull.current = onPull;
  useLayoutEffect(() => {
    const scroller = box.current;
    const content = inner.current;
    if (!scroller || !content) return;
    const onPull = { move: (distance: number) => pull.current?.move(distance), end: (distance: number, speed: number) => pull.current?.end(distance, speed) };
    // Columns move by the track's transform alone; the box never scrolls sideways.
    const onScroll = () => {
      if (scroller.scrollLeft) scroller.scrollLeft = 0;
      if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; reveal(); });
    };
    // A sideways swipe turns one column: the track follows the fingers, a
    // column at most; when they lift it carries on to the column they were
    // heading for if they went far or fast enough, and otherwise returns,
    // in one move. Past the last column it gives a little. Past the first,
    // the whole page follows: that is the way back.
    let home = 0;
    let start = 0;
    let pulled = 0;
    const within = (next: number | null) => next !== null && chartWindow(next, count.current, levels).last >= floorNow.current ? next : null;
    const stopSwipe = observeSwipe(scroller, {
      begin: () => {
        // A swipe that starts while the track still moves carries on from where it is.
        stopTravel.current();
        home = focus.current;
        start = shift.current;
        moving.current = true;
        content.style.willChange = "transform";
        return true;
      },
      move: moved => {
        const each = columnWidth.current;
        const columns = count.current;
        const homeX = -chartWindow(home, columns, levels).first * each;
        const back = homeX > -0.5 && start > -0.5 && pull.current ? Math.max(0, -moved) : 0;
        if (back > 0 || pulled > 0) { pulled = back; onPull.move(back); }
        if (back > 0) { setShift(0); return; }
        // No column before the first one with something in it.
        const behind = within(stepFocus(home, columns, -1, levels)) === null ? homeX : homeX + each;
        let x = Math.max(homeX - each, Math.min(behind, start - moved));
        // The column coming in from the right is laid out before it shows.
        const ahead = x < homeX - 0.5 ? stepFocus(home, columns, 1, levels) : null;
        const level = chartWindow(ahead ?? home, columns, levels).last;
        if (level !== drawn.current) place(level, null, travelMs(each, each));
        const far = -(levels - columns) * each;
        if (x > 0) x = rubberBand(x);
        else if (x < far) x = far + rubberBand(x - far);
        setShift(x);
      },
      end: (moved, speed) => {
        if (pulled > 0) { const distance = pulled; pulled = 0; moving.current = false; content.style.willChange = ""; onPull.end(distance, -speed); return; }
        const next = swipeTurns(moved, speed, SWIPE_TURNS) ? within(stepFocus(home, count.current, Math.sign(moved), levels)) : null;
        travel(next ?? home, null);
      },
      notch: direction => {
        const next = within(stepFocus(focus.current, count.current, direction, levels));
        if (next !== null) travel(next, null);
      },
    }, true);
    // Keyboard focus on a block out of view brings its column in.
    const onFocusIn = (event: FocusEvent) => {
      const column = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>("[data-chart-column]") : null;
      if (scroller.scrollLeft) scroller.scrollLeft = 0;
      if (!column || moving.current) return;
      const depth = Number(column.dataset.chartColumn);
      const { first, last } = chartWindow(focus.current, count.current, levels);
      if (depth < first || depth > last) travel(focusOn(focus.current, count.current, depth, levels), null);
    };
    scroller.addEventListener("scroll", onScroll);
    content.addEventListener("focusin", onFocusIn);
    // The sidebar's width decides the columns in the frame it changes.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => { if (fit()) rest(); else reveal(); });
    observer?.observe(scroller);
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      content.removeEventListener("focusin", onFocusIn);
      stopSwipe();
      observer?.disconnect();
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      // A move cut short lands where it was going.
      if (moving.current) rest();
    };
  }, [box, place, reveal, fit, rest, travel, setShift, levels]);

  const open = useCallback((node: ChartNode) => {
    if (node.kids?.length) { travel(focusOn(focus.current, count.current, node.depth + 1, levels), node); return; }
    if (!opensInPlace(node) || closing.current.has(node.id)) return;
    if (!store.has(node.id)) { store.toggle(node.id); return; }
    // Its details stay until the block has closed over them.
    closing.current.add(node.id);
    wanted.current.delete(node.id);
    growTo(node.id, closedRoom(node), () => {
      closing.current.delete(node.id);
      extras.current.delete(node.id);
      store.toggle(node.id);
      applyExtras();
      place(drawn.current);
    });
  }, [travel, store, growTo, applyExtras, place, levels]);
  /** One column back. False when the first column is already in view. */
  const back = useCallback(() => {
    const next = stepFocus(focus.current, count.current, -1, levels);
    if (next === null || chartWindow(next, count.current, levels).last < floorNow.current) return false;
    travel(next, null);
    return true;
  }, [travel, levels]);
  useLayoutEffect(() => { if (!stepBack) return; stepBack.current = back; return () => { stepBack.current = null; }; }, [stepBack, back]);

  const nodes = nodesByDepth(roots);
  // A block that was not drawn again still names the node of the chart it was last drawn from.
  const openById = useCallback((id: string) => {
    for (const root of rootsNow.current) {
      if (root.kind !== "topic") continue;
      let hit: ChartNode | null = null;
      forEachNode(root.node, node => { if (node.id === id) hit = node; });
      if (hit) { open(hit); return; }
    }
  }, [open]);
  const block = (node: ChartNode) => <Block key={node.id} node={node} context={context} executionOf={executionOf} open={store.has(node.id)} mainShape={mainShape} marks={marks} onOpen={openById} onBack={back} onMeasure={onMeasure} />;
  const nameOf = (depth: number) => names[Math.min(depth, names.length - 1)];
  const others = roots.filter((root): root is Exclude<ChartRoot, { kind: "topic" }> => root.kind !== "topic");

  // A block's details are built from the pages' rows, which read the panel
  // and the open rows from above them: the chart gives both itself, so it
  // works wherever it is drawn, on a page or on the overview.
  return <ActivityPanelReact.Provider value={context}><ActivityRowStoreReact.Provider value={store}><div ref={box as RefObject<HTMLDivElement>} className={styles.cols}>
    <div ref={inner} className="relative flex w-max" data-chart-lead="0">
      {nodes.map((column, depth) => <div key={depth} className="relative w-[var(--chart-col,256px)] flex-none" data-chart-column={depth}>
        <div className={styles.ch}>
          {backInHeader && depth > floor ? <Button type="button" variant="ghost" className={`${pageStyles.tl} ${styles.chBack}`} data-chart-back={depth} onClick={back} aria-label={context.text("Back one column", "退回一列")}>
            <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />{nameOf(depth)}
          </Button> : null}
          <span data-chart-name={depth}>{nameOf(depth)}</span>{depth > 0 ? <span className={styles.crumb} data-chart-crumb={depth} /> : null}
        </div>
        {column.filter(node => !node.parent?.folds).map(block)}
        {depth > 0 ? nodes[depth - 1].filter(node => node.folds).map(node => <div key={node.id} className={`${styles.cell} absolute right-0 left-0 overflow-clip`} data-chart-cell={node.id}>{node.kids!.map(block)}</div>) : null}
        {others.map(root => {
          if (root.kind === "gap") return <div key={root.id} className={`${styles.block} ${styles.gap}`} data-chart-root={root.id} data-small={depth >= 2 ? "1" : undefined}><span className={styles.day}>{root.day}</span><span className={styles.dash} /><span className={styles.line}>{depth === 0 ? root.idle : null}</span></div>;
          if (depth > 0) return null;
          if (root.kind === "more") return <div key={root.id} className={styles.block} data-chart-root={root.id}><span /><span /><span className="flex h-9 items-center pl-1"><Button type="button" variant="ghost" className={styles.action} onClick={() => void showMore?.()}>{root.label}</Button></span></div>;
          return <div key={root.id} className={styles.block} data-chart-root={root.id} data-row-id={root.id} role="button" tabIndex={-1} onClick={onOpenNextWake}
            onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenNextWake?.(); } }}>
            <span className={styles.time}>{root.time}</span>
            <span className="flex flex-col items-center"><i className={styles.bar} data-bar="1" data-next="1" /></span>
            <span className={styles.card}><span className={styles.label}><span className={styles.title}>{root.title}</span><span className={styles.line} data-strong={root.strong ? "1" : undefined}>{root.line}</span></span></span>
          </div>;
        })}
      </div>)}
      {empty ? <div className={styles.empty} style={{ position: "absolute", top: HEADER_HEIGHT, left: 0, width: "var(--chart-col)" }}>{empty}</div> : null}
    </div>
  </div></ActivityRowStoreReact.Provider></ActivityPanelReact.Provider>;
}
