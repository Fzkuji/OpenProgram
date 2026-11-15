"use client"

// shadcn/ui ContextMenu, radix-nova style (the style ui.shadcn.com's docs
// use), from the official registry (/r/styles/radix-nova/context-menu.json).
// Adaptations: package imports and forwarded refs (React 18); lucide icons
// in place of the registry's IconPlaceholder; the panel and rows are the
// shared menu recipes from menu-styles (MENU_PANEL, itemCls,
// MENU_ITEM_STATES, …) — Nova's geometry on the app's popover tokens —
// so a right-click menu looks exactly like every dropdown in the app.
//
// For right-click-only menus. A menu that a button opens as well (the
// sidebar chat and project rows) is a DropdownMenu anchored at the click.

import * as React from "react"
import * as ContextMenuPrimitive from "@radix-ui/react-context-menu"
import { ChevronRight } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
  SHORTCUT,
  itemCls,
} from "@/components/ui/menu/menu-styles"
import { MenuCheck } from "@/components/ui/menu/menu-check"

const ContextMenu = ContextMenuPrimitive.Root
const ContextMenuGroup = ContextMenuPrimitive.Group
const ContextMenuPortal = ContextMenuPrimitive.Portal
const ContextMenuSub = ContextMenuPrimitive.Sub
const ContextMenuRadioGroup = ContextMenuPrimitive.RadioGroup

const ContextMenuTrigger = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <ContextMenuPrimitive.Trigger
    ref={ref}
    data-slot="context-menu-trigger"
    className={className}
    {...props}
  />
))
ContextMenuTrigger.displayName = ContextMenuPrimitive.Trigger.displayName

const MENU_MOTION =
  "z-(--z-popover) outline-none origin-(--radix-context-menu-content-transform-origin) duration-100 " +
  "data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 " +
  "data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 " +
  "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 " +
  "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95"

const ContextMenuContent = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Content>
>(({ className, ...props }, ref) => (
  <ContextMenuPrimitive.Portal>
    <ContextMenuPrimitive.Content
      ref={ref}
      data-slot="context-menu-content"
      className={cn(MENU_PANEL, MENU_MOTION, "min-w-36 text-text-primary", className)}
      {...props}
    />
  </ContextMenuPrimitive.Portal>
))
ContextMenuContent.displayName = ContextMenuPrimitive.Content.displayName

const ContextMenuItem = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Item> & {
    inset?: boolean
    variant?: "default" | "destructive"
  }
>(({ className, inset, variant = "default", ...props }, ref) => (
  <ContextMenuPrimitive.Item
    ref={ref}
    data-slot="context-menu-item"
    data-inset={inset}
    data-variant={variant}
    className={cn(
      itemCls(false, variant === "destructive"),
      MENU_ITEM_STATES,
      variant === "destructive" &&
        "data-[highlighted]:bg-[color-mix(in_srgb,var(--accent-red)_15%,transparent)] data-[highlighted]:text-[var(--accent-red)]",
      inset && "pl-7",
      className
    )}
    {...props}
  />
))
ContextMenuItem.displayName = ContextMenuPrimitive.Item.displayName

const ContextMenuSubTrigger = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.SubTrigger>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubTrigger> & {
    inset?: boolean
  }
>(({ className, inset, children, ...props }, ref) => (
  <ContextMenuPrimitive.SubTrigger
    ref={ref}
    data-slot="context-menu-sub-trigger"
    data-inset={inset}
    className={cn(itemCls(false), MENU_ITEM_STATES, inset && "pl-7", className)}
    {...props}
  >
    {children}
    <ChevronRight className="ml-auto size-4 shrink-0 text-text-muted" aria-hidden="true" />
  </ContextMenuPrimitive.SubTrigger>
))
ContextMenuSubTrigger.displayName = ContextMenuPrimitive.SubTrigger.displayName

const ContextMenuSubContent = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.SubContent>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubContent>
// A submenu stands 4px off its parent panel (the offset is measured from
// the row, inside the panel's p-1), and its first row lines up with that row.
>(({ className, sideOffset = 8, alignOffset = -4, ...props }, ref) => (
  <ContextMenuPrimitive.Portal>
    <ContextMenuPrimitive.SubContent
      ref={ref}
      sideOffset={sideOffset}
      alignOffset={alignOffset}
      data-slot="context-menu-sub-content"
      className={cn(MENU_PANEL, MENU_MOTION, "text-text-primary", className)}
      {...props}
    />
  </ContextMenuPrimitive.Portal>
))
ContextMenuSubContent.displayName = ContextMenuPrimitive.SubContent.displayName

/** A check row: the app's trailing accent check (MenuCheck) rather than
 *  Nova's absolutely placed one, so it lines up with every other menu. */
const ContextMenuCheckboxItem = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.CheckboxItem>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.CheckboxItem>
>(({ className, children, checked, ...props }, ref) => (
  <ContextMenuPrimitive.CheckboxItem
    ref={ref}
    data-slot="context-menu-checkbox-item"
    checked={checked}
    className={cn(itemCls(false), MENU_ITEM_STATES, className)}
    {...props}
  >
    <span className="min-w-0 flex-1 truncate">{children}</span>
    {checked ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
  </ContextMenuPrimitive.CheckboxItem>
))
ContextMenuCheckboxItem.displayName = ContextMenuPrimitive.CheckboxItem.displayName

const ContextMenuRadioItem = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.RadioItem>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.RadioItem>
>(({ className, children, ...props }, ref) => (
  <ContextMenuPrimitive.RadioItem
    ref={ref}
    data-slot="context-menu-radio-item"
    className={cn(itemCls(false), MENU_ITEM_STATES, className)}
    {...props}
  >
    <span className="min-w-0 flex-1 truncate">{children}</span>
    <span className={CHECK_SLOT_PAD}>
      <ContextMenuPrimitive.ItemIndicator>
        <MenuCheck />
      </ContextMenuPrimitive.ItemIndicator>
    </span>
  </ContextMenuPrimitive.RadioItem>
))
ContextMenuRadioItem.displayName = ContextMenuPrimitive.RadioItem.displayName

const ContextMenuLabel = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Label> & { inset?: boolean }
>(({ className, inset, ...props }, ref) => (
  <ContextMenuPrimitive.Label
    ref={ref}
    data-slot="context-menu-label"
    className={cn(GROUP_LABEL, inset && "pl-7", className)}
    {...props}
  />
))
ContextMenuLabel.displayName = ContextMenuPrimitive.Label.displayName

const ContextMenuSeparator = React.forwardRef<
  React.ElementRef<typeof ContextMenuPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <ContextMenuPrimitive.Separator
    ref={ref}
    data-slot="context-menu-separator"
    className={cn(MENU_SEPARATOR, className)}
    {...props}
  />
))
ContextMenuSeparator.displayName = ContextMenuPrimitive.Separator.displayName

function ContextMenuShortcut({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="context-menu-shortcut"
      className={cn("ml-auto", SHORTCUT, className)}
      {...props}
    />
  )
}

export {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuCheckboxItem,
  ContextMenuRadioItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuGroup,
  ContextMenuPortal,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuRadioGroup,
}
