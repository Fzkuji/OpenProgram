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

type GroupedTrendResp = {
  bucket: string;
  days: number;
  group: string;
  series?: Record<string, TrendPoint[]>;
  trend?: TrendPoint[];
};

// ── palette ─────────────────────────────────────────────────────────────────

const PALETTE = [
  "#c76a25", "#3a8a5c", "#5b7ec2", "#b84d8a", "#8b6ec0",
  "#c9923e", "#4a9e9e", "#d05555", "#6b8e3a", "#7a6b5d",
];

function catColor(idx: number): string {
  return PALETTE[idx % PALETTE.length];
}

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

// ── dimension tabs ──────────────────────────────────────────────────────────

type Dimension = "call_kind" | "model_id" | "call_label";
const DIMENSIONS: { key: Dimension; labelKey: string }[] = [
  { key: "call_kind", labelKey: "usage.dim.kind" },
  { key: "model_id", labelKey: "usage.dim.model" },
  { key: "call_label", labelKey: "usage.dim.label" },
];

// ── StackedBarChart ─────────────────────────────────────────────────────────

function StackedBarChart({
  series,
  categories,
}: {
  series: Record<string, TrendPoint[]>;
  categories: string[];
}) {
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

  const H = 180;
  const PAD = { t: 14, r: 28, b: 26, l: 28 };

  const nDays = Object.values(series)[0]?.length ?? 0;

  const { stacks, ticks, maxTotal } = useMemo(() => {
    if (nDays === 0 || w === 0 || categories.length === 0)
      return { stacks: [], ticks: [], maxTotal: 1 };

    const innerW = Math.max(w - PAD.l - PAD.r, 1);
    const innerH = H - PAD.t - PAD.b;
    const slot = innerW / nDays;
    const gap = Math.min(slot * 0.25, 4);
    const barW = Math.max(slot - gap, 1);

    // compute per-day total for y-scale
    const dayTotals = Array.from({ length: nDays }, (_, d) =>
      categories.reduce((s, cat) => s + (series[cat]?.[d]?.total_tokens ?? 0), 0)
    );
    const mx = Math.max(...dayTotals, 1);

    const stackList = Array.from({ length: nDays }, (_, d) => {
      const x = PAD.l + d * slot + gap / 2;
      const ts = series[categories[0]]?.[d]?.ts ?? 0;
      let accH = 0;
      const segments = categories.map((cat, ci) => {
        const val = series[cat]?.[d]?.total_tokens ?? 0;
        const h = (val / mx) * innerH;
        const seg = {
          cat,
          color: catColor(ci),
          x,
          y: PAD.t + innerH - accH - h,
          w: barW,
          h: Math.max(h, val > 0 ? 1.5 : 0),
          val,
        };
        accH += h;
        return seg;
      });
      return { d, x, ts, segments, total: dayTotals[d] };
    });

    const tickCount = Math.min(nDays, 6);
    const tickList = Array.from({ length: tickCount }, (_, i) => {
      const idx = Math.round((i / Math.max(tickCount - 1, 1)) * (nDays - 1));
      return {
        x: PAD.l + idx * slot + slot / 2,
        label: fmtDate(stackList[idx]?.ts ?? 0),
      };
    });

    return { stacks: stackList, ticks: tickList, maxTotal: mx };
  }, [series, categories, w, nDays]);

  if (nDays === 0) {
    return (
      <div className={local.chartWrap} ref={wrapRef} style={{ height: H }}>
        <div className={local.chartEmpty}>暂无数据</div>
      </div>
    );
  }

  return (
    <div className={local.chartWrap} ref={wrapRef} style={{ height: H }}>
      {w > 0 && (
        <svg width={w} height={H}>
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
          {/* baseline for empty days */}
          {stacks
            .filter((st) => st.total === 0)
            .map((st) => (
              <rect
                key={`empty-${st.d}`}
                x={st.x}
                y={H - PAD.b - 1}
                width={st.segments[0]?.w ?? 2}
                height={1}
                fill="var(--border, #e5e0d8)"
              />
            ))}
          {ticks.map((t, i) => (
            <text
              key={i}
              x={t.x}
              y={H - 6}
              textAnchor={
                i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"
              }
              fontSize="11"
              fill="var(--text-secondary)"
            >
              {t.label}
            </text>
          ))}
        </svg>
      )}
      {hover !== null && stacks[hover] && (
        <div
          className={local.chartTip}
          style={{
            left: Math.min(
              Math.max(stacks[hover].x + (stacks[hover].segments[0]?.w ?? 0) / 2, 80),
              w - 80
            ),
          }}
        >
          <strong>{fmtDate(stacks[hover].ts)}</strong>{" "}
          {fmtNum(stacks[hover].total)} tok
        </div>
      )}
    </div>
  );
}

// ── Legend ───────────────────────────────────────────────────────────────────

