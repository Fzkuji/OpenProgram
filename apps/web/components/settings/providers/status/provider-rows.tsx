"use client";

import { useTranslation } from "@/lib/i18n";
import type { ProviderStatus } from "@/lib/settings/provider-status.generated";
import { HoverTip } from "@/components/ui/tooltip";

import { ProviderIcon } from "../icons/provider-icon";
import { compactQuota, dimmed, fmtCount, healthLabel, planLabel, sparkValues, spendView } from "./model";
import { Bar, cx, FixButton, HealthChip, Sparkline } from "./parts";
import styles from "./status.module.css";

/** The same providers as compact rows (design §2, B): one table row each,
 *  with the same health, quota, spend and default model as a card. The
 *  columns are fixed and every cell ends in an ellipsis inside its column,
 *  with the whole text on hover, so the table never grows past the pane. */
export function ProviderRows({
  providers, now, onOpen, onConnect,
}: {
  providers: ProviderStatus[];
  now: number;
  onOpen: (id: string) => void;
  onConnect: (id: string) => void;
}) {
  const { text } = useTranslation();
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
            <th>{text("Default", "默认模型")}</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => {
            const q = compactQuota(p, now, text);
            const spend = spendView(p.spend, text, p.plan);
            // A subscription whose plan is known shows it as the billing tag
            // where the cost would be; its spend is in the plan either way.
            const plan = p.spend.included ? planLabel(p.plan) : "";
            const dim = dimmed(p.health.state);
            const open = () => onOpen(p.id);
            const cost = p.spend.included ? text("Included", "套餐内") : spend.byCost ? spend.value : "—";
            const quotaText = q.parts.map((part) => `${part.b}${part.t}`).join(" · ");
            return (
              <tr key={p.id} data-provider={p.id} data-health={p.health.state} className={cx(dim && styles.dimRow)} onClick={open}>
                <td>
                  <button type="button" className={styles.rowName} title={p.label} onClick={(e) => { e.stopPropagation(); open(); }}>
                    <ProviderIcon id={p.id} size={20} /><span className={styles.rowLabel}>{p.label}</span>
                  </button>
                </td>
                <td>
                  {p.health.detail ? <HealthChip health={p.health} /> : (
                    <HoverTip label={healthLabel(p.health, text)}><span className="block min-w-0"><HealthChip health={p.health} /></span></HoverTip>
                  )}
                </td>
                <td>
                  <div className={cx(`${styles.qcell} flex items-center gap-[10px] min-w-0`, q.tone !== "normal" && styles[q.tone], p.quota?.stale && styles.stale)} data-form={q.form}>
                    {!p.fix && q.left != null ? <Bar left={q.left} tone={q.tone} /> : null}
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
                    {p.fix ? <FixButton fix={p.fix} onConnect={() => onConnect(p.id)} onOpen={open} /> : null}
                  </div>
                </td>
                <td className={cx(styles.num, p.spend.included && styles.secondary)}>
                  <HoverTip label={spend.tip ?? cost}>
                    {plan ? <span className={styles.kind} data-plan={p.plan ?? undefined}>{plan}</span> : <span>{cost}</span>}
                  </HoverTip>
                </td>
                <td><Sparkline values={sparkValues(p.spend.series, spend.byCost)} dim={dim} width={56} /></td>
                <td className={cx(styles.right, styles.num, styles.fade, styles.wide)}>{fmtCount(p.spend.total_tokens)}</td>
                <td className={cx(styles.right, styles.num, styles.fade, styles.wide)}>
                  {p.spend.cache_share != null ? `${Math.round(p.spend.cache_share * 100)}%` : "—"}
                </td>
                <td>{p.default_model ? <HoverTip label={p.default_model}><span className={styles.modelChip}>{p.default_model}</span></HoverTip> : null}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
