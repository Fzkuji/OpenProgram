"use client"

/**
 * Thin radix wrapper, mirroring ui/popover.tsx. Same rule as popover:
 * the Content wrapper carries NO frame and NO width — callers put
 * MENU_PANEL from `components/ui/menu/menu-styles` on it (or on an
 * inner div), so every menu in the app shares one visual definition and
 * fits its content (MENU_PANEL caps the width). Radix supplies the parts
 * popovers make us hand-roll: roving focus, typeahead, Esc,
 * outside-click, and `role="menu"/"menuitem"` semantics. A submenu sits
 * where shadcn's radix-nova one does (no offsets): against its trigger
 * row, top edges level.
 *
 * Every panel hides once its trigger leaves the layout (`hideWhenDetached`):
 * a row that routes away from the chat hides the chat column while the
 * panel still fades out, and Floating UI would otherwise place the fading
 * panel against the trigger's zero rect, at the window's top-left corner.
 *
 * On a touch phone every panel is a sheet from the bottom of the screen and
 * a submenu opens over its parent with a Back row (menu/phone-sheet.tsx);
 * `phoneDrop` drops it from the top instead, for a menu opened from the
 * top of the screen (the phone's computer name).
 */

import * as React from "react"
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu"

import { cn } from "@/lib/utils"
import { PhoneSheetBack, PhoneSub, usePhoneSheet, usePhoneSheetSwipe } from "@/components/ui/menu/phone-sheet"

const DropdownMenu = DropdownMenuPrimitive.Root

const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger

const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content> & { phoneDrop?: boolean }
>(({ className, align = "end", sideOffset = 4, hideWhenDetached = true, phoneDrop, onPointerDownOutside, ...props }, ref) => {
  const sheet = usePhoneSheet()
  const swipe = usePhoneSheetSwipe(sheet)
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        ref={ref}
        align={align}
        sideOffset={sideOffset}
        hideWhenDetached={hideWhenDetached && !sheet}
        data-phone-sheet={sheet ? (phoneDrop ? "drop" : "") : undefined}
        {...(phoneDrop ? {} : swipe)}
        onPointerDownOutside={(event) => {
          // On a phone a tap outside an open menu only closes it, as on
          // iOS: the click that follows does not reach what is under it.
          if (sheet) swallowNextClick()
          onPointerDownOutside?.(event)
        }}
        className={cn(
          "z-(--z-popover) outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=top]:slide-in-from-bottom-2",
          className
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
})
DropdownMenuContent.displayName = DropdownMenuPrimitive.Content.displayName

/** Drops the click that ends the press which closed a menu. */
function swallowNextClick() {
  const stop = (event: MouseEvent) => { event.preventDefault(); event.stopPropagation(); done() }
  const done = () => { window.removeEventListener("click", stop, true); window.clearTimeout(timer) }
  window.addEventListener("click", stop, true)
  const timer = window.setTimeout(done, 700)
}

const DropdownMenuItem = DropdownMenuPrimitive.Item

const DropdownMenuSeparator = DropdownMenuPrimitive.Separator

function DropdownMenuSub(props: React.ComponentProps<typeof DropdownMenuPrimitive.Sub>) {
  return <PhoneSub Sub={DropdownMenuPrimitive.Sub} props={props} />
}

const DropdownMenuSubTrigger = DropdownMenuPrimitive.SubTrigger

const DropdownMenuSubContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.SubContent>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.SubContent>
// A submenu stands 4px off its parent panel (the offset is measured from
// the row, inside the panel's p-1), and its first row lines up with that row.
>(({ className, sideOffset = 8, alignOffset = -4, hideWhenDetached = true, children, ...props }, ref) => {
  const sheet = usePhoneSheet()
  const swipe = usePhoneSheetSwipe(sheet)
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.SubContent
        ref={ref}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        hideWhenDetached={hideWhenDetached && !sheet}
        data-phone-sheet={sheet ? "" : undefined}
        data-phone-sub={sheet ? "" : undefined}
        {...swipe}
        className={cn(
          "z-(--z-popover) outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          className
        )}
        {...props}
      >
        {sheet ? <PhoneSheetBack Item={DropdownMenuPrimitive.Item} /> : null}
        {children}
      </DropdownMenuPrimitive.SubContent>
    </DropdownMenuPrimitive.Portal>
  )
})
DropdownMenuSubContent.displayName = DropdownMenuPrimitive.SubContent.displayName

export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
}
