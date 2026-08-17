/**
 * Format the trailing ": provider · model · sid8" suffix on the Chat
 * / Exec agent badges. Mirrors what the legacy `updateAgentBadges`
 * produced so the visible string stays identical.
 */
export function formatAgentDetails(
  provider?: string,
  model?: string,
  sessionId?: string,
): string {
  const parts: string[] = [];
  parts.push(provider || "?");
  if (model) parts.push(model);
  if (sessionId) parts.push(sessionId.slice(0, 8));
  return ": " + parts.join(" · ");
}
