"use client";

import { Fragment, memo } from "react";
import { KeyRound, Plus, Repeat, Server } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { FixAction, ProviderStatus } from "@/lib/settings/provider-status.generated";
import { cn } from "@/lib/utils";

import { countsKeys, credentialCount } from "../detail/credential-model";
import { providerLogo, ProviderIcon } from "../icons/provider-icon";
import { bestHealth, billingLabel, dimmed, sparkValues, spendView, wayMeta, wayTitle, sharesKey, type CompanyGroup } from "./model";
import { HealthChip, QuotaBlock, Sparkline } from "./parts";
import styles from "./status.module.css";

type Handlers = {
  now: number;
  /** Open the provider's page; a fix names the action that brought it there. */
  onOpen: (id: string, fix?: FixAction) => void;
  onConnect: (id: string) => void;
  onAddCredential: (id: string) => void;
};

/** The 30-day sparkline, spend (or tokens) and cache share; nothing before the first call. */
function SpendStats({ p }: { p: ProviderStatus }) {
  const { text } = useTranslation();
  if (!(p.spend.calls > 0 || p.spend.total_tokens > 0)) return null;
  const spend = spendView(p.spend, text, p.plan);
  const spendText = <span className={styles.num}><b>{spend.value}</b> {spend.suffix}</span>;
  return (
    <div className={styles.cardStats}>
      <Sparkline values={sparkValues(p.spend.series, spend.byCost)} dim={dimmed(p.health.state)} />
      {spend.tip ? <HoverTip label={spend.tip}>{spendText}</HoverTip> : spendText}
      {p.spend.cache_share != null ? (
        <span className={styles.num}>{text("Cache", "缓存")} {Math.round(p.spend.cache_share * 100)}%</span>
      ) : null}
    </div>
  );
}

/** How many credentials and in what state ("3 keys · 1 cooling · 1 invalid"),
 *  each part whole, a long count breaking between parts. */
function CredentialCount({ p, now }: { p: ProviderStatus; now: number }) {
  const { text } = useTranslation();
  return (
    <span className="min-w-0 flex-1">
      {credentialCount(p.keys, p.takes_keys, now, text).map((part, i) => (
        <Fragment key={i}>
          {i > 0 ? <span className="text-text-secondary"> · </span> : null}
          <span className={cn("whitespace-nowrap", part.tone === "warning" && "text-(--warning-soft)", part.tone === "danger" && "text-(--danger-soft)")}>{part.label}</span>
        </Fragment>
      ))}
    </span>
  );
}

/** Add key (Add account for a provider signed in to), filled when it has none. */
function AddCredential({ p, onAdd }: { p: ProviderStatus; onAdd: (id: string) => void }) {
  const { text } = useTranslation();
  if (p.billing === "local") return null;
  return (
    <Button variant={p.keys.length === 0 ? "default" : "elevated"} size="xs" onClick={(e) => { e.stopPropagation(); onAdd(p.id); }}>
      <Plus data-icon="inline-start" aria-hidden="true" />
      {countsKeys(p.keys, p.takes_keys) ? text("Add key", "添加密钥") : text("Add account", "添加账号")}
    </Button>
  );
}

/** One connected provider as a card (design §2, A): logo, name over its
 *  default model, health; one quota block; the 30-day sparkline, spend and
 *  cache share; and at the foot, pinned to the card's bottom, its billing
 *  kind, how many credentials it has and in what state ("3 keys · 1 cooling
 *  · 1 invalid"), with Add key (Add account for a provider signed in to),
 *  filled when it has none. The whole card opens the provider's page. */
