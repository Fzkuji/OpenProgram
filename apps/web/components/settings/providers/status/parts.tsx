"use client";

/**
 * The pieces a provider card, a compact row and the detail page share: the
 * health chip, a quota bar row, the quota block in its four forms, the
 * 30-day sparkline and the inline fix button.
 */
import type { ReactNode } from "react";
import { CircleDashed, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { FixAction, FixWire, HealthWire, ProviderStatus } from "@/lib/settings/provider-status.generated";

import {
  chipTone,
  dimmed,
  fmtAgo,
  healthLabel,
  quotaForm,
  type QuotaRow,
  type Tone,
} from "./model";
import styles from "./status.module.css";

const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(" ");

const BADGE_OF = { ok: "success", warn: "warning", bad: "destructive", muted: "secondary" } as const;

/** A provider's or a key's health as a status Badge: a dot for a state
 *  that answers (ready, limited, refused), none for one not set up. */
export function HealthChip({ health, label }: { health: Pick<HealthWire, "state" | "until" | "detail">; label?: string }) {
  const { text } = useTranslation();
  const tone = chipTone(health.state);
  const chip = (
    <Badge variant={BADGE_OF[tone]} dot={tone !== "muted"} data-state={health.state} className="max-w-full">
      <span className="min-w-0 truncate">{label ?? healthLabel(health, text)}</span>
    </Badge>
  );
  return health.detail ? <HoverTip label={health.detail}>{chip}</HoverTip> : chip;
}

/** A quota bar: the kit's Progress on what is left. */
export function Bar({ left, tone, className }: { left: number; tone: Tone; className?: string }) {
  return <Progress value={left * 100} tone={tone} className={className ?? "min-w-10 flex-1"} />;
}

/** "5h [bar] 62% left · 14:30" — Codex's row, counting what is left. */
export function QuotaRowView({ row }: { row: QuotaRow }) {
  return (
    <>
      <div className={cx(`${styles.qrow} grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-[10px] min-h-(--ui-control-inline)`, row.tone !== "normal" && styles[row.tone])} data-limit={row.key}>
        <span className={styles.qlabel}>{row.label}</span>
        {row.left != null ? <Bar left={row.left} tone={row.tone} /> : <span />}
        <span className={cx(styles.qvalue, styles.num)}><b>{row.value}</b>{row.rest}</span>
      </div>
      {row.under ? <div className={cx(styles.qunder, styles.num)}>{row.under}</div> : null}
    </>
  );
}

export function NoData({ children }: { children: ReactNode }) {
  return (
    <div className={styles.nodata}>
      <CircleDashed aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

/** The inline fix of a state that needs action (design §2): top up on the
 *  provider's billing page, connect, or the provider's page opened on the
 *  credential that needs it (sign in again, replace the key, top up without
 *  a billing page, the local server's start instructions). Signing in again
 *  renews that account's row; it never adds another. */
export function FixButton({
  fix, onConnect, onOpen, size = "xs",
}: {
  fix: FixWire;
  onConnect: () => void;
  onOpen: (fix?: FixAction) => void;
  size?: "xs" | "sm";
}) {
  const { text } = useTranslation();
  const stop = (fn: () => void) => (event: { stopPropagation: () => void }) => { event.stopPropagation(); fn(); };
  if (fix.action === "top_up") {
    if (fix.url) {
      return (
        <Button asChild variant="elevated" size={size}>
          <a href={fix.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
            {text("Top up", "充值")}<ExternalLink data-icon="inline-end" aria-hidden="true" />
          </a>
        </Button>
      );
    }
    return <Button variant="elevated" size={size} onClick={stop(() => onOpen(fix.action))}>{text("Top up", "充值")}</Button>;
  }
  const label = {
    sign_in: text("Sign in again", "重新登录"),
    replace_key: text("Replace key", "更换密钥"),
    connect: text("Connect", "接入"),
    start_server: text("How to start", "如何启动"),
  }[fix.action];
  return (
    <Button variant="elevated" size={size} onClick={stop(() => (fix.action === "connect" ? onConnect() : onOpen(fix.action)))}>
      {label}
    </Button>
  );
}

/** The quota block of a card (design §2): windows, a balance or rate-limit
 *  headroom (nothing when nothing was reported), the fix of a state that needs one, and the
 *  age of a snapshot older than 15 minutes. */
export function QuotaBlock({
  p, now, onConnect, onOpen,
}: {
  p: ProviderStatus;
  now: number;
  onConnect: () => void;
  onOpen: (fix?: FixAction) => void;
}) {
  const { text } = useTranslation();
  const q = quotaForm(p, now, text);
  const stale = !!p.quota?.stale;
  const fixAge = p.keys.map((k) => k.status_at ?? 0).reduce((a, b) => Math.max(a, b), 0);
  // A provider with no credential yet: the card's own add button connects it.
  const fix = p.fix && !(p.fix.action === "connect" && p.keys.length === 0) ? (
    <div className={`${styles.fixRow} flex items-center gap-[8px] min-w-0`}>
      <FixButton fix={p.fix} onConnect={onConnect} onOpen={onOpen} size={p.fix.action === "sign_in" ? "sm" : "xs"} />
      {fixAge ? <span className={cx(styles.xs, styles.muted)}>{fmtAgo(fixAge, now, text)}</span> : null}
    </div>
  ) : null;
  let body: ReactNode = null;
  if (q.form === "windowed") body = q.rows.map((row) => <QuotaRowView key={row.key} row={row} />);
  else if (q.form === "rate_limit") {
    body = (
      <>
        {q.rows.map((row) => <QuotaRowView key={row.key} row={row} />)}
        <NoData>{text("No balance data", "没有余额数据")}</NoData>
      </>
    );
  } else if (q.form === "balance") {
    body = (
      <>
        <div className={cx(`${styles.big} flex items-baseline gap-[8px]`, q.tone !== "normal" && styles[q.tone])}>
          <b>{q.amount}</b>
          <span className={cx(styles.xs, styles.muted)}>{text("balance", "余额")}</span>
        </div>
        {q.runway && !fix ? <div className={cx(styles.xs, styles.secondary, styles.num)}>{q.runway}</div> : null}
        {q.rows.map((row) => <QuotaRowView key={row.key} row={row} />)}
      </>
    );
  }
  // Nothing reported yet: no block, so the card holds no empty band.
  if (!body && !fix && !(stale && p.quota?.fetched_at) && !(p.quota?.read_error && !fix)) return null;
  return (
    <div className={cx(`${styles.quota} flex flex-col justify-center gap-[6px] min-h-[50px]`, stale && styles.stale, dimmed(p.health.state) && styles.dim)} data-form={q.form}>
      {body ? <div className={`${styles.quotaBody} flex flex-col gap-[6px]`}>{body}</div> : null}
      {fix}
      {stale && p.quota?.fetched_at ? (
        <div className={cx(styles.xs, styles.muted)}>{text("Updated", "更新于")} {fmtAgo(p.quota.fetched_at, now, text)}</div>
      ) : null}
      {p.quota?.read_error && !fix ? (
        <HoverTip label={p.quota.read_error}>
          <span className={cx(styles.xs, styles.muted)} tabIndex={0}>{text("Couldn't read the quota", "额度读取失败")}</span>
        </HoverTip>
      ) : null}
    </div>
  );
}

/** A 30-day sparkline of daily cost (or tokens), from the local ledger. */
export function Sparkline({ values, dim = false, width = 72, height = 22 }: { values: number[]; dim?: boolean; width?: number; height?: number }) {
  if (values.length < 2) return <svg className={`${styles.spark} block flex-none`} width={width} height={height} aria-hidden="true" />;
  const max = Math.max(...values, 0);
  const step = (width - 2) / (values.length - 1);
  const y = (v: number) => (max > 0 ? height - 2.5 - (v / max) * (height - 5) : height - 2.5);
  const points = values.map((v, i) => `${(1 + i * step).toFixed(1)},${y(v).toFixed(1)}`);
  const line = `M${points.join(" L")}`;
  const area = `${line} L${(width - 1).toFixed(1)},${height} L1,${height} Z`;
  return (
    <svg className={cx(`${styles.spark} block flex-none`, dim && styles.dim)} width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path className={styles.sparkArea} d={area} />
      <path className={styles.sparkLine} d={line} />
    </svg>
  );
}

export { cx };
