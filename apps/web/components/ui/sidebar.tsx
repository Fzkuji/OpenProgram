"use client"

// shadcn/ui Sidebar shell, radix-nova style: the registry's `floating`
// variant (/r/styles/radix-nova/sidebar.json) — a p-2 gutter around a
// rounded-lg card on the sidebar surface. Only the shell is taken from
// the registry: the open state, the resizable rail, the narrow-viewport
// overlay and everything inside the card (rows, labels, footer) stay this
// app's own. One adaptation, the same the input box and the elevated
// Button carry: the card is borderless, `shadow-raised` (the per-theme
// composer shadow pair) instead of the registry's `shadow-sm ring-1`, so
// the rails, the centre card (styles/base/layout.css .center-col) and the composer are
// one raised material with one corner. The card clips its content
// (overflow-hidden) so panels that paint their own fill, like the file
// tree, keep the rounded corners; the rail's resize handle is positioned
// against the outer shell, so it is not clipped.
import * as React from "react"

import { cn } from "@/lib/utils"

const Sidebar = React.forwardRef<
  HTMLDivElement,
  React.ComponentProps<"div"> & { side?: "left" | "right" }
>(function Sidebar({ side = "left", className, children, ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn("flex h-full p-2 text-sidebar-foreground", className)}
      data-variant="floating"
      data-side={side}
      data-slot="sidebar"
      {...props}
    >
      <div
        data-sidebar="sidebar"
        data-slot="sidebar-inner"
        className="flex size-full flex-col overflow-hidden rounded-lg bg-sidebar shadow-raised"
      >
        {children}
      </div>
    </div>
  )
})

export { Sidebar }
