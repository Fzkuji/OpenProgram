/**
 * The floating preview folding into the composer's web page chip as it
 * closes, and unfolding out of it as it opens again, the way a macOS
 * window minimizes into the Dock. The frame itself moves; its page shows
 * as a still picture meanwhile (web-tab-pip-surface.tsx, hold reason
 * `motion`), because the native page cannot fade or scale.
 */

/** Closing gathers pace into the chip; opening leaves it quickly and
 *  settles at its place. */
export const PIP_FOLD = { duration: 0.24, ease: [0.4, 0, 0.7, 0.2] } as const;
export const PIP_UNFOLD = { duration: 0.28, ease: [0.16, 1, 0.3, 1] } as const;
/** How long a hold waits for a fresh picture of the page before it takes
 *  the live page down over the last one (web-tab-pip-surface.tsx). */
export const PIP_PICTURE_WAIT_MS = 150;
/** How long a page laid out at a new preview size gets to finish laying
 *  itself out from script before it shows. */
export const PIP_RELAYOUT_MS = 250;
/** A surface that never answers a hold does not hold up a close. */
export const PIP_HOLD_WAIT_MS = 400;

export function pipMotionAllowed(): boolean {
  if (typeof document === "undefined") return false;
  return !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
}

/** Where the preview of page `tabId` folds to: the composer's web page chip
 *  that names that page (its data-web-surface-chip is the page's tab id),
 *  the one on screen. In a split view a chat's chip names the page in the
 *  other pane, and folding into it sent the preview into another page;
 *  with no chip for its own page the preview fades where it is. A split
 *  view also keeps the single-pane chat mounted but hidden, its chip 0×0;
 *  among visible chips the one in the preview's own pane wins. */
export function foldTarget(tabId: string | null): HTMLElement | null {
  if (typeof document === "undefined" || !tabId) return null;
  const chips = [...document.querySelectorAll<HTMLElement>("[data-web-surface-chip]")].filter(chip => {
    if (chip.dataset.webSurfaceChip !== tabId) return false;
    const box = chip.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  });
  const owner = document.querySelector<HTMLElement>('[data-pip="true"]')?.dataset.pipOwnerTabId;
  return chips.find(chip => !!owner && chip.closest<HTMLElement>("[data-center-tab-id]")?.dataset.centerTabId === owner)
    ?? chips[0] ?? null;
}

/** The transform that lays `frame`, untransformed, over `target`. */
export function towardTarget(frame: HTMLElement, target: HTMLElement): string {
  const from = frame.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  const scale = Math.max(0.06, Math.min(to.width / from.width, to.height / from.height));
  const dx = to.left + to.width / 2 - (from.left + from.width / 2);
  const dy = to.top + to.height / 2 - (from.top + from.height / 2);
  return `translate(${dx}px, ${dy}px) scale(${scale})`;
}
