/** Correlate function dispatch receipts without waiting for function completion. */
import { optimisticAction } from './optimistic-action';

type Pending = { sessionId: string; cancel: () => void; restore: () => void; toast: (message: string, opts?: { tone?: 'info' | 'warn' | 'error' }) => void };
const pending = new Map<string, Pending>();

export function settleFunctionRetry(sessionId: string, requestId: unknown, error?: string): boolean {
  if (typeof requestId !== 'string') return false;
  const action = pending.get(requestId);
  if (!action || action.sessionId !== sessionId) return false;
  pending.delete(requestId);
  action.cancel();
  if (error) {
    action.restore();
    action.toast(error, { tone: 'error' });
  }
  return true;
}

export function startFunctionRetry(args: {
  sessionId: string; requestId: string; apply: () => void; restore: () => void;
  settled: () => boolean; send: () => boolean; toast: Pending['toast']; timeoutMessage: string;
}): void {
  const cancel = optimisticAction({
    apply: args.apply,
    settled: () => {
      if (!args.settled()) return false;
      pending.delete(args.requestId);
      return true;
    },
    revert: () => { pending.delete(args.requestId); args.restore(); },
    onTimeoutMessage: args.timeoutMessage,
  }, args.toast);
  pending.set(args.requestId, { sessionId: args.sessionId, cancel, restore: args.restore, toast: args.toast });
  try {
    if (args.send()) return;
  } catch {
    // A closed transport can throw between readyState and send.
  }
  settleFunctionRetry(args.sessionId, args.requestId, 'Connection unavailable — retry was not sent.');
}
