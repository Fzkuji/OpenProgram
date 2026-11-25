"use client"

// shadcn/ui Tabs, radix-nova style, copied from the official registry
// (/r/styles/radix-nova/tabs.json) and given the app's one look for a choice
// among a few options: the list is a rounded groove, the triggers plain
// text, and a raised face (<SlidingThumb>) slides under the chosen one.
// `default` is a view or filter switch, as tall as a button; `section` is a
// page's section switch, one size up, and its content slides in from the
// side the switch moved towards.

import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import * as TabsPrimitive from "@radix-ui/react-tabs"

import { cn } from "@/lib/utils"
import { SlidingThumb } from "@/components/ui/sliding-thumb"

function Tabs({
  className,
  orientation = "horizontal",
  onValueChange,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  const root = React.useRef<HTMLDivElement>(null)
  // Which way the choice moved, for the content's slide.
  const moved = (next: string) => {
    const element = root.current
    if (element) {
      const values = Array.from(element.querySelectorAll<HTMLElement>('[data-slot="tabs-trigger"]'))
        .filter((trigger) => trigger.closest('[data-slot="tabs"]') === element)
        .map((trigger) => trigger.dataset.value)
      const current = element.querySelector<HTMLElement>('[data-slot="tabs-trigger"][aria-selected="true"]')?.dataset.value
      const from = values.indexOf(current), to = values.indexOf(next)
      if (from >= 0 && to >= 0 && from !== to) element.dataset.direction = to > from ? "forward" : "back"
    }
    onValueChange?.(next)
  }
  return (
    <TabsPrimitive.Root
      ref={root}
      data-slot="tabs"
      data-orientation={orientation}
      orientation={orientation}
      onValueChange={moved}
      className={cn(
        "group/tabs flex gap-2 data-horizontal:flex-col",
        className
      )}
      {...props}
    />
  )
}

const tabsListVariants = cva(
  "group/tabs-list relative isolate inline-flex w-fit shrink-0 items-center justify-center bg-groove shadow-groove group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col group-data-vertical/tabs:rounded-lg",
  {
    variants: {
      variant: {
        default: "rounded-groove p-[3px]",
        section: "rounded-xl p-1",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function TabsList({
  className,
  variant = "default",
  children,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> &
  VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    >
      {children}
      <SlidingThumb selector='[data-slot="tabs-trigger"][aria-selected="true"]' />
    </TabsPrimitive.List>
  )
}

function TabsTrigger({
  className,
  value,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      data-value={value}
      value={value}
      className={cn(
        // The chosen one is marked by aria-selected, which a tooltip trigger
        // wrapped around it leaves alone (it rewrites data-state).
        "relative z-10 inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border-0 bg-transparent font-medium whitespace-nowrap text-text-secondary outline-none transition-colors duration-(--motion-fast) group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start hover:text-text-bright aria-selected:text-text-bright focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        "group-data-[variant=default]/tabs-list:h-(--ui-choice-h) group-data-[variant=default]/tabs-list:px-3 group-data-[variant=default]/tabs-list:text-fs-sm",
        "group-data-[variant=section]/tabs-list:h-(--ui-button-h) group-data-[variant=section]/tabs-list:px-4 group-data-[variant=section]/tabs-list:text-fs-base",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn(
        "flex-1 text-fs-base outline-none",
        // After the choice moves, the shown content slides in from that side.
        "group-data-[direction]/tabs:data-[state=active]:animate-in group-data-[direction]/tabs:data-[state=active]:fade-in-0 group-data-[direction]/tabs:data-[state=active]:duration-(--motion-slow) motion-reduce:animate-none",
        "group-data-[direction=forward]/tabs:data-[state=active]:slide-in-from-right-1/12 group-data-[direction=back]/tabs:data-[state=active]:slide-in-from-left-1/12",
        className
      )}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants }
