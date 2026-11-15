"use client";

/** An Agent's time chart. React draws the blocks; where they sit is written
 *  straight on the elements. The sidebar's width decides how many whole
 *  columns show; moving between columns slides the track by its transform
 *  alone, with the blocks already laid out for where it ends. A block with
 *  more under it leads one column right; a block that is one call or one
 *  message opens its details in place. */
import { useCallback, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent, type MutableRefObject, type RefObject } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { availableExecutionActions, buildExecutionCommand, newCommandId, type ExecutionCommandAction, type ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import type { ActivityRun } from "@/lib/net/execution/activity-runs-client";
import { processIsActive } from "@/lib/net/execution/process-client";
import type { ActivityPanelContext, PagePull } from "./activity-page-types";
import { ActivityKeyValue, ActivityRowStoreReact, createActivityRowStore } from "./activity-page-row";
import { stepTitle } from "./activity-run-wording";
import { COLUMN_WIDTH, HEADER_HEIGHT, LEVELS, buildChart, chartWindow, closedRoom, columnsFor, focusOn, readableInput, forEachNode, opensInPlace, paintColor, rootHeight, sizeChart, stepFocus, travelMs, type ChartNode, type ChartRoot } from "./agent-chart-model";
import { useExecutionControls } from "./execution-controls";
import { EventDetail } from "./run-insides";
import { observeSwipe, rubberBand, swipeTurns, tweenTo } from "./sideways";
import { useActivityRuns } from "./use-activity-runs";
import { useRunInspection } from "./use-run-inspection";
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
  nextWake: { at: number; line: string } | null;
  onOpenNextWake: () => void;
  /** Set to what moves the chart one column back, for the page's Back. */
  stepBack?: MutableRefObject<(() => boolean) | null>;
  /** A swipe past the first column pulls the page itself: the way back. */
  onPull?: PagePull;
}

const LIVE_ACTIONS: readonly ExecutionCommandAction[] = ["pause", "continue", "cancel"];

function RunActions({ snapshot, context }: { snapshot: ExecutionSnapshot; context: ActivityPanelContext }) {
  const { text, connected } = context;
  const inspection = useRunInspection(snapshot, context.sessionId, context.active);
  const controls = useExecutionControls(snapshot, inspection, context);
  const [writing, setWriting] = useState(false);
  const [instruction, setInstruction] = useState("");
  const decision = context.waitingFor(snapshot);
  const available = availableExecutionActions(snapshot);
  const labels: Partial<Record<ExecutionCommandAction, string>> = { pause: text("Pause", "暂停"), continue: text("Continue", "继续"), cancel: text("Cancel", "取消") };
  const hold = (event: MouseEvent) => event.stopPropagation();
  const send = () => {
    const message = instruction.trim();
    if (!message || !connected) return;
    void controls.submit(buildExecutionCommand(snapshot, "steer", newCommandId(), { message }));
    setInstruction("");
    setWriting(false);
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    event.stopPropagation();
    if (event.key === "Enter") send();
    if (event.key === "Escape") setWriting(false);
  };
  if (writing) {
    return <span className={styles.actions} onClick={hold}>
      <Input autoFocus className={styles.steer} value={instruction} aria-label={text("Add instruction", "补充指令")} placeholder={text("Applied at the next safe point", "在下一个安全点生效")}
        onChange={event => setInstruction(event.target.value)} onKeyDown={onKey} onBlur={() => { if (!instruction.trim()) setWriting(false); }} />
    </span>;
  }
  const rejected = Object.values(controls.commandResults).some(result => result.status === "rejected");
  if (rejected) return <span className={styles.actions}><span className={styles.line}>{text("That did not go through.", "没有生效。")}</span></span>;
  const action = (key: string, label: string, onClick: () => void, busy = false) =>
    <Button key={key} type="button" variant="ghost" className={styles.action} disabled={!connected || busy} onClick={event => { event.stopPropagation(); onClick(); }}>{label}</Button>;
  return <span className={styles.actions}>
    {decision?.answerLabel ? action("answer", decision.answerLabel, () => context.answerInChat(snapshot)) : null}
    {decision ? null : <>
      {LIVE_ACTIONS.slice(0, 2).filter(item => available.includes(item)).map(item => action(item, labels[item]!, () => controls.run(item), controls.pendingActions.has(`execution.${item}`)))}
      {available.includes("steer") ? action("steer", text("Add instruction", "补充指令"), () => setWriting(true)) : null}
      {available.includes("cancel") ? action("cancel", labels.cancel!, () => controls.run("cancel"), controls.pendingActions.has("execution.cancel")) : null}
    </>}
  </span>;
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

function Block({ node, context, executionOf, open, onOpen, onBack, onMeasure }: {
  node: ChartNode; context: ActivityPanelContext; executionOf: (run: ActivityRun) => ExecutionSnapshot | null; open: boolean;
  onOpen: (node: ChartNode) => void; onBack: () => void; onMeasure: (id: string, height: number) => void;
}) {
  const travels = Boolean(node.kids?.length);
  const acts = travels || opensInPlace(node);
  const snapshot = node.hasActions && node.run ? executionOf(node.run) : null;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); onBack(); return; }
    if (event.key === "Enter" || event.key === " " || (travels && event.key === "ArrowRight")) { event.preventDefault(); onOpen(node); }
  };
  return <div className={styles.block} data-chart-id={node.id} data-small={node.depth >= 2 ? "1" : undefined} data-open={open ? "1" : undefined}
    {...acts ? { role: "button", tabIndex: -1, "data-row-id": node.id, onClick: () => onOpen(node), onKeyDown } : {}}
    {...acts && !travels ? { "aria-expanded": open } : {}}>
    <span className={styles.time}>{node.time}</span>
    <span className={styles.rail}><i className={styles.bar} data-bar="1" style={{ background: paintColor(node.paint) }} /></span>
    <span className={styles.card}><span className={styles.label}>
      {node.title ? <span className={styles.title}>{node.title}</span> : null}
      <span className={styles.line} data-strong={node.strong ? "1" : undefined}>{node.line}</span>
      {node.detail && !open ? <span className={styles.detail}>{node.detail}</span> : null}
      {snapshot ? <RunActions snapshot={snapshot} context={context} /> : null}
    </span>{open ? <BlockInsides node={node} context={context} onMeasure={onMeasure} /> : null}</span>
  </div>;
}

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

