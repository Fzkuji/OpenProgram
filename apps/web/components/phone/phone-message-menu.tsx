"use client";

/**
 * A message's actions on the phone: a long press on it opens them as
 * Framework7's action sheet (iOS's own menu in the phone app), where the desktop shows a row of icons under each message on
 * hover (bubbles/message-actions.tsx). The rows are that bar's own
 * buttons, named by their labels, so each action runs the same code and
 * one the bar disables is disabled here. Happy and Paseo keep their
 * transcripts free of per-message buttons the same way; the press arms
 * after 450ms (Paseo) with a medium tap (Happy).
 */

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { armNativePress, nativeMenus } from "@/lib/mobile/native-menu";
import { Actions, ActionsButton, ActionsGroup } from "framework7-react";
import { haptic } from "@/lib/mobile/bridge";
import { useTranslation } from "@/lib/i18n";

import { PhonePortal } from "./phone-portal";

const HOLD_MS = 450;
const SLOP = 8;

type Action = { label: string; icon: string; symbol?: string; disabled: boolean; button: HTMLButtonElement };

/** The action bar's buttons of one message, in their order. */
function actionsOf(message: HTMLElement): Action[] {
  const buttons = message.querySelectorAll<HTMLButtonElement>(
    ".message-actions-footer .message-action-btn, .message-actions-footer .message-nav-btn",
  );
  return [...buttons]
    .filter((button) => button.closest(".message") === message)
    // The button's own icon, so the sheet row shows what the bar shows.
    .map((button) => ({ label: button.getAttribute("aria-label") ?? "", icon: button.querySelector("svg")?.outerHTML ?? "", symbol: button.dataset.symbol, disabled: button.disabled, button }))
    .filter((action) => action.label);
}

/** The part of a message on screen, which is what lifts: a long one is
 *  taller than the screen (lib/mobile/native-menu.ts keeps it to the screen). */
function visiblePart(message: HTMLElement, target: HTMLElement): HTMLElement {
  // A turn has a text block between each step: the one pressed.
  return target.closest<HTMLElement>(".message-content") ?? message;
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
      // In the phone app iOS runs the long press: the message lifts and
      // its actions show as the system's menu.
      if (nativeMenus()) {
        // In a turn, its text: a step's row is not the message.
        if (message.classList.contains("assistant") && !target.closest(".message-content")) return;
        const found = actionsOf(message);
        if (!found.length) return;
        armNativePress(visiblePart(message, target), found.map((action) => ({
          title: action.label,
          icon: action.symbol,
          disabled: action.disabled,
          run: () => action.button.click(),
        })), 18, message.classList.contains("assistant") ? 12 : 0, message.closest(".chat-messages"));
        return;
      }
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
      if (nativeMenus()) {
        const touch = event.touches[0];
        if (Math.hypot(touch.clientX - x, touch.clientY - y) > SLOP) armNativePress(null);
        return;
      }
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

  const { text } = useTranslation();
  return (
    <>
      {children}
      <PhonePortal>
        <Actions opened={!!actions} onActionsClosed={close}>
          <ActionsGroup>
            {(actions ?? []).map((action) => (
              <ActionsButton
                key={action.label}
                className={action.disabled ? "disabled" : undefined}
                // After the sheet closes, so an action that opens its own
                // dialog or editor is not closed with it.
                onClick={() => { window.setTimeout(() => action.button.click(), 0); }}
              >
                {action.label}
              </ActionsButton>
            ))}
          </ActionsGroup>
          <ActionsGroup>
            <ActionsButton strong>{text("Cancel", "取消")}</ActionsButton>
          </ActionsGroup>
        </Actions>
      </PhonePortal>
    </>
  );
}
