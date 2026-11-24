"use client";

/** The one row every Activity page is built from, its open-in-place detail,
 *  and the shared state of a page: which rows are open and which long values
 *  are shown whole. That state lives outside React's tree of rows, so a poll
 *  can never close a row. */
import { createContext, useContext, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ActivityActionRowProps, ActivityKeyValueProps, ActivityMark, ActivityPanelContext, ActivityRowProps, ActivityTone } from "./activity-page-types";
import { axisLabels, type ActivityAxis } from "./activity-page-geometry";
import styles from "./activity-page.module.css";

/** A line's layout: the left cell, the gap and the right cell side by side,
 *  as wide as the track. */
export const LINE = "flex w-[var(--track)] items-stretch";
/** The gap between the two halves. */
export const GAP = "w-4 flex-none";

export interface ActivityRowStore {
  has: (id: string) => boolean;
  /** Opens a row before it is first drawn. Does not notify. */
  seed: (id: string) => void;
  /** Marks a row's default as applied, returning true the first time. */
  decide: (id: string) => boolean;
  toggle: (id: string) => void;
  full: Set<string>;
  subscribe: (listener: () => void) => () => void;
  version: () => number;
}

export function createActivityRowStore(): ActivityRowStore {
  const open = new Set<string>();
  const decided = new Set<string>();
  const listeners = new Set<() => void>();
  let version = 0;
  return {
    has: id => open.has(id),
    seed: id => { decided.add(id); open.add(id); },
    decide: id => { if (decided.has(id)) return false; decided.add(id); return true; },
    toggle: id => { if (open.has(id)) open.delete(id); else open.add(id); version += 1; listeners.forEach(listener => listener()); },
    full: new Set(),
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    version: () => version,
  };
}

export const ActivityPanelReact = createContext<ActivityPanelContext | null>(null);
export const ActivityRowStoreReact = createContext<ActivityRowStore | null>(null);
/** Inside the run list a row's right cell keeps the grid; above it the cell is blank. */
export const ActivityInListReact = createContext(false);
/** Set by a run list so the page head can say what the bars mean. */

/** What rows and frames read of the page they are on. */
export type ActivityPanelView = Pick<ActivityPanelContext, "text" | "connected" | "close" | "pull" | "actionError">;

/** Drawn outside a page, a row still draws, in English and doing nothing,
 *  rather than taking the window down with it. */
const NO_PANEL: ActivityPanelView = { text: english => english, connected: false, close: () => {}, pull: undefined, actionError: null };
const NO_STORE = createActivityRowStore();

export function useActivityPanel(): ActivityPanelView {
  return useContext(ActivityPanelReact) ?? NO_PANEL;
}

export function useActivityRowStore(): ActivityRowStore {
  return useContext(ActivityRowStoreReact) ?? NO_STORE;
}

const join = (...names: unknown[]) => names.filter(Boolean).join(" ");
const indentStyle = (indent: number | undefined) => indent ? { "--indent": `${indent}px` } as CSSProperties : undefined;

function Chevron({ open }: { open: boolean }) {
  return <span className={styles.cs} data-open={open ? "" : undefined}><ChevronRight size={16} strokeWidth={2} aria-hidden="true" /></span>;
}

function EmptyChevron() {
  return <span className={styles.cs} />;
}

/** The inside of a left cell: glyph, title over status, then the chevron. */
export function ActivityCell({ glyph, action, title, status, tone, chevron, extra }: {
  glyph?: ReactNode; action?: boolean; title?: ReactNode; status?: ReactNode; tone?: ActivityTone; chevron?: ReactNode; extra?: ReactNode;
}) {
  return <>
    <span className={`${styles.gs} ml-[var(--indent,0px)] grid size-[22px] flex-none place-items-center`} data-action={action ? "" : undefined}>{glyph}</span>
    <span className="grid min-w-0 flex-1 gap-px">
      {title ? <span className={styles.t}>{typeof title === "object" ? title : <span>{title}</span>}</span> : null}
      {status ? <span className={styles.s} data-tone={tone}>{status}</span> : null}
      {extra}
    </span>
    {chevron ?? <EmptyChevron />}
  </>;
}

function Mark({ mark }: { mark: ActivityMark }) {
  const tone = mark.tone === "muted" || mark.tone === "ok" ? undefined : mark.tone;
  if (mark.kind === "tick") return <i className={styles.tick} data-mark={tone} style={{ left: `min(${mark.left}%, calc(100% - 3px))` }} />;
  return <i className={styles.gbar} data-mark={tone} data-flat={mark.flatEnd ? "" : undefined} style={{ left: `${mark.left}%`, width: `${mark.width}%` }} />;
}

const emptyGrid = <><span className={styles.tm} /><span className={styles.plot} /></>;

