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
  const [open, setOpen] = React.useState(false)
  const triggerRef = React.useRef<HTMLElement | null>(null)
  // 指针是否真的悬在 trigger 上。radix 在 trigger 拿到焦点时也会请求
  // 打开提示——弹层里选完选项后焦点回到 trigger，就是"选完反而冒
  // 提示"的来源。纯 focus（指针不在）一律不冒。
  const pointerInside = React.useRef(false)
  return (
    <TooltipProvider delayDuration={600}>
      <Tooltip
        open={open}
        onOpenChange={(next) => {
          // trigger 挂着已打开的弹层（PopoverTrigger/Menu 会标
          // aria-expanded="true"）时不再冒提示——弹窗里自带解释，
          // 提示只会和弹窗叠在一起。
          if (
            next &&
            (triggerRef.current?.getAttribute("aria-expanded") === "true" ||
              !pointerInside.current)
          ) {
            return
          }
          setOpen(next)
        }}
      >
        <TooltipTrigger
          asChild
          ref={triggerRef as React.Ref<HTMLButtonElement>}
          onPointerEnter={() => {
            pointerInside.current = true
          }}
          onPointerLeave={() => {
            pointerInside.current = false
          }}
        >
          {children}
        </TooltipTrigger>
        <TooltipContent side={side}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, HoverTip }
