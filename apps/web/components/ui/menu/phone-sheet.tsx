"use client";

/**
 * Menus on a touch phone are sheets: a panel that rises from the bottom of
 * the screen, 8px off its edges and rounded in parallel with its corners,
 * over a dimmed page (app/styles/base/phone.css, "Menus as sheets"). A
 * submenu does not fly out beside its row, where a phone has no room: it
 * opens as a sheet over its parent, with a Back row that returns to it.
 * A swipe down on a sheet that is scrolled to its top closes the menu.
 *
 * The radix wrappers (ui/dropdown-menu.tsx, ui/context-menu.tsx) mark their
 * panels with `data-phone-sheet` and wrap each Sub in PhoneSub, so every
 * menu built on them behaves this way; the desktop is unchanged.
 */

import * as React from "react";
import { ChevronLeft } from "lucide-react";

import { useTouchPhone } from "@/lib/layout/phone";
import { useTranslation } from "@/lib/i18n";
import { MENU_ITEM_STATES, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";

export { useTouchPhone as usePhoneSheet };

/** The open state of the submenu around a SubContent, so its Back row can
 *  close it. */
const SubClose = React.createContext<(() => void) | null>(null);

type SubProps = {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: React.ReactNode;
};

/** A radix Sub whose open state the Back row can reach. A caller's own
 *  `open` / `onOpenChange` still lead. */
export function PhoneSub<P extends SubProps>({ Sub, props }: {
  Sub: React.ComponentType<P>;
  props: P;
}) {
  const [own, setOwn] = React.useState(props.defaultOpen ?? false);
  const open = props.open ?? own;
  const setOpen = React.useCallback((next: boolean) => {
    if (props.open === undefined) setOwn(next);
    props.onOpenChange?.(next);
  }, [props]);
  const close = React.useCallback(() => setOpen(false), [setOpen]);
  return (
    <SubClose.Provider value={close}>
      <Sub {...props} open={open} onOpenChange={setOpen} />
    </SubClose.Provider>
  );
}

/** The first row of a submenu's sheet: back to the menu it came from. */
export function PhoneSheetBack({ Item }: {
  Item: React.ComponentType<{
    className?: string;
    onSelect?: (event: Event) => void;
    children?: React.ReactNode;
  }>;
}) {
  const close = React.useContext(SubClose);
  const { text } = useTranslation();
  if (!close) return null;
  return (
    <Item
      className={cn(itemCls(false), MENU_ITEM_STATES, "phone-sheet-back col-span-full")}
      onSelect={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <ChevronLeft className="size-5 shrink-0" aria-hidden="true" />
      <span>{text("Back", "返回")}</span>
    </Item>
  );
}

/** Touch handlers for a sheet: from its scrolled-to-top position the sheet
 *  follows a finger pulling it down, and closes when let go 64px down (it
 *  settles back short of that), the way a phone's sheets close. A sheet
 *  that is not a radix layer passes its own `close`. */
export function usePhoneSheetSwipe(enabled: boolean, close?: () => void) {
  const start = React.useRef<{ y: number; top: boolean; dy: number } | null>(null);
  return React.useMemo(() => enabled ? {
    onTouchStart(event: React.TouchEvent<HTMLElement>) {
      start.current = { y: event.touches[0].clientY, top: event.currentTarget.scrollTop <= 0, dy: 0 };
    },
    onTouchMove(event: React.TouchEvent<HTMLElement>) {
      const from = start.current;
      if (!from?.top) return;
      from.dy = Math.max(0, event.touches[0].clientY - from.y);
      const el = event.currentTarget;
      el.style.transition = "none";
      el.style.transform = from.dy ? `translateY(${from.dy}px)` : "";
    },
    onTouchEnd(event: React.TouchEvent<HTMLElement>) {
      const from = start.current;
      start.current = null;
      if (!from?.top) return;
      const el = event.currentTarget;
      if (from.dy < 64) {
        el.style.transition = "transform 200ms cubic-bezier(0.32, 0.72, 0, 1)";
        el.style.transform = "";
        return;
      }
      // The closing animation (phone.css) runs on from where the finger
      // left the sheet: its keyframes name only the end.
      if (close) close();
      else closeAllSheets();
    },
  } : {}, [enabled, close]);
}

/** An Escape closes the topmost menu layer once React has let the one
 *  above it go, so the sheets close one frame apart. */
function closeAllSheets(left = 6) {
  if (left === 0 || !document.querySelector("[data-phone-sheet][data-state='open']")) {
    // The menu hands focus back to its button as after a key press, which
    // draws the keyboard's focus ring; a finger closed it, so none.
    (document.activeElement as HTMLElement | null)?.blur?.();
    return;
  }
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  window.requestAnimationFrame(() => closeAllSheets(left - 1));
}
