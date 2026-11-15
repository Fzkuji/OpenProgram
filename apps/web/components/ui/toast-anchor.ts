/**
 * Where a toast centres: over the centre area the user is working in,
 * never just over the window.
 *
 * - Split view (`.center-col[data-split]`): the pane showing the toast's
 *   session when it has one and that session is in a pane, otherwise the
 *   pane holding the active tab.
 * - One pane, a zoomed pane or a non-chat page: the centre column, the
 *   card between the sidebars, so rails of different widths do not push
 *   the bubble off the content's centre.
 * - Canvas full screen, or no visible centre column: the window (null).
 *
 * The host reads this once when a toast shows and follows the anchor's
 * size with a ResizeObserver while a toast is visible (toast-host.tsx).
 */

export interface ToastTabsSnapshot {
  activeId: string | null;
  tabs: ReadonlyArray<{ id: string; sessionId?: string | null }>;
}

/** Pane elements carry their tab id in one of these attributes. */
const PANE_IDS = ["data-center-tab-id", "data-file-pane-id"] as const;

function shown(el: Element): boolean {
  const box = el.getBoundingClientRect();
  return box.width > 0 && box.height > 0;
}

/** The element whose box the toast centres over, or null for the window. */
export function toastAnchor(
  doc: Document,
  state: ToastTabsSnapshot,
  sessionId?: string | null,
): HTMLElement | null {
  if (doc.documentElement.hasAttribute("data-canvas-fullscreen")) return null;
  const column = [...doc.querySelectorAll<HTMLElement>(".center-col")].find(shown);
  if (!column) return null;
  if (!column.hasAttribute("data-split")) return column;

  const panes = [...column.querySelectorAll<HTMLElement>(PANE_IDS.map((name) => `[${name}]`).join(", "))]
    .filter(shown);
  const paneOf = (tabId: string) =>
    panes.find((pane) => PANE_IDS.some((name) => pane.getAttribute(name) === tabId));
  if (sessionId) {
    for (const tab of state.tabs) {
      if (tab.sessionId !== sessionId) continue;
      const pane = paneOf(tab.id);
      if (pane) return pane;
    }
  }
  return (state.activeId && paneOf(state.activeId)) || column;
}

/** The bubble's horizontal centre in window px: the anchor's centre, kept
 *  far enough from the window's edges that a bubble `bubbleWidth` wide
 *  stays `margin` px inside them. An anchor with no width counts as the
 *  window. */
export function toastCentreX(
  anchor: { left: number; width: number } | null,
  viewportWidth: number,
  bubbleWidth: number,
  margin = 8,
): number {
  const middle = viewportWidth / 2;
  // A collapsed or detached anchor (its pane just closed) means the window.
  const centre = anchor && anchor.width > 0 ? anchor.left + anchor.width / 2 : middle;
  const half = bubbleWidth / 2 + margin;
  if (half * 2 >= viewportWidth) return middle;
  return Math.min(Math.max(centre, half), viewportWidth - half);
}
