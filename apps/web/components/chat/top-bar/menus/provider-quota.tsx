"use client";

/**
 * The marks the model picker draws for a provider's state
 * (docs/reference/design/providers/provider-management.html#chat):
 *
 *   - QuotaRing   — 16 px ring of % left, for a window or key limit once
 *                   half is used; the context meter's tokens, warning from
 *                   80 % used, danger from 95 %; a stale value at half opacity.
 *   - StateWord   — the word a row shows when the provider needs action.
 *   - QuotaRows   — the submenu header: `label reset ···· NN%` over a
 *                   full-width bar per window, or `label ···· amount`
 *                   (balance, credits).
 *   - TriggerMark — the red dot on the picker trigger when the provider
 *                   cannot answer or its quota is nearly spent.
 *
 * Colours are the existing tokens only (`--meter-fill`, `--meter-track`,
 * `--warning-soft`, `--danger-soft`); hints ride the controls (`data-tip`).
 */
import { Progress } from "@/components/ui/progress";
import type { PickerQuota, Tone } from "@/lib/settings/provider-status.generated";
import { percentLeft, type QuotaRow, type WordTone } from "@/lib/models/provider-state";
import { cn } from "@/lib/utils";

/** Each tone as text, stroke and fill classes on the meter tokens. */
const TONE_TEXT: Record<Tone, string> = {
  normal: "text-(--meter-fill)",
  warning: "text-(--warning-soft)",
  danger: "text-(--danger-soft)",
};
const TONE_STROKE: Record<Tone, string> = {
  normal: "stroke-(--meter-fill)",
  warning: "stroke-(--warning-soft)",
  danger: "stroke-(--danger-soft)",
};
const TONE_FILL: Record<Tone, string> = {
  normal: "bg-(--meter-fill)",
  warning: "bg-(--warning-soft)",
  danger: "bg-(--danger-soft)",
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
      <circle cx="8" cy="8" r={R} fill="none" strokeWidth={3} className="stroke-(--meter-track)" />
      <circle
        cx="8" cy="8" r={R} fill="none" strokeWidth={3} strokeLinecap="round"
        strokeDasharray={`${arc} ${CIRCUMFERENCE.toFixed(2)}`}
        className={TONE_STROKE[quota.tone]}
      />
    </svg>
  );
}

export function StateWord({ label, tone, tip }: { label: string; tone: WordTone; tip?: string }) {
  return (
    <span
      data-state-word={tone}
      data-tip={tip}
      className={cn("shrink-0 text-fs-xs", tone === "muted" ? "text-text-muted" : TONE_TEXT[tone])}
    >
      {label}
    </span>
  );
}

export function QuotaRows({ rows }: { rows: QuotaRow[] }) {
  if (!rows.length) return null;
  // One line per limit at the menu's secondary size: the label in the
  // primary text colour, the reset muted beside it, the value on the right
  // edge of the model rows' trailing column. A window or key limit draws a
  // full-width bar under its line (the kit's Progress, the same bar as the
  // provider cards); an amount without a limit is the line alone. Tone
  // colours the value and the bar fill; a stale value is muted and its fill
  // drawn at half opacity, like the ring. A long label or reset
  // truncates at the end (logical sides, so RTL mirrors) and never widens
  // the submenu or pushes the value off its edge.
  return (
    <div className="flex flex-col gap-2 px-1.5 pt-0.5 pb-2" data-quota-rows>
      {rows.map((row) => (
        <div key={row.key} data-quota-row={row.tone} data-stale={row.stale || undefined}>
          <div className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap text-fs-sm leading-[18px]">
            <span className="min-w-0 truncate text-text-primary">{row.label}</span>
            {row.reset ? <span className="min-w-0 shrink-[2] truncate text-fs-xs text-text-muted">{row.reset}</span> : null}
            <span
              className={cn("ms-auto shrink-0 ps-3", row.stale ? "text-text-muted" : row.tone === "normal" ? "text-text-primary" : TONE_TEXT[row.tone])}
            >
              {row.pct == null ? row.value : `${row.pct}%`}
            </span>
          </div>
          {row.left == null ? null : (
            <Progress className="mt-1.5" value={row.left * 100} tone={row.tone} stale={row.stale} />
          )}
        </div>
      ))}
    </div>
  );
}

/** A dot on the provider logo of the picker trigger (danger tone; see
 *  `triggerMark`). */
export function TriggerMark({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden="true"
      data-trigger-mark={tone}
      className={cn("pointer-events-none absolute -top-0.5 -right-0.5 size-1.5 rounded-full ring-2 ring-(--bg-input)", TONE_FILL[tone])}
    />
  );
}
