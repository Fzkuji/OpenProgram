/** What a pane's model settings entry was read for: the branch it shows,
 *  that branch's Agent, and the store's settings epoch (it moves when some
 *  conversation's model changed and the news did not say which). */
export function settingsShownFor(branchId: string | null, agentId: string | null, epoch: number): string {
  return `${branchId ?? ""}|${agentId ?? ""}|${epoch}`;
}

/** Whether a pane must read its conversation's model settings again.
 *
 *  A focused pane follows the focused reads. An unfocused one reads when it
 *  has no entry yet, or when what its entry was read for (``shownFor``, see
 *  ``settingsShownFor``) moved since. It keeps showing the entry meanwhile. */
export function settingsReadNeeded(
  sessionId: string | null, focused: boolean, hasEntry: boolean, shownFor: string,
  lastRead: string | undefined,
): boolean {
  if (!sessionId || focused) return false;
  if (!hasEntry) return true;
  return lastRead !== undefined && lastRead !== shownFor;
}
