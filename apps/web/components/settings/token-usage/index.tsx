"use client";

import { useEffect, useMemo, useState } from "react";

import { SettingsPage } from "../settings-ui";
import local from "./usage.module.css";
import { cachedFetch } from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";
import { ChartNoAxesColumn } from "lucide-react";
import { ManageEmptyState, ManageSubnav } from "@/components/ui/manage-page";
import { HoverTip } from "@/components/ui/tooltip";
import { StackedBarChart, type StackPoint } from "./stacked-bar-chart";

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
  /** A subscription's calls: the cost is the API list price, not billed. */
  included?: boolean;
  unpriced_events?: number;
};

/** What a source or function row's calls are billed (newer Workers). */
type CostSplit = {
  billed_cost?: number;
  included_cost?: number;
  unpriced_events?: number;
};

type KindRow = CostSplit & {
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

type LabelRow = CostSplit & {
  label: string;
  input_tokens: number;
  output_tokens: number;
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
    cost_known: boolean;
    unknown_cost_events: number;
    events: number;
    /** What pay-per-token providers charge (priced calls only). */
    billed_cost?: number;
    /** API list price of calls a subscription already covers. */
    included_cost?: number;
    /** Pay-per-token calls with no known price. */
    unpriced_events?: number;
  };
  by_model: ModelRow[];
  by_kind: KindRow[];
  by_label: LabelRow[];
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
  "#e8537a", "#3b82f6", "#22c55e", "#f59e0b", "#a855f7",
  "#06b6d4", "#ef4444", "#84cc16", "#ec4899", "#14b8a6",
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

/** A source or function row's cost as it is billed: "Included" when a
 *  subscription covers every call, else the billed sum (with unpriced calls),
 *  the list price of the covered calls on hover. Older Workers: the raw sum. */
function CostCell({ row, text }: { row: CostSplit & { cost: number }; text: (en: string, zh: string) => string }) {
  if (row.billed_cost == null) return <>{fmtCost(row.cost)}</>;
  const included = row.included_cost ?? 0;
  const unpriced = row.unpriced_events ?? 0;
  const tip = included > 0 ? text(`Subscriptions cover ${fmtCost(included)} at API prices`, `订阅已覆盖按 API 价约 ${fmtCost(included)}`) : "";
  const value = included > 0 && row.billed_cost === 0 && unpriced === 0
    ? text("Included", "套餐内")
    : fmtCost(row.billed_cost) + (unpriced ? ` · ${text(`${unpriced} unpriced`, `${unpriced} 次无价格`)}` : "");
  return tip ? <HoverTip label={tip}><span tabIndex={0}>{value}</span></HoverTip> : <>{value}</>;
}

// ── dimension tabs ──────────────────────────────────────────────────────────

type Dimension = "call_kind" | "model_id" | "call_label";
const DIMENSIONS: { key: Dimension; labelKey: "usage.dim.kind" | "usage.dim.model" | "usage.dim.label" }[] = [
  { key: "call_kind", labelKey: "usage.dim.kind" },
  { key: "model_id", labelKey: "usage.dim.model" },
  { key: "call_label", labelKey: "usage.dim.label" },
];

// ── Legend ───────────────────────────────────────────────────────────────────

function Legend({ categories }: { categories: string[] }) {
  return (
    <div className={local.legend}>
      {categories.map((cat, i) => (
        <span key={cat} className="inline-flex items-center gap-[5px]">
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
          <div key={r.name} className="contents">
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

// ── cost ────────────────────────────────────────────────────────────────────

type Card = { label: string; value: string; note?: string; tip?: string };

/** Est. cost as billed: the priced pay-per-token sum, how many such calls
 *  have no price, and (in a tooltip) what subscriptions cover at list price.
 *  One unpriced call never hides the total. Older Workers send only
 *  `cost` / `cost_known`; then an unknown cost still reads "Unknown". */
function costCard(totals: Summary["totals"], label: string, text: (en: string, zh: string) => string): Card {
  if (totals.billed_cost == null) {
    return { label, value: totals.cost_known ? fmtCost(totals.cost) : text("Unknown", "未知") };
  }
  const unpriced = totals.unpriced_events ?? 0;
  const included = totals.included_cost ?? 0;
  return {
    label,
    value: fmtCost(totals.billed_cost),
    note: unpriced > 0 ? text(`${unpriced} calls unpriced`, `${unpriced} 次调用无价格`) : undefined,
    tip: included > 0 ? text(`Subscriptions cover ${fmtCost(included)} at API prices`, `订阅已覆盖按 API 价约 ${fmtCost(included)}`) : undefined,
  };
}

// ── main component ───────────────────────────────────────────────────────────


const USAGE_DAYS = 30;
const DAY_SECONDS = 86_400;

/** Everything on this page covers the same 30 days. The window starts on a
 *  day boundary so the request stays cacheable, and matches the chart's. */
function usageRequests() {
  const today = Math.floor(Date.now() / 1000 / DAY_SECONDS);
  const since = (today - USAGE_DAYS + 1) * DAY_SECONDS;
  const trend = (group: string) =>
    cachedFetch<GroupedTrendResp>(`/api/usage/trend?bucket=day&days=${USAGE_DAYS}&group=${group}`);
  return Promise.all([
    cachedFetch<Summary>(`/api/usage/summary?since=${since}`),
    trend("call_kind"),
    trend("model_id"),
    trend("call_label"),
  ]);
}

/** Start loading the page when Settings opens, so it appears already filled. */
export function prefetchUsagePage(): void {
  void usageRequests().catch(() => {});
}

export function TokenUsageSection() {
  const { t, text } = useTranslation();
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
        const [s, tKind, tModel, tLabel] = await usageRequests();
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

  const cards = useMemo<Card[]>(
    () =>
      totals
        ? [
            { label: t("usage.card.input"), value: fmtNum(totals.input_tokens) },
            { label: t("usage.card.output"), value: fmtNum(totals.output_tokens) },
            { label: t("usage.card.cache"), value: fmtNum(totals.cache_read_tokens) },
            { label: t("usage.card.total"), value: fmtNum(totals.total_tokens) },
            costCard(totals, t("usage.card.cost"), text),
            { label: t("usage.card.calls"), value: fmtNum(totals.events) },
          ]
        : [],
    [totals, t, text]
  );

  // current dimension's data
  const curTrend = trendData[dim];
  const series = useMemo(() => curTrend?.series ?? {}, [curTrend]);
  const categories = useMemo(() => Object.keys(series), [series]);
  const points = useMemo<StackPoint[]>(() => {
    const days = Object.values(series)[0]?.length ?? 0;
    return Array.from({ length: days }, (_, d) => ({
      ts: series[categories[0]]?.[d]?.ts ?? 0,
      values: categories.map((cat) => series[cat]?.[d]?.total_tokens ?? 0),
    }));
  }, [series, categories]);

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
    if (dim === "call_label") {
      // Raw label (even empty) so the name matches the trend series key the
      // bar colours are looked up by; CategoryBars shows an empty name as "—".
      return summary.by_label.map((r) => ({
        name: r.label,
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
    <SettingsPage title={t("settings.tab.usage")} description={t("usage.desc")} bodyClassName={local.usagePage}>
        {loading && <div className={local.muted}>{t("usage.loading")}</div>}
        {error && <div className={local.muted}>{t("usage.error")}</div>}
        {!loading && !error && totals && totals.events === 0 && (
          <ManageEmptyState
            compact
            icon={<ChartNoAxesColumn />}
            title={text("No usage recorded yet", "暂无用量记录")}
            description={t("usage.empty")}
          />
        )}
        {!loading && !error && totals && totals.events > 0 && (
          <>
            {/* stat cards */}
            <h3 className={local.sectionTitle}>{text("Last 30 days", "最近 30 天")}</h3>
            <div className="grid grid-cols-[repeat(auto-fit,_minmax(120px,_1fr))] gap-[12px]">
              {cards.map((c: Card) => (
                <div key={c.label} className={local.card}>
                  {c.tip ? (
                    <HoverTip label={c.tip}><div className={local.cardValue} tabIndex={0}>{c.value}</div></HoverTip>
                  ) : <div className={local.cardValue}>{c.value}</div>}
                  <div className={local.cardLabel}>{c.label}{c.note ? <span className={local.cardNote}> · {c.note}</span> : null}</div>
                </div>
              ))}
            </div>

            {/* dimension tabs — the shared subnav (tablist + arrow keys) */}
            <ManageSubnav
              className={local.dimSubnav}
              tabs={DIMENSIONS.map((d) => ({ id: d.key, label: t(d.labelKey) }))}
              activeTab={dim}
              onTabChange={(id) => setDim(id as Dimension)}
              ariaLabel={text("Usage breakdown", "用量维度")}
              panelId="usage-breakdown"
            />

            {/* stacked bar chart + legend */}
            <div
              className={local.section}
              id="usage-breakdown"
              role="tabpanel"
              aria-labelledby={`usage-breakdown-tab-${dim}`}
            >
              <h3 className={local.sectionTitle}>{t("usage.trend")}</h3>
              <StackedBarChart
                points={points}
                colors={categories.map((_, i) => catColor(i))}
                format={(v) => fmtNum(Math.round(v))}
                formatTip={(v) => `${fmtNum(v)} tok`}
              />
              <Legend categories={categories} />
            </div>

            {/* horizontal breakdown bars */}
            {barRows.length > 0 && (
              <div className={local.section}>
                <CategoryBars rows={barRows} categories={categories} />
              </div>
            )}

            {/* By source table */}
            {(summary?.by_kind ?? []).length > 0 && (
              <div className={local.section}>
                <h3 className={local.sectionTitle}>{t("usage.byKind")}</h3>
                <div className="overflow-x-auto">
                  <table className={local.table}>
                    <thead>
                      <tr>
                        <th>{t("usage.col.source")}</th>
                        <th className={local.num}>{t("usage.col.input")}</th>
                        <th className={local.num}>{t("usage.col.output")}</th>
                        <th className={local.num}>{t("usage.col.calls")}</th>
                        <th className={local.num}>{t("usage.col.cost")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary!.by_kind.map((r) => (
                        <tr key={r.kind}>
                          <td className={local.modelCell}>{r.kind}</td>
                          <td className={local.num}>{fmtNum(r.input_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.output_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.events)}</td>
                          <td className={local.num}><CostCell row={r} text={text} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* By model table */}
            {(summary?.by_model ?? []).length > 0 && (
              <div className={local.section}>
                <h3 className={local.sectionTitle}>{t("usage.byModel")}</h3>
                <div className="overflow-x-auto">
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
                          <td className={local.num}>
                            {r.included ? (
                              <HoverTip label={text(`${fmtCost(r.cost)} at API prices`, `按 API 价约 ${fmtCost(r.cost)}`)}>
                                <span tabIndex={0}>{text("Included", "套餐内")}</span>
                              </HoverTip>
                            ) : r.unpriced_events ? `${fmtCost(r.cost)} · ${text(`${r.unpriced_events} unpriced`, `${r.unpriced_events} 次无价格`)}` : fmtCost(r.cost)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* By function table */}
            {(summary?.by_label ?? []).length > 0 && (
              <div className={local.section}>
                <h3 className={local.sectionTitle}>{t("usage.dim.label")}</h3>
                <div className="overflow-x-auto">
                  <table className={local.table}>
                    <thead>
                      <tr>
                        <th>{t("usage.col.function")}</th>
                        <th className={local.num}>{t("usage.col.input")}</th>
                        <th className={local.num}>{t("usage.col.output")}</th>
                        <th className={local.num}>{t("usage.col.calls")}</th>
                        <th className={local.num}>{t("usage.col.cost")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary!.by_label.map((r) => (
                        <tr key={r.label || "—"}>
                          <td className={local.modelCell}>{r.label || "—"}</td>
                          <td className={local.num}>{fmtNum(r.input_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.output_tokens)}</td>
                          <td className={local.num}>{fmtNum(r.events)}</td>
                          <td className={local.num}><CostCell row={r} text={text} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
    </SettingsPage>
  );
}
