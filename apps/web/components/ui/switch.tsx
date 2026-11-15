"use client"

// shadcn/ui Switch, radix-nova style, copied verbatim from the official
// registry (/r/styles/radix-nova/switch.json). Adaptations: the Radix
// import is @radix-ui/react-switch, and the root forwards its ref
// (React 18: refs are not props until 19). The thumb is sized in px like
// the track: Nova's size-4 / size-3 are rem, which the 14px root shrank to
// 14px / 10.5px, so the thumb sat off-centre with a gap at the end.
import * as React from "react"
import * as SwitchPrimitive from "@radix-ui/react-switch"

import { cn } from "@/lib/utils"

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root> & {
    size?: "sm" | "default"
  }
>(({ className, size = "default", ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    data-slot="switch"
    data-size={size}
    className={cn(
      "peer group/switch relative inline-flex shrink-0 items-center rounded-full border border-transparent transition-all outline-none group-has-[:focus-visible]/field-label:border-transparent group-has-[:focus-visible]/field-label:ring-0 after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[size=default]:h-[18.4px] data-[size=default]:w-[32px] data-[size=sm]:h-[14px] data-[size=sm]:w-[24px] dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-checked:bg-primary data-unchecked:bg-input dark:data-unchecked:bg-input/80 data-disabled:cursor-not-allowed data-disabled:opacity-50",
      className
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      data-slot="switch-thumb"
      className="pointer-events-none block rounded-full bg-background ring-0 transition-transform group-data-[size=default]/switch:size-[16px] group-data-[size=sm]/switch:size-[12px] group-data-[size=default]/switch:data-checked:translate-x-[14px] group-data-[size=sm]/switch:data-checked:translate-x-[10px] dark:data-checked:bg-primary-foreground group-data-[size=default]/switch:data-unchecked:translate-x-0 group-data-[size=sm]/switch:data-unchecked:translate-x-0 dark:data-unchecked:bg-foreground"
    />
  </SwitchPrimitive.Root>
))
Switch.displayName = SwitchPrimitive.Root.displayName

export { Switch }
