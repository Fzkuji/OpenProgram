/**
 * The hover motion every icon in a control shares: hovering the control
 * makes its icon pop, 1.12 times its size, on one spring.
 *
 * An icon finds the control from inside: the nearest clickable ancestor
 * (HOVER_HOST), or the icon itself when it sits in none. Solar glyphs
 * (../solar-icons/solar-icon.tsx) and Agent characters
 * (../avatar/bot/bot-avatar.ts) both use it, so a chip or menu row moves
 * its picture the same way whatever the picture is.
 */
import type { Transition, Variants } from "framer-motion";

/** The elements whose hover drives the icon inside them. */
export const HOVER_HOST =
  'button, a, summary, [role="button"], [role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"], .folder-pill-seg, .runtime-badge, .status-badge';

export const HOVER_SPRING: Transition = { type: "spring", stiffness: 420, damping: 16, mass: 0.8 };

export const HOVER_POP_SCALE = 1.12;

export const HOVER_POP: Variants = { normal: { scale: 1 }, animate: { scale: HOVER_POP_SCALE } };

/** The box of a filled icon in the composer and the popups it opens: the
 *  drawing's ink is scaled until its longer side is this many px. */
export const FILL_BOX = 16;

/** The box a filled icon grows to on its control's hover, in px. */
export const FILL_HOVER_BOX = 18;

/** The hover scale of a filled icon: its 16 px box becomes exactly 18 px. */
export const HOVER_FILL_SCALE = FILL_HOVER_BOX / FILL_BOX;

/** The pop of a filled icon (opt-in; every other icon keeps HOVER_POP). */
export const HOVER_POP_FILL: Variants = { normal: { scale: 1 }, animate: { scale: HOVER_FILL_SCALE } };

/** The element whose hover moves the icon `self`. */
export function hoverHostOf(self: Element): Element {
  return self.parentElement?.closest(HOVER_HOST) ?? self;
}

/** Call `enter` and `leave` as the pointer enters and leaves `self`'s
 *  hover host. Returns the function that stops listening. */
export function followHostHover(self: Element, enter: () => void, leave: () => void): () => void {
  const host = hoverHostOf(self);
  host.addEventListener("mouseenter", enter);
  host.addEventListener("mouseleave", leave);
  return () => {
    host.removeEventListener("mouseenter", enter);
    host.removeEventListener("mouseleave", leave);
  };
}