function nodesByDepth(roots: ChartRoot[]): ChartNode[][] {
  const columns: ChartNode[][] = Array.from({ length: LEVELS }, () => []);
  for (const root of roots) if (root.kind === "topic") forEachNode(root.node, node => columns[node.depth].push(node));
  return columns;
}

export function AgentChart({ head, context, box, reportName, nextWake, onOpenNextWake, stepBack, onPull }: AgentChartProps) {
  const { text, now } = context;
  const source = useMemo(() => ({ kind: "branch" as const, head }), [head]);
  const { runs, total, loaded, failed, showMore } = useActivityRuns({ sessionId: context.sessionId, source, active: context.active, isOpen: NO_ROWS_OPEN, openVersion: 0, chart: true });
  const executionOf = useCallback((run: ActivityRun) => run.execution_id ? context.controller.executions.find(item => item.execution_id === run.execution_id) ?? null : null, [context.controller.executions]);
  const waiting = runs.some(run => { const snapshot = executionOf(run); return snapshot ? context.waitingFor(snapshot) !== null : false; });
  const anyLive = waiting || runs.some(run => run.ended_at === null);
  // Text and bars move only every few seconds while something runs, never while idle.
  const beat = anyLive ? Math.floor(now / 5) * 5 : 0;
  const roots = useMemo<ChartRoot[]>(() => buildChart(runs, {
    text, now: anyLive ? beat : now,
    waitOf: run => { const snapshot = executionOf(run); return snapshot ? context.waitingFor(snapshot) : null; },
    hasActions: run => executionOf(run) !== null,
    liveCalls: run => {
      if (!run.execution_id || run.ended_at !== null) return [];
      const saved = new Set((run.steps ?? []).flatMap(event => event.kind === "step" && event.tool_call_id ? [event.tool_call_id] : []));
      const calls = (context.processes.calls ?? []).filter(call => call.execution_id === run.execution_id && call.status === "running" && call.name !== "agent" && !(call.tool_call_id && saved.has(call.tool_call_id)))
        .map(call => ({ id: call.id, at: call.started_at, label: call.description || stepTitle(call.name, undefined, text) }));
      const programs = context.processes.items.filter(item => item.execution_id === run.execution_id && processIsActive(item) && item.status !== "unknown")
        .map(item => ({ id: item.id, at: item.started_at, label: item.title?.trim() || item.command.trim() }));
      return [...calls, ...programs];
    },
    reportName, nextWake, earlier: Math.min(50, total - runs.length),
    // `now` is `beat` while anything runs; while idle a new render needs no new chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [runs, total, beat, waiting, text, executionOf, nextWake?.at, nextWake?.line, context.processes.calls, context.processes.items]);

  const inner = useRef<HTMLDivElement>(null);
  const parts = useRef<ChartParts | null>(null);
  const rootsNow = useRef(roots);
  rootsNow.current = roots;
  const columnsNow = useRef<ChartNode[][]>([]);
  // The columns: how many show, how wide each is, the deepest one the reader
  // went to, the level the blocks are laid out at, and where the track sits.
  const count = useRef(1);
  const columnWidth = useRef(COLUMN_WIDTH);
  const focus = useRef(0);
  const drawn = useRef(-1);
  const shift = useRef(0);
  // A move between columns, or fingers on the trackpad, owns the track.
  const moving = useRef(false);
  const stopTravel = useRef<() => void>(() => {});
  const frame = useRef(0);
  // Which blocks are open, and how tall each one's details measured.
  const [store] = useState(createActivityRowStore);
  useSyncExternalStore(store.subscribe, store.version, store.version);
  const extras = useRef(new Map<string, number>());
  const applyExtras = useCallback(() => {
    for (const root of rootsNow.current) if (root.kind === "topic") forEachNode(root.node, node => { node.extra = extras.current.get(node.id) ?? 0; });
    sizeChart(rootsNow.current);
  }, []);

  const setShift = useCallback((x: number) => {
    shift.current = x;
    if (inner.current) inner.current.style.transform = x ? `translate3d(${x}px,0,0)` : "";
  }, []);

  /** Draws the blocks near the view and names each column's parent. It
   *  moves nothing, so it can run on every frame of a vertical scroll. */
  const reveal = useCallback(() => {
    const scroller = box.current;
    const found = parts.current;
    if (!scroller || !found) return;
    const columns = columnsNow.current;
    const at = scroller.scrollTop;
    const view = scroller.clientHeight || 0;
    // Only blocks near the view are drawn: a long chart has hundreds a screen away.
    if (view > 0) {
      const show = (element: HTMLElement | undefined, node: ChartNode) => {
        if (!element) return;
        const display = node.top < at + 2 * view && node.top + node.height > at - view ? "" : "none";
        if (element.style.display !== display) element.style.display = display;
      };
      for (let depth = 0; depth < LEVELS - 1; depth += 1) for (const node of columns[depth] ?? []) show(found.blocks.get(node.id), node);
      for (const node of columns[LEVELS - 2] ?? []) if (node.kids) show(found.cells.get(node.id), node);
    }
    found.crumbs.forEach(crumb => {
      const list = columns[Number(crumb.dataset.chartCrumb)] ?? [];
      const top = list.find(node => node.top + node.height > at + HEADER_HEIGHT);
      const name = top?.parent ? top.parent.title ?? top.parent.line : "";
      if (crumb.textContent !== name) crumb.textContent = name;
    });
  }, [box]);

  /** Lays every block out at one level, at once. The block given as `held`,
   *  or else the one at the top of the view, keeps its place on screen. */
  const place = useCallback((level: number, held: ChartNode | null = null) => {
    const scroller = box.current;
    const content = inner.current;
    if (!scroller || !content) return;
    const put = (element: HTMLElement | undefined, top: number, height: number) => { if (element) { element.style.top = `${top}px`; element.style.height = `${height}px`; } };
    const { blocks, bars, cells, others, columnElements } = parts.current ??= findParts(content);
    const columns = columnsNow.current = nodesByDepth(rootsNow.current);
    // Read the box before any write below, or each read costs a layout.
    const at = scroller.scrollTop;
    let anchor: ChartNode | undefined = held ?? undefined;
    const offset = held ? held.top - at : 0;
    let fraction = 0;
    if (!anchor && at >= 1) {
      const depth = Math.min(LEVELS - 2, drawn.current < 0 ? level : Math.min(drawn.current, level));
      anchor = columns[depth].find(node => node.height > 0 && node.top + node.height > at);
      if (anchor) fraction = (at - anchor.top) / anchor.height;
    }

    const placeNode = (node: ChartNode, top: number): number => {
      const height = node.heights[level];
      const moved = node.top !== top || node.height !== height;
      node.top = top; node.height = height;
      if (moved) {
        put(blocks.get(node.id), top, height);
        const bar = bars.get(node.id);
        if (bar) bar.style.height = `${Math.max(2, height - 6)}px`;
      }
      if (!node.kids) return height;
      if (node.depth === LEVELS - 2) {
        const cell = cells.get(node.id);
        if (moved) put(cell, top, height);
        let below = 0;
        for (const kid of node.kids) below += placeNode(kid, below);
        if (cell) {
          const scrolls = level === LEVELS - 1 && below > height + 1;
          // Setting scrollTop lays the page out, so only a cell that did scroll is reset.
          if (scrolls) cell.dataset.scrolls = "1";
          else if (cell.dataset.scrolls) { delete cell.dataset.scrolls; cell.scrollTop = 0; }
        }
        return height;
      }
      let below = top;
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
    if (anchor && anchor.height > 0) {
      const top = held ? anchor.top - offset : anchor.top + fraction * anchor.height;
      if (Math.abs(scroller.scrollTop - top) >= 1) scroller.scrollTop = top;
    }
    reveal();
  }, [box, reveal]);

  /** How many columns the width holds and how wide each is. True when that changed. */
  const fit = useCallback((): boolean => {
    const scroller = box.current;
    const content = inner.current;
    const width = scroller?.clientWidth ?? 0;
    if (!scroller || !content || width <= 0) return false;
    const columns = columnsFor(width);
    const each = width / columns;
    if (columns === count.current && each === columnWidth.current && content.style.getPropertyValue("--chart-col")) return false;
    count.current = columns;
    columnWidth.current = each;
    content.style.setProperty("--chart-col", `${each}px`);
    return true;
  }, [box]);

  /** The view the focus asks for, at once: a width change lands in its own frame. */
  const rest = useCallback(() => {
    stopTravel.current();
    moving.current = false;
    if (inner.current) inner.current.style.willChange = "";
    const { first, last } = chartWindow(focus.current, count.current);
    setShift(-first * columnWidth.current);
    if (last !== drawn.current) place(last);
  }, [place, setShift]);

  useLayoutEffect(() => {
    parts.current = null;
    if (extras.current.size) applyExtras();
    fit();
    const { first, last } = chartWindow(focus.current, count.current);
    if (!moving.current) setShift(-first * columnWidth.current);
    place(drawn.current < 0 ? last : drawn.current);
  }, [roots, place, fit, setShift, applyExtras]);

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
    const { first, last } = chartWindow(target, count.current);
    if (last !== drawn.current) place(last, held);
    const from = shift.current;
    const to = -first * columnWidth.current;
    const done = () => { moving.current = false; content.style.willChange = ""; stopTravel.current = () => {}; };
    if (Math.abs(to - from) < 0.5) { setShift(to); done(); return; }
    moving.current = true;
    content.style.willChange = "transform";
    stopTravel.current = tweenTo(from, to, travelMs(to - from, columnWidth.current), setShift, done);
  }, [place, setShift]);

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
        const homeX = -chartWindow(home, columns).first * each;
        const back = homeX > -0.5 && start > -0.5 && pull.current ? Math.max(0, -moved) : 0;
        if (back > 0 || pulled > 0) { pulled = back; onPull.move(back); }
        if (back > 0) { setShift(0); return; }
        let x = Math.max(homeX - each, Math.min(homeX + each, start - moved));
        // The column coming in from the right is laid out before it shows.
        const ahead = x < homeX - 0.5 ? stepFocus(home, columns, 1) : null;
        const level = chartWindow(ahead ?? home, columns).last;
        if (level !== drawn.current) place(level);
        const far = -(LEVELS - columns) * each;
        if (x > 0) x = rubberBand(x);
        else if (x < far) x = far + rubberBand(x - far);
        setShift(x);
      },
      end: (moved, speed) => {
        if (pulled > 0) { const distance = pulled; pulled = 0; moving.current = false; content.style.willChange = ""; onPull.end(distance, -speed); return; }
        const next = swipeTurns(moved, speed, SWIPE_TURNS) ? stepFocus(home, count.current, Math.sign(moved)) : null;
        travel(next ?? home, null);
      },
      notch: direction => {
        const next = stepFocus(focus.current, count.current, direction);
        if (next !== null) travel(next, null);
      },
    }, true);
    // Keyboard focus on a block out of view brings its column in.
    const onFocusIn = (event: FocusEvent) => {
      const column = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>("[data-chart-column]") : null;
      if (scroller.scrollLeft) scroller.scrollLeft = 0;
      if (!column || moving.current) return;
      const depth = Number(column.dataset.chartColumn);
      const { first, last } = chartWindow(focus.current, count.current);
      if (depth < first || depth > last) travel(focusOn(focus.current, count.current, depth), null);
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
  }, [box, place, reveal, fit, rest, travel, setShift]);

  const open = useCallback((node: ChartNode) => {
    if (node.kids?.length) { travel(focusOn(focus.current, count.current, node.depth + 1), node); return; }
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
  }, [travel, store, growTo, applyExtras, place]);
  /** One column back. False when the first column is already in view. */
  const back = useCallback(() => {
    const next = stepFocus(focus.current, count.current, -1);
    if (next === null) return false;
    travel(next, null);
    return true;
  }, [travel]);
  useLayoutEffect(() => { if (!stepBack) return; stepBack.current = back; return () => { stepBack.current = null; }; }, [stepBack, back]);

  const names = [text("Topics", "话题"), text("Messages", "消息"), text("Steps", "步骤"), text("Inside a step", "步骤里面")];
  const nodes = nodesByDepth(roots);
  const block = (node: ChartNode) => <Block key={node.id} node={node} context={context} executionOf={executionOf} open={store.has(node.id)} onOpen={open} onBack={back} onMeasure={onMeasure} />;
  const others = roots.filter((root): root is Exclude<ChartRoot, { kind: "topic" }> => root.kind !== "topic");

  return <ActivityRowStoreReact.Provider value={store}><div ref={box as RefObject<HTMLDivElement>} className={styles.cols}>
    <div ref={inner} className={styles.inner}>
      {names.map((name, depth) => <div key={name} className={styles.col} data-chart-column={depth}>
        <div className={styles.ch}><span>{name}</span>{depth > 0 ? <span className={styles.crumb} data-chart-crumb={depth} /> : null}</div>
        {depth < LEVELS - 1 ? nodes[depth].map(block) : nodes[LEVELS - 2].filter(node => node.kids).map(node => <div key={node.id} className={styles.cell} data-chart-cell={node.id}>{node.kids!.map(block)}</div>)}
        {others.map(root => {
          if (root.kind === "gap") return <div key={root.id} className={`${styles.block} ${styles.gap}`} data-chart-root={root.id} data-small={depth >= 2 ? "1" : undefined}><span className={styles.day}>{root.day}</span><span className={styles.dash} /><span className={styles.line}>{depth === 0 ? root.idle : null}</span></div>;
          if (depth > 0) return null;
          if (root.kind === "more") return <div key={root.id} className={styles.block} data-chart-root={root.id}><span /><span /><span className={styles.more}><Button type="button" variant="ghost" className={styles.action} onClick={() => void showMore()}>{root.label}</Button></span></div>;
          return <div key={root.id} className={styles.block} data-chart-root={root.id} data-row-id={root.id} role="button" tabIndex={-1} onClick={onOpenNextWake}
            onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenNextWake(); } }}>
            <span className={styles.time}>{root.time}</span>
            <span className={styles.rail}><i className={styles.bar} data-bar="1" data-next="1" /></span>
            <span className={styles.card}><span className={styles.label}><span className={styles.title}>{root.title}</span><span className={styles.line}>{root.line}</span></span></span>
          </div>;
        })}
      </div>)}
      {loaded && runs.length === 0 ? <div className={styles.empty} style={{ position: "absolute", top: HEADER_HEIGHT, left: 0, width: "var(--chart-col)" }}>{failed ? text("Could not read the runs. Retrying automatically.", "读不到运行记录，正在自动重试。") : text("No runs yet.", "还没有运行记录。")}</div> : null}
    </div>
  </div></ActivityRowStoreReact.Provider>;
}
