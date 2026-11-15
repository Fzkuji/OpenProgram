"use client";

/**
 * The marks the model picker draws for a provider's state
 * (docs/reference/design/providers/provider-management.html#chat):
 *
 *   - QuotaRing   — 16 px ring of % left, for a window or key limit once
 *                   half is used; the context meter's tokens, warning from
 *                   80 % used, danger from 95 %; a stale value at half opacity.
 *   - StateWord   — the word a row shows when the provider needs action.
 *   - QuotaRows   — the submenu header: `label [bar] NN% · reset` per window,
 *                   or an amount (balance, credits), the Codex row.
 *   - TriggerMark — the dot on the picker trigger when action is needed or
 *                   the quota warns.
 *
 * Colours are the existing tokens only (`--meter-fill`, `--meter-track`,
 * `--warning-soft`, `--danger-soft`); hints ride the controls (`data-tip`).
 */
import type { PickerQuota, Tone } from "@/lib/settings/provider-status.generated";
import { percentLeft, type QuotaRow, type WordTone } from "@/lib/models/provider-state";
import { cn } from "@/lib/utils";

const TONE_VAR: Record<Tone, string> = {
  normal: "var(--meter-fill)",
  warning: "var(--warning-soft)",
  danger: "var(--danger-soft)",
};

const R = 6;
const CIRCUMFERENCE = 2 * Math.PI * R;

export function QuotaRing({ quota, stale = false, tip, className }: { quota: PickerQuota; stale?: boolean; tip?: string; className?: string }) {
  const left = Math.max(0, Math.min(1, quota.left_fraction));
  const arc = (left * CIRCUMFERENCE).toFixed(2);
  return (
    <svg
      viewBox="0 0 16 16"
      width={16}
      height={16}
      className={cn("inline-block shrink-0 -rotate-90", stale && "opacity-50", className)}
      data-quota-ring={quota.tone}
      data-tip={tip}
      role="img"
      aria-label={tip ?? `${percentLeft(left)}%`}
    >
      <circle cx="8" cy="8" r={R} fill="none" strokeWidth={3} style={{ stroke: "var(--meter-track)" }} />
      <circle
        cx="8" cy="8" r={R} fill="none" strokeWidth={3} strokeLinecap="round"
        strokeDasharray={`${arc} ${CIRCUMFERENCE.toFixed(2)}`}
        style={{ stroke: TONE_VAR[quota.tone] }}
      />
    </svg>
  );
}

export function StateWord({ label, tone, tip }: { label: string; tone: WordTone; tip?: string }) {
  return (
    <span
      data-state-word={tone}
      data-tip={tip}
      className={cn("shrink-0 text-fs-xs", tone === "muted" ? "text-text-muted" : undefined)}
      style={tone === "muted" ? undefined : { color: TONE_VAR[tone] }}
    >
      {label}
    </span>
  );
}

export function QuotaRows({ rows }: { rows: QuotaRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="grid grid-cols-[auto_minmax(64px,1fr)_auto] items-center gap-x-2.5 px-1.5 pt-0.5 pb-1.5" data-quota-rows>
      {rows.map((row) => (
        <div key={row.key} className={cn("contents", row.stale && "[&>*]:opacity-50")} data-quota-row={row.tone}>
          <span className="text-fs-xs font-medium text-text-secondary">{row.label}</span>
          {row.left == null ? (
            <span />
          ) : (
            <span className="h-1.5 overflow-hidden rounded-full bg-(--meter-track)">
              <span
                className="block h-full rounded-full"
                style={{ width: `${Math.round(row.left * 100)}%`, background: TONE_VAR[row.tone] }}
              />
            </span>
          )}
          <span
            className="text-right text-fs-xs whitespace-nowrap tabular-nums text-text-secondary"
            style={row.tone === "normal" ? undefined : { color: TONE_VAR[row.tone] }}
          >
            {row.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** A dot on the provider logo of the picker trigger. */
export function TriggerMark({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden="true"
      data-trigger-mark={tone}
      className="pointer-events-none absolute -top-0.5 -right-0.5 size-1.5 rounded-full ring-2 ring-(--bg-input)"
      style={{ background: TONE_VAR[tone] }}
    />
  );
}
