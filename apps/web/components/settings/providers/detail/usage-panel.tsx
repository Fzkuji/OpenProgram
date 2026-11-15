"use client";

import { useEffect, useMemo, useState } from "react";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { ProviderStatus, ProviderUsage } from "@/lib/settings/provider-status.generated";

import { StackedBarChart, type StackPoint } from "../../token-usage/stacked-bar-chart";
import { barTone, baseKey, bindingLimit, fmtAmount, fmtCount, fmtPercent, fmtReset, fmtUsd, leftFraction, planLabel } from "../status/model";
import { cx } from "../status/parts";
import styles from "../status/status.module.css";

type Days = 7 | 30 | 90;
type Measure = "cost" | "tokens";

const TOP = "var(--meter-fill)";
const OTHER = "color-mix(in srgb, var(--meter-fill) 35%, transparent)";

/** The usage panel of a provider's page (design §5): 7, 30 or 90 days by
 *  cost or tokens; spent, tokens, cache read and the limit that runs out
 *  first; a daily chart of the top model over the others, with the daily
 *  pace a key limit or balance allows. */
export function UsagePanel({ providerId, status }: { providerId: string; status: ProviderStatus | null }) {
  const { text } = useTranslation();
  const [days, setDays] = useState<Days>(30);
  const included = !!status?.spend.included;
  // Subscriptions read in tokens; the status may arrive after the panel, so
  // the measure follows it until a measure is picked by hand.
  const [picked, setPicked] = useState<Measure | null>(null);
  const measure: Measure = picked ?? (included ? "tokens" : "cost");
  const setMeasure = (m: Measure) => setPicked(m);
  const [usage, setUsage] = useState<ProviderUsage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    fetch(`/api/providers/${encodeURIComponent(providerId)}/usage?days=${days}&group=day,model`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: ProviderUsage) => { if (alive) setUsage(d); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [providerId, days]);

  const byCost = measure === "cost";
  const top = useMemo(() => {
    const models = [...(usage?.models ?? [])];
    models.sort((a, b) => (byCost ? b.list_price_usd - a.list_price_usd : b.total_tokens - a.total_tokens));
    return models[0]?.model_id ?? null;
  }, [usage, byCost]);

  const points = useMemo<StackPoint[]>(() => (usage?.series ?? []).map((d) => {
    const total = byCost ? d.list_price_usd : d.tokens;
    const own = top ? (byCost ? d.models[top]?.list_price_usd : d.models[top]?.tokens) ?? 0 : 0;
    return { ts: d.ts, values: [own, Math.max(0, total - own)] };
  }), [usage, byCost, top]);

  const now = Date.now() / 1000;
  const limit = status ? bindingLimit(status) : null;
  const money = limit && limit.unit.toLowerCase() === "usd" && ["balance", "key_limit", "credits"].includes(baseKey(limit));
  const pace = byCost && money && limit
    ? ((limit.limit ?? limit.remaining ?? 0) / days) || null
    : null;

  const totals = usage?.totals;
  const spent = !totals ? "—" : totals.included ? text("Included", "套餐内") : fmtUsd(totals.cost_usd ?? totals.list_price_usd);
  const plan = planLabel(status?.plan);
  const spentTip = !totals ? "" : totals.included
    ? (plan ? text(`${plan} plan · `, `${plan} 套餐 · `) : "")
      + text(`${fmtUsd(totals.list_price_usd)} at API prices`, `按 API 价约 ${fmtUsd(totals.list_price_usd)}`)
    : totals.unpriced_calls ? text(`${totals.unpriced_calls} calls unpriced`, `${totals.unpriced_calls} 次调用无价格`) : "";

  let limitValue = "—";
  let limitLabel = text("No quota data", "没有额度数据");
  let limitTone = "normal";
  if (limit) {
    const reset = fmtReset(limit.resets_at, now);
    limitTone = barTone(limit);
    if (limit.unit === "percent") {
      limitValue = fmtPercent(leftFraction(limit));
      limitLabel = text(`${limit.label} left`, `${limit.label} 剩余`) + (reset ? ` · ${reset}` : "");
    } else if (baseKey(limit) === "balance" && limit.limit == null) {
      limitValue = fmtAmount(limit.remaining, limit.unit);
      limitLabel = text("Balance", "余额");
    } else if (limit.unit === "requests" || limit.unit === "tokens") {
      limitValue = fmtCount(limit.remaining);
      limitLabel = text(`${limit.label} left`, `${limit.label} 剩余`) + (reset ? ` · ${reset}` : "");
    } else {
      limitValue = fmtAmount(limit.remaining, limit.unit);
      limitLabel = text(`${limit.label} left`, `${limit.label} 剩余`) + (reset ? ` · ${reset}` : "");
    }
  }

  // A subscription's cost is covered by the plan: its bars keep their shape
  // but the axis prints no dollars, and a day's list price shows only on hover.
  const subscription = included || !!totals?.included;
  const format = !byCost ? (v: number) => fmtCount(v)
    : subscription ? () => ""
    : (v: number) => fmtUsd(v).replace(/\.00$/, "");
  const formatTip = byCost && subscription
    ? (v: number) => text(`Included · ${fmtUsd(v)} at API prices`, `套餐内 · 按 API 价约 ${fmtUsd(v)}`)
    : undefined;
  return (
    <section className={styles.panel} aria-label={text("Usage", "用量")}>
      <div className={styles.panelHead}>
        <span>{text("Usage", "用量")}</span>
        <span className="flex-1" />
        <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v) as Days)}>
          <TabsList aria-label={text("Period", "时段")}>
            <TabsTrigger value="7" className="px-2">7d</TabsTrigger>
            <TabsTrigger value="30" className="px-2">30d</TabsTrigger>
            <TabsTrigger value="90" className="px-2">90d</TabsTrigger>
          </TabsList>
        </Tabs>
        <Tabs value={measure} onValueChange={(v) => setMeasure(v as Measure)}>
          <TabsList aria-label={text("Measure", "度量")}>
            <TabsTrigger value="cost" className="px-2">{text("Cost", "费用")}</TabsTrigger>
            <TabsTrigger value="tokens" className="px-2">{text("Tokens", "Token")}</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className={`${styles.stats} grid grid-cols-4 gap-[8px]`}>
        <div>
          {spentTip ? <HoverTip label={spentTip}><b tabIndex={0}>{spent}</b></HoverTip> : <b>{spent}</b>}
          <span>{text("Spent", "花费")}</span>
        </div>
        <div><b>{totals ? fmtCount(totals.total_tokens) : "—"}</b><span>{text("Tokens", "Token")}</span></div>
        <div><b>{totals?.cache_share != null ? fmtPercent(totals.cache_share) : "—"}</b><span>{text("Cache read", "缓存命中")}</span></div>
        <div data-limit={limit?.limit_key ?? ""}>
          <b className={cx(limitTone !== "normal" && styles[limitTone])}>{limitValue}</b>
          <span>{limitLabel}</span>
        </div>
      </div>
      {failed ? (
        <div className={cx(styles.xs, styles.muted)} role="alert">{text("Couldn't read the usage", "无法读取用量")}</div>
      ) : (
        <StackedBarChart points={points} colors={[TOP, OTHER]} format={format} formatTip={formatTip} height={150} line={pace} />
      )}
      <div className={styles.legend}>
        {top ? <span><i className={styles.legendSwatch} style={{ background: TOP }} />{top}</span> : null}
        <span><i className={styles.legendSwatch} style={{ background: OTHER }} />{text("Other models", "其他模型")}</span>
        {pace ? <span><i className={styles.legendPace} />{text("Daily pace for the limit", "额度对应的每日用量")}</span> : null}
      </div>
    </section>
  );
}
