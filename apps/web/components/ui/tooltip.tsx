"use client"

import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"

import { cn } from "@/lib/utils"

// shadcn/ui Tooltip, radix-nova style (the style ui.shadcn.com's docs use),
// from the official registry (/r/styles/radix-nova/tooltip.json): an
// inverted badge with an arrow. Adaptations: package imports, a forwarded
// ref (React 18), the app's type tier instead of text-xs, and
// a z-index above the app's dialogs. HoverTip / TipBody below are the
// app's wrapper around it.

const TooltipProvider = TooltipPrimitive.Provider

const Tooltip = TooltipPrimitive.Root

const TooltipTrigger = TooltipPrimitive.Trigger

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 0, children, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      data-slot="tooltip-content"
      sideOffset={sideOffset}
      className={cn(
        // Above dialogs and every menu (layers.css): the tip is portalled.
        "z-(--z-tooltip) inline-flex w-fit max-w-xs origin-(--radix-tooltip-content-transform-origin) items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-fs-xs text-background has-data-[slot=kbd]:pr-1.5 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 **:data-[slot=kbd]:relative **:data-[slot=kbd]:isolate **:data-[slot=kbd]:z-50 **:data-[slot=kbd]:rounded-sm data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=delayed-open]:zoom-in-95 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
        className
      )}
      {...props}
    >
      {children}
      <TooltipPrimitive.Arrow className="z-50 size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-2xs bg-foreground fill-foreground" />
    </TooltipPrimitive.Content>
  </TooltipPrimitive.Portal>
))
TooltipContent.displayName = TooltipPrimitive.Content.displayName

/** Convenience wrapper: <HoverTip label="..."> {children}. 包成 Provider/
 *  Root/Trigger/Content 链, 消费方只用关心 label 字符串。message-actions
 *  这种用 label 跟 children 一锅出的位置批量用. */
interface HoverTipProps {
  label: React.ReactNode
  children: React.ReactElement
  side?: "top" | "right" | "bottom" | "left"
  /** false keeps the tip on `side` even when it is short of room, for
   *  controls whose other sides are covered (a native web view). */
  avoidCollisions?: boolean
}
/** Hover dwell before a tip shows: long enough that passing the pointer
 *  over the controls never flashes tips, short enough to still help. */
const HOVER_TIP_DELAY_MS = 1500

function HoverTip({ label, children, side = "top", avoidCollisions }: HoverTipProps) {
  const [open, setOpen] = React.useState(false)
  const triggerRef = React.useRef<HTMLElement | null>(null)
  // 指针是否真的悬在 trigger 上。radix 在 trigger 拿到焦点时也会请求
  // 打开提示——弹层里选完选项后焦点回到 trigger，就是"选完反而冒
  // 提示"的来源。纯 focus（指针不在）一律不冒。
  const pointerInside = React.useRef(false)
  return (
    <TooltipProvider delayDuration={HOVER_TIP_DELAY_MS}>
      <Tooltip
        open={open}
        onOpenChange={(next) => {
          // trigger 挂着已打开的弹层（PopoverTrigger/Menu 会标
          // aria-expanded="true"）时不再冒提示——弹窗里自带解释，
          // 提示只会和弹窗叠在一起。页面上任何菜单/弹层开着时也不
          // 冒（轻量守卫，替代 modal 的全页交互屏蔽）。
          if (
            next &&
            (triggerRef.current?.getAttribute("aria-expanded") === "true" ||
              // A tip on a wrapper (a Select keeps its own trigger) stays
              // away while the control inside has its popup open.
              triggerRef.current?.querySelector('[aria-expanded="true"]') != null ||
              !pointerInside.current ||
              document.querySelector(
                '[data-radix-popper-content-wrapper] :is([role="dialog"],[role="menu"])',
              ) != null)
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
        <TooltipContent side={side} avoidCollisions={avoidCollisions}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/** The one hover-tip layout for controls: a bright first line saying what
 *  the control is and its current value, then an optional muted line with
 *  the detail (a path, what a click does). Pass it as HoverTip's label. */
function TipBody({ title, detail }: { title: React.ReactNode; detail?: React.ReactNode }) {
  return (
    <span className="hover-tip-body">
      <span className="hover-tip-title">{title}</span>
      {detail ? <span className="hover-tip-detail">{detail}</span> : null}
    </span>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, HoverTip, TipBody }
