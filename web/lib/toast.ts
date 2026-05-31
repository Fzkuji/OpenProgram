/**
 * Tiny app-wide transient toast.
 *
 * There was no toast component in the project (only a one-off inline
 * bubble in the sessions list and a legacy `window.__toast` global that
 * may not exist), so this is the shared one: call `showToast(...)` from
 * anywhere and `<ToastHost/>` (mounted once in the top bar) renders a
 * top-centred bubble that fades itself out after a few seconds.
 */

export type ToastTone = "info" | "warn" | "error";

export interface ToastDetail {
  message: string;
  tone?: ToastTone;
  /** Auto-dismiss after this many ms (default 3500). */
  duration?: number;
}

export const TOAST_EVENT = "op:toast";

/** Fire a transient toast. No-op during SSR. */
export function showToast(
  message: string,
  tone: ToastTone = "info",
  duration = 3500,
): void {
  if (typeof window === "undefined" || !message) return;
  window.dispatchEvent(
    new CustomEvent<ToastDetail>(TOAST_EVENT, {
      detail: { message, tone, duration },
    }),
  );
}
