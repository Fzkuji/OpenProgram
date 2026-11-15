"use client";

import { memo } from "react";

import { useTranslation } from "@/lib/i18n";
import type { ProviderStatus } from "@/lib/settings/provider-status.generated";

import { ProviderIcon } from "../icons/provider-icon";
import { billingLabel, dimmed, sparkValues, spendView } from "./model";
import { HealthChip, QuotaBlock, Sparkline } from "./parts";
import { HoverTip } from "@/components/ui/tooltip";
import styles from "./status.module.css";

/** One connected provider as a card (design §2, A): logo, name, billing
 *  kind and health; one quota block; a footer with the 30-day sparkline,
 *  spend, cache share and default model. The whole card opens the detail. */
export const ProviderCard = memo(function ProviderCard({
  p, now, onOpen, onConnect,
}: {
  p: ProviderStatus;
  now: number;
  onOpen: (id: string) => void;
  onConnect: (id: string) => void;
}) {
  const { text } = useTranslation();
  const spend = spendView(p.spend, text, p.plan);
  const open = () => onOpen(p.id);
  const spendText = (
    <span className={styles.num}><b>{spend.value}</b> {spend.suffix}</span>
  );
  return (
    <article className={styles.card} data-provider={p.id} data-health={p.health.state} onClick={open}>
      <div className="flex items-center gap-[10px] min-w-0">
        <ProviderIcon id={p.id} size={24} />
        <button type="button" className={styles.cardName} onClick={(e) => { e.stopPropagation(); open(); }}>{p.label}</button>
        <span className={styles.kind} data-plan={p.plan ?? undefined}>{billingLabel(p.billing, text, p.plan)}</span>
        <span className="flex-1" />
        <HealthChip health={p.health} />
      </div>
      <QuotaBlock p={p} now={now} onConnect={() => onConnect(p.id)} onOpen={open} />
      <div className={styles.cardFoot}>
        <Sparkline values={sparkValues(p.spend.series, spend.byCost)} dim={dimmed(p.health.state)} />
        {spend.tip ? <HoverTip label={spend.tip}>{spendText}</HoverTip> : spendText}
        {p.spend.cache_share != null ? (
          <span className={styles.num}>{text("Cache", "缓存")} {Math.round(p.spend.cache_share * 100)}%</span>
        ) : null}
        <span className="flex-1" />
        {p.default_model ? <span className={styles.modelChip}>{p.default_model}</span> : null}
      </div>
    </article>
  );
});
