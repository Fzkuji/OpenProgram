"use client"

import * as React from "react"
import * as TogglePrimitive from "@radix-ui/react-toggle"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const toggleVariants = cva(
  "group/toggle inline-flex items-center justify-center gap-1 rounded-md text-fs-base font-medium whitespace-nowrap transition-all outline-none hover:bg-muted hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 aria-pressed:bg-muted data-[state=on]:bg-muted dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-transparent",
        // Switches that are each on or off on their own (the days of a
        // week, a tool that stays armed): the app's raised face, as on
        // Button, filled with a tint of the accent while on.
        elevated: "bg-bg-input shadow-raised hover:bg-bg-input hover:shadow-raised-hover aria-pressed:bg-primary/12 aria-pressed:text-primary data-[state=on]:bg-primary/12 data-[state=on]:text-primary",
        // One choice of a few: a groove (ToggleGroup draws it and
        // the raised face that slides under the chosen option); the options
        // are plain text, the chosen one bright.
        segmented: "relative z-10 rounded-md bg-transparent text-text-secondary hover:bg-transparent hover:text-text-bright aria-pressed:bg-transparent data-[state=on]:bg-transparent data-[state=on]:text-text-bright",
      },
      size: {
        default:
          "h-(--ui-button-h) min-w-(--ui-button-h) px-[10px] has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-(--ui-button-h-xs) min-w-(--ui-button-h-xs) rounded-sm px-2 text-fs-xs [&_svg:not([class*='size-'])]:size-3",
        sm: "h-(--ui-button-h-sm) min-w-(--ui-button-h-sm) px-[10px] text-fs-sm has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-(--ui-button-h-lg) min-w-(--ui-button-h-lg) px-[10px] has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
      },
    },
    // Inside a groove an option is the groove's height less its padding:
    // default 26 + 3 + 3 = 32, sm 24 + 2 + 2 = 28, xs 20 + 2 + 2 = 24,
    // lg 30 + 3 + 3 = 36. The groove's corner is the option's plus the
    // padding, so the two curves stay concentric.
    compoundVariants: [
      { variant: "segmented", size: "default", className: "h-(--ui-choice-h) min-w-0 px-3 text-fs-sm" },
      { variant: "segmented", size: "sm", className: "h-(--ui-button-h-xs) min-w-0 px-3" },
      { variant: "segmented", size: "xs", className: "h-(--ui-control-inline) min-w-0 rounded-sm px-2.5" },
      { variant: "segmented", size: "lg", className: "h-(--ui-button-h) min-w-0 px-3.5" },
    ],
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

const Toggle = React.forwardRef<
  React.ElementRef<typeof TogglePrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof TogglePrimitive.Root> &
    VariantProps<typeof toggleVariants>
>(({ className, variant = "default", size = "default", ...props }, ref) => (
  <TogglePrimitive.Root
    ref={ref}
    data-slot="toggle"
    className={cn(toggleVariants({ variant, size, className }))}
    {...props}
  />
))
Toggle.displayName = TogglePrimitive.Root.displayName

export { Toggle, toggleVariants }
