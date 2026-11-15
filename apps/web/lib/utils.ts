import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import type React from "react";

// globals.css adds the font-size steps text-fs-*. Unregistered, twMerge
// reads them as text colours and drops the real colour beside them
// (a primary Button with text-fs-sm lost text-primary-foreground).
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["fs-2xs", "fs-xs", "fs-sm", "fs-base", "fs-chat", "fs-md", "fs-title", "fs-lg", "fs-display"],
    },
  },
});

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
