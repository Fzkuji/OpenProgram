"use client"

// shadcn/ui Separator, radix-nova style, copied verbatim from the official
// registry (/r/styles/radix-nova/separator.json). Adaptations: the Radix
// import is @radix-ui/react-separator, and the root forwards its ref
// (React 18: refs are not props until 19).
import * as React from "react"
import * as SeparatorPrimitive from "@radix-ui/react-separator"

import { cn } from "@/lib/utils"

const Separator = React.forwardRef<
  React.ElementRef<typeof SeparatorPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SeparatorPrimitive.Root>
>(({ className, orientation = "horizontal", decorative = true, ...props }, ref) => (
  <SeparatorPrimitive.Root
    ref={ref}
    data-slot="separator"
    decorative={decorative}
    orientation={orientation}
    className={cn(
      "shrink-0 bg-border data-horizontal:h-px data-horizontal:w-full data-vertical:w-px data-vertical:self-stretch",
      className
    )}
    {...props}
  />
))
Separator.displayName = SeparatorPrimitive.Root.displayName

export { Separator }
