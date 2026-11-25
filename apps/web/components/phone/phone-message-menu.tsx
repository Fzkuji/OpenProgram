"use client";

/**
 * A message's actions on the phone: a long press on it opens them as a
 * sheet, where the desktop shows a row of icons under each message on
 * hover (bubbles/message-actions.tsx). The rows are that bar's own
 * buttons, named by their labels, so each action runs the same code and
 * one the bar disables is disabled here. Happy and Paseo keep their
 * transcripts free of per-message buttons the same way; the press arms
 * after 450ms (Paseo) with a medium tap (Happy).
 */

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { haptic } from "@/lib/mobile/bridge";
import { cn } from "@/lib/utils";

const HOLD_MS = 450;
const SLOP = 8;

type Action = { label: string; disabled: boolean; button: HTMLButtonElement };

/** The action bar's buttons of one message, in their order. */
function actionsOf(message: HTMLElement): Action[] {
  const buttons = message.querySelectorAll<HTMLButtonElement>(
    ".message-actions-footer .message-action-btn, .message-actions-footer .message-nav-btn",
  );
  return [...buttons]
    .filter((button) => button.closest(".message") === message)
    .map((button) => ({ label: button.getAttribute("aria-label") ?? "", disabled: button.disabled, button }))
    .filter((action) => action.label);
}

export function PhoneMessageMenu({ root, children }: { root: RefObject<HTMLElement | null>; children: ReactNode }) {
  const [actions, setActions] = useState<Action[] | null>(null);
  const pressed = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let timer = 0;
    let x = 0;
    let y = 0;
    let fired = false;
    const cancel = () => { window.clearTimeout(timer); timer = 0; };
    const onStart = (event: TouchEvent) => {
      fired = false;
      if (event.touches.length !== 1) return cancel();
      const target = event.target as HTMLElement;
      // Controls, the editor and the composer keep their own press.
      if (target.closest("button, a, input, textarea, [contenteditable], .tl, [data-composer-input-area]")) return;
      const message = target.closest<HTMLElement>(".message[data-msg-id]");
      if (!message || message.classList.contains("is-editing")) return;
      x = event.touches[0].clientX;
      y = event.touches[0].clientY;
      cancel();
      timer = window.setTimeout(() => {
        timer = 0;
        const found = actionsOf(message);
        if (!found.length) return;
        fired = true;
        // A user bubble is also a context-menu trigger (rewind), whose own
        // touch long press would open a second menu at 700ms.
        message.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerType: "touch" }));
        haptic("medium");
        pressed.current = message;
        message.setAttribute("data-phone-pressed", "");
        setActions(found);
      }, HOLD_MS);
    };
    const onMove = (event: TouchEvent) => {
      if (!timer) return;
      const touch = event.touches[0];
      if (Math.hypot(touch.clientX - x, touch.clientY - y) > SLOP) cancel();
    };
    // The lift that ends a long press is not also a tap.
    const onEnd = (event: TouchEvent) => {
      cancel();
      if (fired) event.preventDefault();
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: true });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", cancel);
    return () => {
      cancel();
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", cancel);
    };
  }, [root]);

  const close = () => {
    pressed.current?.removeAttribute("data-phone-pressed");
    pressed.current = null;
    setActions(null);
  };

  return (
    <>
      {children}
      <DropdownMenu open={!!actions} onOpenChange={(open) => { if (!open) close(); }}>
        <DropdownMenuTrigger asChild>
          <span className="phone-message-menu-anchor" aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent className={MENU_PANEL} onCloseAutoFocus={(event) => event.preventDefault()}>
          {(actions ?? []).map((action) => (
            <DropdownMenuItem
              key={action.label}
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              disabled={action.disabled}
              // After the sheet closes, so an action that opens its own
              // dialog or editor is not closed with it.
              onSelect={() => { window.setTimeout(() => action.button.click(), 0); }}
            >
              {action.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
