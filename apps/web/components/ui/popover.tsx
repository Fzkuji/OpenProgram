"use client"

// shadcn/ui Popover, radix-nova style (the style ui.shadcn.com's docs use),
// from the official registry (/r/styles/radix-nova/popover.json).
// Adaptations: package imports and forwarded refs (React 18); the frame
// reads --popover-ring / --popover-shadow like the menus and the Select
// popup (Nova's ring-foreground/10 + shadow-md in light themes, lifted in
// dark ones, styles/design/shadcn.css). A content with bg-transparent hosts its own panel
// (MENU_PANEL, a custom picker) and gets none of the surface, so its
// children keep the app's own sizes.
//
// NON-modal on purpose. Modal mode's pointer-events shield killed ALL
// page interaction while a menu was open (no scrolling, no window drag) —
// far more than the ask. The real requirement — "no tooltips while a
// menu is open" — is handled by a guard inside HoverTip itself.

import * as React from "react"
import * as PopoverPrimitive from "@radix-ui/react-popover"

import { cn } from "@/lib/utils"

const Popover = (props: React.ComponentProps<typeof PopoverPrimitive.Root>) => (
  <PopoverPrimitive.Root data-slot="popover" {...props} />
)

const PopoverTrigger = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Trigger>
>((props, ref) => <PopoverPrimitive.Trigger ref={ref} data-slot="popover-trigger" {...props} />)
PopoverTrigger.displayName = PopoverPrimitive.Trigger.displayName

const PopoverAnchor = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Anchor>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Anchor>
>((props, ref) => <PopoverPrimitive.Anchor ref={ref} data-slot="popover-anchor" {...props} />)
PopoverAnchor.displayName = PopoverPrimitive.Anchor.displayName

/** Nova's popover surface. */
export const POPOVER_SURFACE =
  "flex w-72 flex-col gap-2.5 rounded-lg bg-popover p-2.5 text-fs-base text-popover-foreground shadow-(--popover-shadow) transition-shadow hover:shadow-(--popover-shadow-hover) ring-1 ring-(color:--popover-ring)"

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = "center", sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      data-slot="popover-content"
      align={align}
      sideOffset={sideOffset}
      className={cn(
        "z-(--z-popover) origin-(--radix-popover-content-transform-origin) outline-hidden duration-100 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
        /\bbg-transparent\b/.test(className ?? "") ? "w-72" : POPOVER_SURFACE,
        className
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
))
PopoverContent.displayName = PopoverPrimitive.Content.displayName

function PopoverHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="popover-header"
      className={cn("flex flex-col gap-0.5 text-fs-base", className)}
      {...props}
    />
  )
}

function PopoverTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <div
      data-slot="popover-title"
      className={cn("font-medium", className)}
      {...props}
    />
  )
}

function PopoverDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="popover-description"
      className={cn("text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
}
