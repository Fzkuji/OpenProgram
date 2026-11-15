"use client"

// shadcn/ui Label, radix-nova style, copied verbatim from the official
// registry (/r/styles/radix-nova/label.json). Adaptations: the Radix
// import is @radix-ui/react-label, and the root forwards its ref
// (React 18: refs are not props until 19).
import * as React from "react"
import * as LabelPrimitive from "@radix-ui/react-label"

import { cn } from "@/lib/utils"

const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    data-slot="label"
    className={cn(
      "flex items-center gap-2 text-fs-base leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
      className
    )}
    {...props}
  />
))
Label.displayName = LabelPrimitive.Root.displayName

export { Label }
