"use client";

/**
 * The pieces the compact rows and the detail page share: the health chip,
 * a quota bar, the 30-day sparkline and the inline fix button.
 */
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { FixAction, FixWire, HealthWire } from "@/lib/settings/provider-status.generated";

import {
  chipTone,
  healthLabel,
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
