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

// ── TrendChart: area chart drawn in real pixels (no viewBox stretch) ─────────
//
// We measure the container width with a ResizeObserver and draw the SVG in
// 1:1 pixel coordinates. Stretching a fixed viewBox with
// preserveAspectRatio="none" would warp the stroke, the dots and the axis
// text horizontally; pixel-space drawing keeps everything crisp at any width.

function TrendChart({ trend }: { trend: TrendPoint[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(0);

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

  const H = 160;
  const PAD = { t: 14, r: 14, b: 26, l: 14 };

  const { area, line, dots, ticks } = useMemo(() => {
    if (trend.length === 0 || w === 0)
      return { area: "", line: "", dots: [], ticks: [] };
    const max = Math.max(...trend.map((p) => p.total_tokens), 1);
    const innerW = Math.max(w - PAD.l - PAD.r, 1);
    const innerH = H - PAD.t - PAD.b;
    const xy = trend.map((p, i) => {
      const x = PAD.l + (i / Math.max(trend.length - 1, 1)) * innerW;
      const y = PAD.t + (1 - p.total_tokens / max) * innerH;
      return [x, y] as const;
    });
    const lineD = `M${xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" L")}`;
    const areaD =
      xy.length > 1
        ? `${lineD} L${xy[xy.length - 1][0].toFixed(1)},${H - PAD.b} L${xy[0][0].toFixed(1)},${H - PAD.b} Z`
        : "";
    // at most 5 evenly-spaced x-axis labels
    const tickCount = Math.min(trend.length, 5);
    const tickList = Array.from({ length: tickCount }, (_, i) => {
      const idx = Math.round((i / Math.max(tickCount - 1, 1)) * (trend.length - 1));
      return { x: xy[idx][0], label: fmtDate(trend[idx].ts) };
    });
    return {
      area: areaD,
      line: xy.length > 1 ? lineD : "",
      dots: xy,
      ticks: tickList,
    };
  }, [trend, w]);

  if (trend.length === 0) {
    return (
      <div className={local.chartWrap} ref={wrapRef}>
        <div className={local.chartEmpty}>暂无数据</div>
      </div>
    );
  }

  return (
    <div className={local.chartWrap} ref={wrapRef}>
      {w > 0 && (
        <svg width={w} height={H}>
          <defs>
            <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent-blue, #b8651f)" stopOpacity="0.30" />
              <stop offset="100%" stopColor="var(--accent-blue, #b8651f)" stopOpacity="0.04" />
            </linearGradient>
          </defs>
          {area && <path d={area} fill="url(#areaGrad)" />}
          {line && (
            <path d={line} fill="none" stroke="var(--accent-blue, #b8651f)" strokeWidth="2" />
          )}
          {dots.map(([x, y], i) => (
            <circle key={i} cx={x} cy={y} r="3" fill="var(--accent-blue, #b8651f)" />
          ))}
          {ticks.map((t, i) => (
            <text
              key={i}
              x={t.x}
              y={H - 6}
              textAnchor="middle"
              fontSize="11"
              fill="var(--text-secondary)"
            >
              {t.label}
            </text>
          ))}
        </svg>
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
          cachedFetch<TrendResp>("/api/usage/trend?bucket=day"),
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