export function ActivityRow({ id, title, status, tone, glyph, indent, time, mark, defaultOpen, children }: ActivityRowProps) {
  const store = useActivityRowStore();
  if (defaultOpen && store.decide(id)) store.seed(id);
  const open = useSyncExternalStore(store.subscribe, () => store.has(id), () => store.has(id));
  const inList = useContext(ActivityInListReact);
  const button = useRef<HTMLButtonElement>(null);
  const openable = children !== undefined;
  const hasRight = time !== undefined || Boolean(mark);
  const shownTone = tone === "muted" || tone === "ok" ? undefined : tone;
  const cell = <ActivityCell glyph={glyph} title={title} status={status} tone={shownTone} chevron={openable ? <Chevron open={open} /> : undefined} />;
  const toggle = () => { store.toggle(id); button.current?.focus({ preventScroll: true }); };
  return <>
    <div className={join(styles.ln, LINE, title && hasRight && styles.hasn, !hasRight && styles.nor)} style={indentStyle(indent)}
      data-act={openable ? "" : undefined} onClick={openable ? toggle : undefined}>
      {openable
        ? <Button ref={button} type="button" variant="ghost" className={`${styles.lc} ${styles.snap}`} tabIndex={-1} data-row-id={id} aria-expanded={open}>{cell}</Button>
        : <div className={`${styles.lc} ${styles.snap}`}>{cell}</div>}
      <span className={`${styles.gp} ${GAP}`} />
      <div className={`${styles.rc} ${styles.snap}`}>
        {title && hasRight ? <span className={styles.rn}>{title}</span> : null}
        {hasRight ? <><span className={styles.tm}>{time}</span><span className={styles.plot}>{mark ? <Mark mark={mark} /> : null}</span></> : inList ? emptyGrid : null}
      </div>
    </div>
    {openable && open ? <div className={`${styles.opened} contents`}>{children()}</div> : null}
  </>;
}

/** What a row opens to: always whole in the left half, or across both when they fit. */
export function ActivityDetail({ indent, children }: { indent?: number; children: ReactNode }) {
  return <div className={join(styles.ln, LINE, styles.dl)}>
    <div className={styles.det} style={indentStyle(indent)}>{children}</div>
    <span className={`${styles.gp} ${GAP}`} />
    <div className={styles.rc}>{emptyGrid}</div>
  </div>;
}

/** A line of plain words inside a detail. */
export function ActivityNote({ tone, children }: { tone?: ActivityTone; children: ReactNode }) {
  return <p className={styles.plain} data-tone={tone}>{children}</p>;
}

export function ActivityKeyValue({ label, value, mono, clamp = 8, indent }: ActivityKeyValueProps) {
  const store = useActivityRowStore();
  const { text } = useActivityPanel();
  const fullKey = `${label}|${value.slice(0, 48)}`;
  const element = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(() => store.full.has(fullKey));
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node || full) { setCut(false); return; }
    const measure = () => setCut(node.scrollHeight > node.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [full, value]);
  const showAll = () => {
    store.full.add(fullKey);
    setFull(true);
    element.current?.focus({ preventScroll: true });
  };
  const body = <div className={`${styles.kv} grid @min-[420px]:grid-cols-[120px_minmax(0,1fr)] @min-[420px]:gap-x-2`} data-bare={label ? undefined : ""}>
    {label ? <div className={styles.k}>{label}</div> : null}
    <div className="min-w-0">
      <div ref={element} tabIndex={-1} className={styles.v} data-mono={mono ? "" : undefined} data-full={full ? "" : undefined} style={full ? undefined : { WebkitLineClamp: clamp }}>{value}</div>
      {cut && !full ? <Button type="button" variant="link" size="xs" className={styles.sa} onClick={showAll}>{text("Show all", "展开全文")}</Button> : null}
    </div>
  </div>;
  return indent ? <div style={{ marginLeft: indent }}>{body}</div> : body;
}

/** A row that does something: icon in the glyph column, no chevron. */
export function ActivityActionRow({ icon: Icon, label, note, disabled, busy, indent, onClick }: ActivityActionRowProps) {
  const { text } = useActivityPanel();
  const inList = useContext(ActivityInListReact);
  const off = Boolean(disabled || busy);
  // Working… answers the reader's own press; a row that cannot act yet just waits, without a word.
  const status = busy ? text("Working…", "正在处理…") : note;
  const cell = <ActivityCell action glyph={<Icon size={20} strokeWidth={1.8} aria-hidden="true" />} title={label} status={status} />;
  const activate = () => { if (!off) onClick(); };
  return <div className={join(styles.ln, LINE, styles.nor)} style={indentStyle(indent)} data-act={off ? undefined : ""} onClick={activate}>
    <Button type="button" variant="ghost" className={`${styles.lc} ${styles.snap}`} tabIndex={-1} data-row-id={off ? undefined : `action:${label}`} disabled={off}>{cell}</Button>
    <span className={`${styles.gp} ${GAP}`} />
    <div className={`${styles.rc} ${styles.snap}`}>{inList ? emptyGrid : null}</div>
  </div>;
}

function AxisLabels({ axis }: { axis: ActivityAxis }) {
  return <>{axisLabels(axis).map((label, index) => <span key={index} className={styles.axl} data-last={index === axis.divisions ? "" : undefined} style={{ left: `${(index / axis.divisions * 100).toFixed(2)}%` }}>{label}</span>)}</>;
}

/** A sticky day label with the axis across the right half. */
export function ActivityDayRow({ name, axis }: { name: string; axis: ActivityAxis }) {
  return <div className={`${styles.ln} ${LINE} ${styles.day}`}>
    <div className={`${styles.lc} ${styles.snap}`}><span className={styles.sl}>{name}</span></div>
    <span className={`${styles.gp} ${GAP}`} />
    <div className={`${styles.rc} ${styles.snap}`}><span className={`${styles.tm} ${styles.tmDay} self-end pb-[6px]`}>{name}</span><span className={styles.plot}><AxisLabels axis={axis} /></span></div>
  </div>;
}

export function ActivityShowMore({ label, onClick }: { label: string; onClick: () => void }) {
  return <div className={join(styles.ln, LINE, styles.dl)}>
    <div className={styles.more}><Button type="button" variant="link" size="xs" className={styles.moreButton} onClick={onClick}>{label}</Button></div>
  </div>;
}
