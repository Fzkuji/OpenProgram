"use client";

/**
 * How far a background program is, and what it printed, for Activity's
 * program rows and pages. The numbers come from the server, which reads
 * them out of the program's output (openprogram/host/processes/progress.py).
 */
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import type { ProcessProgress } from "@/lib/net/execution/process-client";
import styles from "./program-progress.module.css";

type Text = (en: string, zh: string) => string;

export type ProgressLook = {
  /** 0..1, or null when the amount is unknown. */
  fraction: number | null;
  /** "45 / 100 samples", "62%". */
  count: string | null;
  /** A bar's own description ("eval"), shown before the count. */
  stage: string | null;
  /** "3.4/s", or with a byte unit "1.2 MB/s". */
  rate: string | null;
  /** Seconds left, compact ("1m 4s"). */
  eta: string | null;
  failed: boolean;
};

const BYTES = /^[kMGTP]?i?B$/;

function amount(value: number, unit?: string | null): string {
  if (unit && BYTES.test(unit) || value >= 100_000) {
    return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
  }
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
}

/** "45s", "1m 4s", "2h 5m": for figures in a narrow panel. */
export function clock(value: number): string {
  const s = Math.max(0, Math.round(value));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m}m ${s % 60}s` : `${m}m`;
  return m % 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 60)}h`;
}

export function seconds(value: number, text: Text): string {
  const s = Math.max(0, Math.round(value));
  if (s < 60) return text(`${s} s`, `${s} 秒`);
  const m = Math.floor(s / 60), rest = s % 60;
  if (m < 60) return rest ? text(`${m} min ${rest} s`, `${m} 分 ${rest} 秒`) : text(`${m} min`, `${m} 分钟`);
  const h = Math.floor(m / 60);
  return m % 60 ? text(`${h} h ${m % 60} min`, `${h} 小时 ${m % 60} 分`) : text(`${h} h`, `${h} 小时`);
}

export function progressLook(progress: ProcessProgress | null | undefined): ProgressLook | null {
  if (!progress) return null;
  const { current, total } = progress;
  const known = current != null && total != null && total > 0;
  const unit = progress.unit && progress.unit !== "it" ? progress.unit : null;
  // A marker's or counter's label names what is counted; a bar's is its description.
  const counted = progress.source === "bar" ? null : progress.label || null;
  let count: string | null = null;
  if (known && unit === "%") count = `${Math.round(current!)}%`;
  else if (known) count = `${amount(current!, unit)} / ${amount(total!, unit)}${unit ? ` ${unit}` : ""}${counted ? ` ${counted}` : ""}`;
  const per = unit && BYTES.test(unit) ? unit : null;
  return {
    fraction: known && progress.state !== "indeterminate" ? Math.min(1, current! / total!) : null,
    count,
    stage: progress.source === "bar" ? progress.label || null : null,
    rate: progress.rate ? `${progress.rate >= 10 ? Math.round(progress.rate) : progress.rate.toFixed(1)}${per ? ` ${per}` : ""}/s` : null,
    eta: progress.eta != null && known && current! < total! ? clock(progress.eta) : null,
    failed: progress.state === "error",
  };
}

/** A thin bar; with no fraction it sweeps to show work without an amount. */
export function ProgressBar({ fraction, tone, size = "sm", note, noteClassName }: {
  fraction: number | null; tone: "run" | "ok" | "err" | "muted"; size?: "sm" | "md";
  /** A short figure at the bar's right end, such as the time left. */
  note?: string | null;
  noteClassName?: string;
}) {
  const bar = <span className={styles.bar} data-size={size} data-tone={tone} data-indeterminate={fraction == null ? "1" : undefined}
    data-program-bar="" role="progressbar" aria-valuemin={0} aria-valuemax={100}
    aria-valuenow={fraction == null ? undefined : Math.round(fraction * 100)}>
    <span className={styles.fill} style={fraction == null ? undefined : { width: `${fraction * 100}%` }} />
  </span>;
  if (!note) return bar;
  return <span className={styles.withNote}>{bar}<span className={noteClassName ?? styles.note}>{note}</span></span>;
}

// ---- output ------------------------------------------------------------------

const SGR = /\x1b\[([0-9;]*)m/g;
// Every other escape: cursor moves, line erases, OSC sequences (titles, progress).
const OTHER_ESCAPES = /\x1b\[[0-?]*[ -/]*[@-ln-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

type Style = { fg?: number | string; bold?: boolean; dim?: boolean; italic?: boolean; underline?: boolean };

function applySgr(style: Style, codes: number[]): Style {
  const next = { ...style };
  for (let i = 0; i < codes.length; i++) {
    const c = codes[i];
    if (c === 0) { delete next.fg; next.bold = next.dim = next.italic = next.underline = false; }
    else if (c === 1) next.bold = true;
    else if (c === 2) next.dim = true;
    else if (c === 3) next.italic = true;
    else if (c === 4) next.underline = true;
    else if (c === 22) next.bold = next.dim = false;
    else if (c === 23) next.italic = false;
    else if (c === 24) next.underline = false;
    else if (c === 39) delete next.fg;
    else if ((c >= 30 && c <= 37) || (c >= 90 && c <= 97)) next.fg = c;
    else if (c === 38 && codes[i + 1] === 5) { const n = codes[i + 2]; next.fg = n < 8 ? 30 + n : n < 16 ? 82 + n : undefined; i += 2; }
    else if (c === 38 && codes[i + 1] === 2) { next.fg = `rgb(${codes[i + 2]}, ${codes[i + 3]}, ${codes[i + 4]})`; i += 4; }
  }
  return next;
}

/** Output as a terminal shows it: a line redrawn with carriage returns
 *  keeps its last frame, colours and weights stay, other escapes go. */
export function renderTerminal(output: string): ReactNode[] {
  const lines = output.replace(OTHER_ESCAPES, "").split("\n").map(line => {
    if (!line.includes("\r")) return line;
    const frames = line.split("\r").filter(Boolean);
    return frames.at(-1) ?? "";
  });
  const nodes: ReactNode[] = [];
  let style: Style = {};
  let key = 0;
  const push = (chunk: string) => {
    if (!chunk) return;
    const plain = style.fg == null && !style.bold && !style.dim && !style.italic && !style.underline;
    if (plain) { nodes.push(chunk); return; }
    const classes = [typeof style.fg === "number" ? styles[`c${style.fg}`] : "", style.bold ? styles.bold : "", style.dim ? styles.dim : "",
      style.italic ? styles.italic : "", style.underline ? styles.underline : ""].filter(Boolean).join(" ");
    nodes.push(<span key={key++} className={classes} style={typeof style.fg === "string" ? { color: style.fg } : undefined}>{chunk}</span>);
  };
  const text = lines.join("\n");
  let last = 0;
  for (const match of text.matchAll(SGR)) {
    push(text.slice(last, match.index));
    style = applySgr(style, (match[1] || "0").split(";").map(Number));
    last = match.index! + match[0].length;
  }
  push(text.slice(last));
  return nodes;
}

/** The program's output, following new lines while the reader is at the end. */
export function ProgramOutput({ output, live, empty }: { output: string; live: boolean; empty: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const atEnd = useRef(true);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onScroll = () => { atEnd.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24; };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => node.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const node = ref.current;
    if (node && atEnd.current) node.scrollTop = node.scrollHeight;
  }, [output]);
  return <pre ref={ref} className={styles.terminal} data-program-output="">
    {output ? renderTerminal(output.replace(/\n$/, "")) : <span className={styles.placeholder}>{empty}</span>}
    {live && <span className={styles.cursor} aria-hidden="true" />}
  </pre>;
}
