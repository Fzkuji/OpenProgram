"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import styles from "../settings-page.module.css";
import local from "./usage.module.css";
import { cachedFetch } from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";

// ── API types ───────────────────────────────────────────────────────────────

type ModelRow = {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
  cost: number;
  events: number;
};

type KindRow = {
  kind: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number;
  events: number;
};

type TrendPoint = {
  ts: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  cost: number;
  events: number;
};

type Summary = {
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total_tokens: number;
    cost: number;
    events: number;
  };
  by_model: ModelRow[];
  by_kind: KindRow[];
};

type TrendResp = { bucket: string; trend: TrendPoint[] };

// ── formatters ──────────────────────────────────────────────────────────────

function fmtNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toLocaleString("en-US");
}

function fmtCost(c: number | null): string {
  if (c == null) return "—";
  if (c === 0) return "$0.00";
  if (c < 0.01) return "<$0.01";
  return "$" + c.toFixed(2);
}

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

// ── TrendChart: calendar-style daily bar chart ──────────────────────────────
//
// The backend returns a CONTIGUOUS window (last N days, zeros filled), so the
// chart always shows a full date axis — days with no usage are just empty
// slots, never collapsed away. Drawn in real pixels (ResizeObserver width) so
// nothing warps. Hovering a bar shows its date + total.

