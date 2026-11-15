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
        // The app's raised face, as on Button: no border, the composer
        // shadow pair, an accent ring when on.
        elevated: "bg-bg-input shadow-raised hover:bg-bg-input hover:shadow-raised-hover aria-pressed:bg-bg-input data-[state=on]:bg-bg-input aria-pressed:ring-[1.5px] aria-pressed:ring-primary data-[state=on]:ring-[1.5px] data-[state=on]:ring-primary",
      },
      size: {
        default:
          "h-(--ui-button-h) min-w-(--ui-button-h) px-[10px] has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        sm: "h-(--ui-button-h-sm) min-w-(--ui-button-h-sm) px-[10px] text-fs-sm has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-(--ui-button-h-lg) min-w-(--ui-button-h-lg) px-[10px] has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
      },
    },
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
