"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import local from "./usage.module.css";

/** One day of the chart: its time and one value per stacked series. */
export type StackPoint = { ts: number; values: number[] };

const PAD = { t: 14, r: 16, b: 26, l: 52 };

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Daily stacked bars with a y scale, date ticks and a hover total.
 *  Token Usage draws one series per source/model/function; a provider's
 *  usage panel draws its top model over the others, with an optional
 *  dashed line (the daily pace a key limit or balance allows). */
export function StackedBarChart({
  points,
  colors,
  format,
  formatTip,
  height = 200,
  line = null,
}: {
  points: StackPoint[];
  colors: string[];
  /** A y-axis label. */
  format: (value: number) => string;
  /** The hover total; defaults to `format`. */
  formatTip?: (value: number) => string;
  height?: number;
  /** A horizontal reference line, in the values' unit. */
  line?: number | null;
}) {
  const { text } = useTranslation();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  const H = height;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      setW(entries[0].contentRect.width);
    });
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const nDays = points.length;

  const { stacks, ticks, yTicks, lineY } = useMemo(() => {
    if (nDays === 0 || w === 0) return { stacks: [], ticks: [], yTicks: [], lineY: null };

    const innerW = Math.max(w - PAD.l - PAD.r, 1);
    const innerH = H - PAD.t - PAD.b;
    const slot = innerW / nDays;
    const gap = Math.min(slot * 0.25, 4);
    const barW = Math.max(slot - gap, 1);

    const dayTotals = points.map((p) => p.values.reduce((s, v) => s + v, 0));
    const mx = Math.max(...dayTotals, line ?? 0, 1e-9);

    const stackList = points.map((p, d) => {
      const x = PAD.l + d * slot + gap / 2;
      let accH = 0;
      const segments = p.values.map((val, ci) => {
        const h = (val / mx) * innerH;
        const seg = { color: colors[ci % colors.length], x, y: PAD.t + innerH - accH - h, w: barW, h: Math.max(h, val > 0 ? 1.5 : 0), val };
        accH += h;
        return seg;
      });
      return { d, x, ts: p.ts, segments, total: dayTotals[d] };
    });

    const tickCount = Math.min(nDays, 6);
    const tickList = Array.from({ length: tickCount }, (_, i) => {
      const idx = Math.round((i / Math.max(tickCount - 1, 1)) * (nDays - 1));
      return { x: PAD.l + idx * slot + slot / 2, label: fmtDate(stackList[idx]?.ts ?? 0) };
    });

    const yTickCount = 4;
    const yTickList = Array.from({ length: yTickCount }, (_, i) => {
      const val = (mx / (yTickCount - 1)) * i;
      const y = PAD.t + innerH - (val / mx) * innerH;
      return { y, val, label: format(val) };
    });

    const ly = line != null && line > 0 ? PAD.t + innerH - (line / mx) * innerH : null;
    return { stacks: stackList, ticks: tickList, yTicks: yTickList, lineY: ly };
  }, [points, colors, format, line, w, nDays, H]);

  if (nDays === 0) {
    return (
      <div className={local.chartWrap} ref={wrapRef} style={{ height: H }}>
        <div className={local.chartEmpty}>{text("No usage recorded in this period", "此时段暂无用量记录")}</div>
      </div>
    );
  }

  const tip = formatTip ?? format;
  return (
    <div className={local.chartWrap} ref={wrapRef} style={{ height: H }}>
      {w > 0 && (
        <svg width={w} height={H}>
          {yTicks.map((yt, i) => (
            <g key={`y-${i}`}>
              {i > 0 && (
                <line x1={PAD.l} x2={w - PAD.r} y1={yt.y} y2={yt.y} stroke="var(--border)" strokeDasharray="3,3" strokeWidth="1" />
              )}
              <text x={PAD.l - 8} y={yt.y + 4} textAnchor="end" fontSize="10" fill="var(--text-secondary)">{yt.label}</text>
            </g>
          ))}
          {stacks.map((st) =>
            st.segments.map((seg, si) =>
              seg.h > 0 ? (
                <rect
                  key={`${st.d}-${si}`}
                  x={seg.x}
                  y={seg.y}
                  width={seg.w}
                  height={seg.h}
                  rx={Math.min(seg.w / 2, 2)}
                  fill={seg.color}
                  opacity={hover === null || hover === st.d ? 1 : 0.4}
                  onMouseEnter={() => setHover(st.d)}
                  onMouseLeave={() => setHover(null)}
                />
              ) : null
            )
          )}
          {stacks
            .filter((st) => st.total === 0)
            .map((st) => (
              <rect key={`empty-${st.d}`} x={st.x} y={H - PAD.b - 1} width={st.segments[0]?.w ?? 2} height={1} fill="var(--border)" />
            ))}
          {lineY != null && (
            <line x1={PAD.l} x2={w - PAD.r} y1={lineY} y2={lineY} stroke="var(--warning-soft)" strokeDasharray="3,3" strokeWidth="1.5" data-line="pace" />
          )}
          {ticks.map((t, i) => (
            <text key={i} x={t.x} y={H - 6} textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"} fontSize="11" fill="var(--text-secondary)">
              {t.label}
            </text>
          ))}
        </svg>
      )}
      {hover !== null && stacks[hover] && (
        <div
          className={local.chartTip}
          style={{ left: Math.min(Math.max(stacks[hover].x + (stacks[hover].segments[0]?.w ?? 0) / 2, 80), w - 80) }}
        >
          <strong>{fmtDate(stacks[hover].ts)}</strong>{" "}
          {tip(stacks[hover].total)}
        </div>
      )}
    </div>
  );
}