function TrendChart({ trend }: { trend: TrendPoint[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(0);
  const [hover, setHover] = useState<number | null>(null);

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

  const H = 168;
  const PAD = { t: 14, r: 28, b: 26, l: 28 };

  const { bars, ticks } = useMemo(() => {
    if (trend.length === 0 || w === 0) return { bars: [], ticks: [] };
    const mx = Math.max(...trend.map((p) => p.total_tokens), 1);
    const innerW = Math.max(w - PAD.l - PAD.r, 1);
    const innerH = H - PAD.t - PAD.b;
    const n = trend.length;
    const slot = innerW / n;
    const gap = Math.min(slot * 0.25, 4);
    const barW = Math.max(slot - gap, 1);
    const barList = trend.map((p, i) => {
      const h = (p.total_tokens / mx) * innerH;
      return {
        x: PAD.l + i * slot + gap / 2,
        y: PAD.t + (innerH - h),
        w: barW,
        h: Math.max(h, p.total_tokens > 0 ? 2 : 0), // min 2px so non-zero days show
        point: p,
      };
    });
    // up to 6 evenly-spaced date labels along the axis
    const tickCount = Math.min(n, 6);
    const tickList = Array.from({ length: tickCount }, (_, i) => {
      const idx = Math.round((i / Math.max(tickCount - 1, 1)) * (n - 1));
      return { x: PAD.l + idx * slot + slot / 2, label: fmtDate(trend[idx].ts) };
    });
    return { bars: barList, ticks: tickList };
  }, [trend, w]);

  if (trend.length === 0) {
    return (
      <div className={local.chartWrap} ref={wrapRef}>
        <div className={local.chartEmpty}>暂无数据</div>
      </div>
    );
  }

  return (
    <div className={local.chartWrap} ref={wrapRef} style={{ height: H }}>
      {w > 0 && (
        <svg width={w} height={H}>
          {bars.map((b, i) => (
            <rect
              key={i}
              x={b.x}
              y={b.h > 0 ? b.y : H - PAD.b - 1}
              width={b.w}
              height={b.h > 0 ? b.h : 1}
              rx={Math.min(b.w / 2, 2)}
              fill={
                b.h > 0
                  ? "var(--accent-blue, #b8651f)"
                  : "var(--border, #e5e0d8)"
              }
              opacity={hover === null || hover === i ? 1 : 0.5}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
          {ticks.map((t, i) => (
            <text
              key={i}
              x={t.x}
              y={H - 6}
              textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"}
              fontSize="11"
              fill="var(--text-secondary)"
            >
              {t.label}
            </text>
          ))}
        </svg>
      )}
      {hover !== null && bars[hover] && (
        <div
          className={local.chartTip}
          style={{
            left: Math.min(Math.max(bars[hover].x + bars[hover].w / 2, 60), w - 60),
          }}
        >
          <strong>{fmtDate(bars[hover].point.ts)}</strong>{" "}
          {fmtNum(bars[hover].point.total_tokens)} tok · {fmtCost(bars[hover].point.cost)}
        </div>
      )}
    </div>
  );
}

// ── KindBars: horizontal bar chart ──────────────────────────────────────────

function KindBars({ rows }: { rows: KindRow[] }) {
  const max = Math.max(...rows.map((r) => r.total_tokens), 1);
  return (
    <div className={local.barsWrap}>
      {rows.map((r) => (
        <div key={r.kind} className={local.barRow}>
          <span className={local.barLabel}>{r.kind}</span>
          <div className={local.barTrack}>
            <div
              className={local.barFill}
              style={{ width: `${(r.total_tokens / max) * 100}%` }}
            />
          </div>
          <span className={local.barValue}>{fmtNum(r.total_tokens)}</span>
        </div>
      ))}
    </div>
  );
}

// ── main component ───────────────────────────────────────────────────────────

export function TokenUsageSection() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, tr] = await Promise.all([
          cachedFetch<Summary>("/api/usage/summary"),
          cachedFetch<TrendResp>("/api/usage/trend?bucket=day&days=30"),
        ]);
        if (!alive) return;
        setSummary(s);
        setTrend(tr.trend ?? []);
      } catch {
        if (alive) setError(true);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const totals = summary?.totals;

  const cards = useMemo(
    () =>
      totals
        ? [
            { label: t("usage.card.input"), value: fmtNum(totals.input_tokens) },
            { label: t("usage.card.output"), value: fmtNum(totals.output_tokens) },
            { label: t("usage.card.cache"), value: fmtNum(totals.cache_read_tokens) },
            { label: t("usage.card.total"), value: fmtNum(totals.total_tokens) },
            { label: t("usage.card.cost"), value: fmtCost(totals.cost) },
            { label: t("usage.card.calls"), value: fmtNum(totals.events) },
          ]
        : [],
    [totals, t]
  );

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{t("settings.tab.usage")}</h2>
        <p className={styles.pageMeta}>{t("usage.desc")}</p>
      </div>
      <div className={styles.pageBody}>
        {loading && <div className={local.muted}>{t("usage.loading")}</div>}
        {error && <div className={local.muted}>{t("usage.error")}</div>}
        {!loading && !error && totals && (
          <>
            {/* stat cards */}
            <div className={local.cards}>
              {cards.map((c) => (
                <div key={c.label} className={local.card}>
                  <div className={local.cardValue}>{c.value}</div>
                  <div className={local.cardLabel}>{c.label}</div>
                </div>
              ))}
            </div>

            {/* daily trend sparkline */}
            <div className={local.section}>
              <h3 className={local.sectionTitle}>{t("usage.trend")}</h3>
              <TrendChart trend={trend} />
            </div>

            {/* by call_kind bars */}
            {(summary?.by_kind ?? []).length > 0 && (
              <div className={local.section}>
                <h3 className={local.sectionTitle}>{t("usage.byKind")}</h3>
                <KindBars rows={summary!.by_kind} />
              </div>
            )}

            {/* per-model table */}
            <div className={local.section}>
              <h3 className={local.sectionTitle}>{t("usage.byModel")}</h3>
              {(summary?.by_model ?? []).length > 0 ? (
                <div className={local.tableWrap}>
                  <table className={local.table}>
                    <thead>
                      <tr>
                        <th>{t("usage.col.model")}</th>
                        <th>{t("usage.col.provider")}</th>
                        <th className={local.num}>{t("usage.col.input")}</th>
                        <th className={local.num}>{t("usage.col.output")}</th>
                        <th className={local.num}>{t("usage.col.cacheRead")}</th>
                        <th className={local.num}>{t("usage.col.calls")}</th>
                        <th className={local.num}>{t("usage.col.cost")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary!.by_model.map((r) => (
                        <tr key={`${r.provider}:${r.model}`}>
                          <td className={local.modelCell}>{r.model}</td>
                          <td>{r.provider}</td>
                          <td className={local.num}>{fmtNum(r.input_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.output_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.cache_read_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.events)}</td>
                          <td className={local.num}>{fmtCost(r.cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className={local.muted}>{t("usage.empty")}</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