function Legend({ categories }: { categories: string[] }) {
  return (
    <div className={local.legend}>
      {categories.map((cat, i) => (
        <span key={cat} className={local.legendItem}>
          <span
            className={local.legendDot}
            style={{ background: catColor(i) }}
          />
          {cat || "—"}
        </span>
      ))}
    </div>
  );
}

// ── CategoryBars (horizontal) with per-category colors ──────────────────────

function CategoryBars({
  rows,
  categories,
}: {
  rows: { name: string; total_tokens: number; cost: number; events: number }[];
  categories: string[];
}) {
  const max = Math.max(...rows.map((r) => r.total_tokens), 1);
  return (
    <div className={local.barsWrap}>
      {rows.map((r) => {
        const ci = categories.indexOf(r.name);
        return (
          <div key={r.name} className={local.barRow}>
            <span className={local.barLabel}>{r.name || "—"}</span>
            <div className={local.barTrack}>
              <div
                className={local.barFill}
                style={{
                  width: `${(r.total_tokens / max) * 100}%`,
                  background: ci >= 0 ? catColor(ci) : undefined,
                }}
              />
            </div>
            <span className={local.barValue}>{fmtNum(r.total_tokens)}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── main component ───────────────────────────────────────────────────────────

export function TokenUsageSection() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dim, setDim] = useState<Dimension>("call_kind");
  const [trendData, setTrendData] = useState<Record<string, GroupedTrendResp>>(
    {}
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // initial load: summary + all 3 grouped trends
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, tKind, tModel, tLabel] = await Promise.all([
          cachedFetch<Summary>("/api/usage/summary"),
          cachedFetch<GroupedTrendResp>(
            "/api/usage/trend?bucket=day&days=30&group=call_kind"
          ),
          cachedFetch<GroupedTrendResp>(
            "/api/usage/trend?bucket=day&days=30&group=model_id"
          ),
          cachedFetch<GroupedTrendResp>(
            "/api/usage/trend?bucket=day&days=30&group=call_label"
          ),
        ]);
        if (!alive) return;
        setSummary(s);
        setTrendData({
          call_kind: tKind,
          model_id: tModel,
          call_label: tLabel,
        });
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

  // current dimension's data
  const curTrend = trendData[dim];
  const series = curTrend?.series ?? {};
  const categories = useMemo(() => Object.keys(series), [series]);

  // bar rows for current dimension from summary
  const barRows = useMemo(() => {
    if (!summary) return [];
    if (dim === "model_id") {
      return summary.by_model.map((r) => ({
        name: r.model,
        input_tokens: r.input_tokens,
        output_tokens: r.output_tokens,
        total_tokens: r.total_tokens,
        cost: r.cost,
        events: r.events,
      }));
    }
    return summary.by_kind.map((r) => ({
      name: r.kind,
      input_tokens: r.input_tokens,
      output_tokens: r.output_tokens,
      total_tokens: r.total_tokens,
      cost: r.cost,
      events: r.events,
    }));
  }, [summary, dim]);

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

            {/* dimension tabs */}
            <div className={local.dimTabs}>
              {DIMENSIONS.map((d) => (
                <button
                  key={d.key}
                  className={`${local.dimTab} ${dim === d.key ? local.dimTabActive : ""}`}
                  onClick={() => setDim(d.key)}
                >
                  {t(d.labelKey as any)}
                </button>
              ))}
            </div>

            {/* stacked bar chart + legend */}
            <div className={local.section}>
              <StackedBarChart series={series} categories={categories} />
              <Legend categories={categories} />
            </div>

            {/* horizontal breakdown bars */}
            {barRows.length > 0 && (
              <div className={local.section}>
                <CategoryBars rows={barRows} categories={categories} />
              </div>
            )}

            {/* detail table — switches with dimension tab */}
            <div className={local.section}>
              {dim === "model_id" && (summary?.by_model ?? []).length > 0 && (
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
              )}
              {dim !== "model_id" && barRows.length > 0 && (
                <div className={local.tableWrap}>
                  <table className={local.table}>
                    <thead>
                      <tr>
                        <th>{dim === "call_kind" ? t("usage.col.source") : t("usage.col.function")}</th>
                        <th className={local.num}>{t("usage.col.input")}</th>
                        <th className={local.num}>{t("usage.col.output")}</th>
                        <th className={local.num}>{t("usage.col.calls")}</th>
                        <th className={local.num}>{t("usage.col.cost")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {barRows.map((r) => (
                        <tr key={r.name}>
                          <td className={local.modelCell}>{r.name || "—"}</td>
                          <td className={local.num}>{fmtNum(r.input_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.output_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.events)}</td>
                          <td className={local.num}>{fmtCost(r.cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {barRows.length === 0 && dim !== "model_id" && (
                <div className={local.muted}>{t("usage.empty")}</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