export const ProviderCard = memo(function ProviderCard({
  p, now, onOpen, onConnect, onAddCredential,
}: { p: ProviderStatus } & Handlers) {
  const { text } = useTranslation();
  const open = () => onOpen(p.id);
  const keys = countsKeys(p.keys, p.takes_keys);
  const none = p.keys.length === 0;
  const subtitle = p.default_model ?? (none ? (keys ? text("No key yet", "还没有密钥") : text("Not signed in", "还没登录")) : "");
  return (
    <Card size="sm" className={styles.card} data-provider={p.id} data-health={p.health.state} onClick={open}>
      <CardHeader className="grid-cols-[auto_minmax(0,1fr)_auto] has-data-[slot=card-action]:grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-[10px]">
        <ProviderIcon id={p.id} size={28} />
        <div className="flex min-w-0 flex-col">
          <CardTitle className="min-w-0">
            <Button variant="link" className="h-auto max-w-full justify-start p-0 text-fs-base font-semibold text-text-bright" onClick={(e) => { e.stopPropagation(); open(); }}>
              <span className="min-w-0 truncate">{p.label}</span>
            </Button>
          </CardTitle>
          {subtitle ? <CardDescription className="truncate text-fs-xs text-text-secondary">{subtitle}</CardDescription> : null}
        </div>
        <CardAction className="col-start-3 row-span-1 self-center"><HealthChip health={p.health} /></CardAction>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-[10px]">
        <QuotaBlock p={p} now={now} onConnect={() => onConnect(p.id)} onOpen={(fix) => onOpen(p.id, fix)} />
        <SpendStats p={p} />
        <div className={styles.cardFoot} data-credentials>
          <Badge variant="outline" data-plan={p.plan ?? undefined}>{billingLabel(p.billing, text, p.plan)}</Badge>
          <CredentialCount p={p} now={now} />
          <AddCredential p={p} onAdd={onAddCredential} />
        </div>
      </CardContent>
    </Card>
  );
});

/** One way of a company's card: its icon (a plan, a key, a local server),
 *  title over plan, connection and model, its own health; then its quota,
 *  spend and credentials with their add button. It opens that provider's page. */
function WaySection({ p, shared, now, onOpen, onConnect, onAddCredential }: { p: ProviderStatus; shared: boolean } & Handlers) {
  const { text } = useTranslation();
  const open = () => onOpen(p.id);
  const meta = wayMeta(p, { keys: countsKeys(p.keys, p.takes_keys), shared }, text);
  const Icon = p.billing === "subscription" ? Repeat : p.billing === "local" ? Server : KeyRound;
  return (
    <section className={styles.way} data-provider={p.id} data-health={p.health.state} onClick={open}>
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-[8px]">
        <span className={styles.wayIcon} aria-hidden="true"><Icon /></span>
        <div className="flex min-w-0 flex-col">
          <Button variant="link" size="sm" className="h-auto max-w-full justify-start p-0 leading-[18px] font-medium text-text-bright" onClick={(e) => { e.stopPropagation(); open(); }}>
            <span className="min-w-0 truncate">{wayTitle(p, text)}</span>
          </Button>
          {meta ? <span className="truncate text-fs-xs leading-4 text-text-secondary">{meta}</span> : null}
        </div>
        <HealthChip health={p.health} />
      </div>
      <QuotaBlock p={p} now={now} onConnect={() => onConnect(p.id)} onOpen={(fix) => onOpen(p.id, fix)} />
      <SpendStats p={p} />
      <div className={styles.wayFoot} data-credentials>
        <CredentialCount p={p} now={now} />
        <AddCredential p={p} onAdd={onAddCredential} />
      </div>
    </section>
  );
}

/** The provider whose logo stands for a company: its first way that has one. */
export function companyIcon(group: CompanyGroup): string {
  return group.ways.find((p) => providerLogo(p.id))?.id ?? group.family;
}

/** A company connected in more than one way (design §2): its logo and name
 *  over "N ways to connect", the best of its ways' health, and a section per
 *  way. A company connected one way only is a ProviderCard. */
export const CompanyCard = memo(function CompanyCard({ group, ...handlers }: { group: CompanyGroup } & Handlers) {
  const { text } = useTranslation();
  const n = group.ways.length;
  return (
    <Card size="sm" className={cn(styles.card, styles.companyCard)} data-family={group.family}>
      <CardHeader className="grid-cols-[auto_minmax(0,1fr)_auto] has-data-[slot=card-action]:grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-[10px]">
        <ProviderIcon id={companyIcon(group)} size={28} />
        <div className="flex min-w-0 flex-col">
          <CardTitle className="min-w-0 truncate font-semibold text-text-bright">{group.label}</CardTitle>
          <CardDescription className="truncate text-fs-xs text-text-secondary">{text(`${n} ways to connect`, `${n} 种接入方式`)}</CardDescription>
        </div>
        <CardAction className="col-start-3 row-span-1 self-center"><HealthChip health={bestHealth(group.ways.map((p) => p.health))} /></CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-[10px] px-0">
        {group.ways.map((p, i) => (
          <WaySection key={p.id} p={p} shared={sharesKey(p, group.ways.slice(0, i))} {...handlers} />
        ))}
      </CardContent>
    </Card>
  );
});
