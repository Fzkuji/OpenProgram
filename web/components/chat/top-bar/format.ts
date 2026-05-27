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

/**
 * Whether a (provider, model) pair appears in the live "enabled models"
 * list returned by ``/api/models/enabled``.
 *
 * Session metadata stores the model in either the bare form
 * (``"gpt-5.5"``) or the runtime-prefixed form
 * (``"claude-code:claude-sonnet-4"``); the enabled-models API uses
 * the bare form in ``id`` plus the provider in ``provider``. Match
 * both shapes so we don't flag a usable model as unavailable just
 * because the session was persisted with the prefix.
 *
 * When ``enabledModels`` is ``undefined`` (still loading or fetch
 * failed) we return ``true`` to avoid flashing the warning state on
 * page load.
 */
export function isModelAvailable(
  provider: string | undefined,
  model: string | undefined,
  enabledModels: { id: string; provider: string }[] | undefined,
): boolean {
  if (enabledModels === undefined) return true;
  if (!provider || !model) return false;
  const prefix = provider + ":";
  const rawId = model.startsWith(prefix) ? model.slice(prefix.length) : model;
  return enabledModels.some(
    (m) => m.provider === provider && (m.id === rawId || m.id === model),
  );
}
