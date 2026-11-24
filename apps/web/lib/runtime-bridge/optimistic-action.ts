/**
 * optimisticAction — the shared 0ms-feedback helper.
 *
 * Interaction-feedback policy (docs/design/ui/interaction-feedback.md):
 * every click that starts something slower than ~100ms flips a visible
 * transitional state IMMEDIATELY (client-side, 0ms), real data backfills
 * when the server confirms, and a failure/timeout rolls the state back with
 * a visible error.
 *
 * This helper owns layer 1 (the 0ms flip) + the failure/timeout rollback.
 * The success backfill is layer 3 — it arrives on its own (a `load_session`
 * reload that replaces the message store, or a stream that fills the card),
 * so the helper only needs to STOP reverting once it sees the world moved
 * on. ``settled()`` is that check: return true once the real data has
 * landed (message gone from the store, tree repopulated, status advanced…),
 * and the armed revert timer is cancelled.
 *
 * Used by ≥3 surfaces (Function-call Retry, version switcher / branch
 * checkout) that all share the pattern "flip now, server confirms via a
 * load_session reload, revert on timeout". Keep it dumb: one deadline
 * `setTimeout`, and `settled()` asked again each time the store it reads
 * changes; no bookkeeping store, no per-surface subclass.
 */

/** Default rollback window for a control action (checkout / retry). Chosen
 *  well above the ~0.2s pre-created-node hydrate and the typical
 *  load_session round-trip, low enough that a genuinely stuck action
 *  surfaces to the user rather than hanging on a lie. */
export const OPTIMISTIC_TIMEOUT_MS = 10_000;

export interface OptimisticActionArgs {
  /** Apply the 0ms transitional state (spinner, target-selected, …). */
  apply: () => void;
  /** True once the real data has superseded the optimistic state — the
   *  helper stops watching and never reverts. Asked on each change
   *  `subscribe` reports and once when the timeout fires. */
  settled: () => boolean;
  /** Hear each change of the store `settled` reads (a zustand store's
   *  `subscribe`); returns the unsubscribe. */
  subscribe: (onChange: () => void) => () => void;
  /** Undo ``apply`` — restore the pre-click state. Runs only if the
   *  timeout fires while ``settled()`` is still false. */
  revert: () => void;
  /** Shown (toast) when the action times out unresolved. */
  onTimeoutMessage?: string;
  /** Override the rollback window (ms). */
  timeoutMs?: number;
}

type ToastFn = (message: string, opts?: { tone?: "info" | "warn" | "error" }) => void;

/**
 * Run an optimistic action: apply the transitional state now, then watch
 * for it to settle. If it hasn't settled by ``timeoutMs``, revert and toast.
 *
 * ``toast`` is injected (not imported) so this module has no dependency on
 * the toast layer — callers pass ``showToast``. Returns a canceller the
 * caller can invoke if it learns the action resolved through another path.
 */
export function optimisticAction(
  args: OptimisticActionArgs,
  toast?: ToastFn,
): () => void {
  const timeoutMs = args.timeoutMs ?? OPTIMISTIC_TIMEOUT_MS;
  args.apply();

  let done = false;
  let unsubscribe = () => {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  const finish = () => {
    if (done) return;
    done = true;
    unsubscribe();
    clearTimeout(timer);
  };

  // Stop the revert the moment the backfill lands, without waiting out
  // the window: the store says when it changed.
  unsubscribe = args.subscribe(() => {
    if (!done && args.settled()) finish();
  });

  timer = setTimeout(() => {
    if (done) return;
    if (args.settled()) {
      finish();
      return;
    }
    finish();
    args.revert();
    if (args.onTimeoutMessage && toast) {
      toast(args.onTimeoutMessage, { tone: "warn" });
    }
  }, timeoutMs);

  return finish;
}
