export function resolveFnFormSessionId(
  currentSessionId: string | null,
  activeChatKey: string | null,
): string | null {
  return activeChatKey ?? currentSessionId;
}

/** A legacy running flag is only safe to clear when the failed dispatch
 *  still owns the visible chat. Per-session runningTasks are cleared by
 *  explicit key independently of this compatibility flag. */
export function shouldClearLegacyRunning(
  dispatchSessionId: string | null,
  activeChatKey: string | null,
  currentSessionId: string | null,
): boolean {
  if (!dispatchSessionId) return false;
  return (activeChatKey ?? currentSessionId) === dispatchSessionId;
}
