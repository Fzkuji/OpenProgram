"use client"

import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"

import { cn } from "@/lib/utils"

const TooltipProvider = TooltipPrimitive.Provider

const Tooltip = TooltipPrimitive.Root

const TooltipTrigger = TooltipPrimitive.Trigger

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Content
    ref={ref}
    sideOffset={sideOffset}
    className={cn(
      // `hover-tip` (app/styles/chat.css) carries the Claude-style look:
      // NO border, a solid badge a touch lighter than the surface, crisp
      // text. Keep only geometry / shadow / animation here.
      "hover-tip z-50 overflow-hidden rounded-[8px] px-[10px] py-[6px] text-[13px] shadow-(--shadow-popover) animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 origin-[--radix-tooltip-content-transform-origin]",
      className
    )}
    {...props}
  />
))
TooltipContent.displayName = TooltipPrimitive.Content.displayName

/** Convenience wrapper: <HoverTip label="..."> {children}. 包成 Provider/
 *  Root/Trigger/Content 链, 消费方只用关心 label 字符串。message-actions
 *  这种用 label 跟 children 一锅出的位置批量用. */
interface HoverTipProps {
  label: React.ReactNode
  children: React.ReactElement
  side?: "top" | "right" | "bottom" | "left"
}
function HoverTip({ label, children, side = "top" }: HoverTipProps) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent side={side}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, HoverTip }
