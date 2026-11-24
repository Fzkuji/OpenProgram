"use client";

import { useTranslation } from "@/lib/i18n";
import type { FixAction, ProviderStatus } from "@/lib/settings/provider-status.generated";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";

import { ProviderIcon } from "../icons/provider-icon";
import { companyIcon } from "./provider-card";
import { bestHealth, chipTone, companyGroups, compactQuota, dimmed, fmtCount, healthLabel, planLabel, sparkValues, spendView, wayShort, wayTitle } from "./model";
import { Bar, cx, FixButton, HealthChip, Sparkline } from "./parts";
import styles from "./status.module.css";

/** The same providers as compact rows (design §2, B): one table row each,
 *  with the same health, quota, spend and default model as a card. A
 *  company connected in more than one way is a row of its own (logo, name,
 *  its best way's health) with its ways indented under it, each by its
 *  short name ("ChatGPT", "API") with the whole title on hover.
 *  The columns are fixed and every cell ends in an ellipsis inside its
 *  column, with the whole text on hover, so the table never grows past the pane. */
export function ProviderRows({
  providers, now, onOpen, onConnect,
}: {
  providers: ProviderStatus[];
  now: number;
  onOpen: (id: string, fix?: FixAction) => void;
  onConnect: (id: string) => void;
}) {
  const { text } = useTranslation();
  /** One provider's row; `way`: one way of a company, named by its title under the company's row. */
  const row = (p: ProviderStatus, way: boolean) => {
    const q = compactQuota(p, now, text);
    const spend = spendView(p.spend, text, p.plan);
    // A subscription whose plan is known shows it as the billing tag
    // where the cost would be; its spend is in the plan either way.
    const plan = p.spend.included ? planLabel(p.plan) : "";
    const dim = dimmed(p.health.state);
    const open = () => onOpen(p.id);
    const cost = p.spend.included ? text("Included", "套餐内") : spend.byCost ? spend.value : "—";
    const quotaText = q.parts.map((part) => `${part.b}${part.t}`).join(" · ");
    // A way starts where its company's name starts, after the logo.
    return (
      <tr key={p.id} data-provider={p.id} data-way={way ? "" : undefined} data-health={p.health.state} className={cx(dim && styles.dimRow)} onClick={open}>
        <td>
          {way ? (
            <Button variant="link" size="sm" title={wayTitle(p, text)} className="max-w-full justify-start pr-0 pl-[30px] text-fs-base font-medium text-text-bright" onClick={(e) => { e.stopPropagation(); open(); }}>
              <span className="min-w-0 truncate">{wayShort(p, text)}</span>
            </Button>
          ) : (
            <Button variant="link" size="sm" title={p.label} className="max-w-full justify-start gap-[10px] px-0 text-fs-base font-medium text-text-bright" onClick={(e) => { e.stopPropagation(); open(); }}>
              <ProviderIcon id={p.id} size={20} /><span className="min-w-0 truncate">{p.label}</span>
            </Button>
          )}
        </td>
        <td>
          {chipTone(p.health.state) === "ok" ? null : p.health.detail ? <HealthChip health={p.health} /> : (
            <HoverTip label={healthLabel(p.health, text)}><span className="block min-w-0"><HealthChip health={p.health} /></span></HoverTip>
          )}
        </td>
        <td>
          <div className={cx(`${styles.qcell} flex items-center gap-[10px] min-w-0`, q.tone !== "normal" && styles[q.tone], p.quota?.stale && styles.stale)} data-form={q.form}>
            {!p.fix && q.left != null ? <Bar left={q.left} tone={q.tone} className="w-[72px] flex-none" /> : null}
            {q.parts.length ? (
              <HoverTip label={quotaText}>
                <span className={cx(styles.qvalue, styles.num)}>
                  {q.parts.map((part, i) => (
                    <span key={i}>{i > 0 ? " · " : ""}{part.b ? <b>{part.b}</b> : null}{part.t}</span>
                  ))}
                </span>
              </HoverTip>
            ) : !p.fix && p.billing !== "local" ? (
              <span className={cx(styles.xs, styles.muted)}>{text("No quota data", "没有额度数据")}</span>
            ) : null}
            {p.fix ? <FixButton fix={p.fix} onConnect={() => onConnect(p.id)} onOpen={(fix) => onOpen(p.id, fix)} /> : null}
          </div>
        </td>
        <td className={cx(styles.num, p.spend.included && styles.secondary)}>
          <HoverTip label={spend.tip ?? cost}>
            {plan ? <Badge variant="outline" data-plan={p.plan ?? undefined}>{plan}</Badge> : <span>{cost}</span>}
          </HoverTip>
        </td>
        <td><Sparkline values={sparkValues(p.spend.series, spend.byCost)} dim={dim} width={56} /></td>
        <td className={cx(styles.right, styles.num, styles.fade, styles.wide)}>{fmtCount(p.spend.total_tokens)}</td>
        <td className={cx(styles.right, styles.num, styles.fade, styles.wide)}>
          {p.spend.cache_share != null ? `${Math.round(p.spend.cache_share * 100)}%` : "—"}
        </td>
        <td>{p.default_model ? <HoverTip label={p.default_model}><Badge variant="secondary" className="max-w-full font-mono"><span className="min-w-0 truncate">{p.default_model}</span></Badge></HoverTip> : null}</td>
      </tr>
    );
  };
  return (
    <div className="overflow-x-auto">
      <table className={styles.rows}>
        <thead>
          <tr>
            <th>{text("Provider", "服务商")}</th>
            <th className={styles.colStatus}>{text("Status", "状态")}</th>
            <th className={styles.colQuota}>{text("Quota", "额度")}</th>
            <th className={styles.colSpend}>{text("30 days", "30 天")}</th>
            <th className={styles.colTrend} aria-label={text("Trend", "走势")} />
            <th className={cx(styles.right, styles.wide, styles.colTokens)}>{text("Tokens", "Token")}</th>
            <th className={cx(styles.right, styles.wide, styles.colCache)}>{text("Cache", "缓存")}</th>
            <th className={styles.colDefault}>{text("Default", "默认模型")}</th>
          </tr>
        </thead>
        <tbody>
          {companyGroups(providers).flatMap((group) => group.ways.length === 1 ? [row(group.ways[0], false)] : [
            <tr key={`family:${group.family}`} data-family={group.family} className={styles.companyRow}>
              <td>
                <span className="flex min-w-0 items-center gap-[10px] text-fs-base font-medium text-text-bright">
                  <ProviderIcon id={companyIcon(group)} size={20} />
                  <span className="min-w-0 truncate">{group.label}</span>
                </span>
              </td>
              <td>{chipTone(bestHealth(group.ways.map((p) => p.health)).state) === "ok" ? null : <HealthChip health={bestHealth(group.ways.map((p) => p.health))} />}</td>
              {/* One empty cell per column, so the narrow pane hides the same ones. */}
              <td /><td /><td /><td className={styles.wide} /><td className={styles.wide} /><td />
            </tr>,
            ...group.ways.map((p) => row(p, true)),
          ])}
        </tbody>
      </table>
    </div>
  );
}
