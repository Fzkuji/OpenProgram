/**
 * agent-style — deterministic colour + initial for an agent_id.
 *
 * Same agent_id always lands on the same palette slot, so multi-agent
 * chats stay visually consistent across reloads. The palette mirrors
 * the lane colours used by the history-graph so the chat avatar and
 * the DAG branch line read as the same agent.
 */

// Kept in sync with web/components/right-sidebar/branches-panel.tsx
// LANE_COLORS — change both together so the chat avatar matches the
// branch dot in the right rail.
const PALETTE = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

// FNV-1a 32-bit. Cheap, stable, no deps. We just want N-bucketed
// hashing of strings — anything sub-millisecond is fine.
function _hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h >>> 0;
}

/** Pick a palette colour for an agent_id. Empty / falsy → null,
 *  let the caller use the existing neutral avatar. */
export function agentColor(agentId: string | undefined | null): string | null {
  if (!agentId) return null;
  return PALETTE[_hash(agentId) % PALETTE.length];
}

/** One-character avatar text for an agent. Picks the first
 *  letter / digit of the id, uppercased. Empty / falsy → "A"
 *  (the legacy "Agentic" initial) so we don't regress when no
 *  agent_id is stamped. */
export function agentInitial(agentId: string | undefined | null): string {
  if (!agentId) return "A";
  for (const ch of agentId) {
    if (/[a-zA-Z0-9]/.test(ch)) return ch.toUpperCase();
  }
  return "A";
}

/** Display name for an agent. "main" is the default profile so we
 *  keep showing "Agentic" for it (avoids gratuitously renaming the
 *  most common case). Other ids show as-is. */
export function agentDisplayName(agentId: string | undefined | null): string {
  if (!agentId || agentId === "main") return "Agentic";
  return agentId;
}
