"use client"

// shadcn/ui Progress, radix-nova geometry (/r/styles/radix-nova/progress.json):
// a rounded track with an indicator. Written without the Radix primitive (the
// registry's only use of it is the role and value attributes, set here) and on
// the app's meter tokens, so every quota bar is this one part: the provider
// cards and rows, a credential's limits, and the model menu's usage rows.
// `tone` colours the fill at the same thresholds the context meter uses
// (warning from 80 % used, danger from 95 %); an exhausted bar keeps a
// danger-tinted track so an empty bar still reads as spent; `stale` draws an
// old reading at half opacity.
import * as React from "react"

import { cn } from "@/lib/utils"

type ProgressTone = "normal" | "warning" | "danger"

const FILL: Record<ProgressTone, string> = {
  normal: "bg-(--meter-fill)",
  warning: "bg-(--warning-soft)",
  danger: "bg-(--danger-soft)",
}

function Progress({
  className,
  value,
  tone = "normal",
  stale = false,
  ...props
}: React.ComponentProps<"div"> & {
  /** 0–100. */
  value: number
  tone?: ProgressTone
  stale?: boolean
}) {
  const pct = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      data-slot="progress"
      data-tone={tone}
      className={cn(
        "relative flex h-1 w-full items-center overflow-hidden rounded-full",
        tone === "danger"
          ? "bg-[color-mix(in_oklab,var(--danger-soft)_24%,transparent)]"
          : "bg-(--meter-track)",
        className
      )}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className={cn("size-full flex-1 rounded-full transition-all", FILL[tone], stale && "opacity-50")}
        style={{ transform: `translateX(-${100 - pct}%)` }}
      />
    </div>
  )
}

export { Progress, type ProgressTone }
