/**
 * Which character each agent on the graph wears (dag/rendering.md §4).
 *
 * A reply is the conversation's Agent speaking, so it wears that Agent's
 * character; the graph's nodes carry no Agent id, so the view that knows
 * the conversation (components/chat/dag/dag-view.tsx) reports it here.
 * A sub-agent wears the character its name gives (the rule that pictures
 * an Agent without an avatar), so different sub-agents look different and
 * the same one looks the same wherever it shows.
 */
import { botLookFor } from "@/components/avatar/bot/identity";

/** The Main Agent's character until the view reports the conversation's. */
const FALLBACK = botLookFor("main").shape;

let mainShape: string = FALLBACK;

export function mainAgentShape(): string {
  return mainShape;
}

/** Record the conversation Agent's character; true when it changed. */
export function setMainAgentShape(shape: string | null): boolean {
  const next = shape || FALLBACK;
  if (next === mainShape) return false;
  mainShape = next;
  return true;
}

/** The character a sub-agent of this name wears. */
export function subAgentShape(name: string): string {
  return botLookFor(name.trim() || "sub-agent").shape;
}
