import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type React from "react";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Keyboard activation for elements that carry `role="button"` on a
 * non-button tag. A real <button> fires onClick for Enter and Space by
 * itself; a div with a role does not, so a keyboard user simply cannot
 * reach the action. Pair this with `tabIndex={0}`:
 *
 *   <div role="button" tabIndex={0} onClick={go} onKeyDown={activateOnKey(go)}>
 *
 * ponytail: exists only because these rows are divs for layout reasons.
 * A row that can become a real <button> should — then delete the call.
 */
export function activateOnKey(action: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    // Space would scroll the list out from under the row otherwise.
    e.preventDefault();
    action();
  };
}
