/**
 * Which character a Bot gets when nobody picked one: the same id always
 * gives the same shape and colour.
 */
import { BOT_COLOR_IDS, BOT_SHAPES, type BotColor, type BotShape } from "./catalog";

export interface BotLook {
  shape: BotShape;
  color: BotColor;
}

/** The built-in Agents wear a character that fits their job. */
const BUILT_IN: Record<string, BotLook> = {
  main: { shape: "cloud", color: "blue" },
  butler: { shape: "cat", color: "teal" },
  utility: { shape: "lightbulb", color: "yellow" },
  planner: { shape: "controller", color: "violet" },
  decision: { shape: "star", color: "orange" },
  "chief-of-staff": { shape: "chat", color: "blue" },
  "spec-writer": { shape: "book", color: "violet" },
  "bug-reproducer": { shape: "magnifier", color: "orange" },
  "pr-producer": { shape: "rocket", color: "red" },
  "pr-verifier": { shape: "shield", color: "teal" },
  "research-desk": { shape: "brain", color: "pink" },
  "inbox-triage": { shape: "mug", color: "yellow" },
  "career-scout": { shape: "camera", color: "green" },
  "knowledge-keeper": { shape: "sprout", color: "green" },
  writer: { shape: "paint-brush", color: "blue" },
};

/** FNV-1a, 32 bit. */
function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

export function botLookFor(id: string): BotLook {
  const known = Object.hasOwn(BUILT_IN, id) ? BUILT_IN[id] : undefined;
  if (known) return known;
  const h = hash(id);
  return {
    shape: BOT_SHAPES[h % BOT_SHAPES.length],
    // The high bits, so shape and colour do not move together.
    color: BOT_COLOR_IDS[(h >>> 16) % BOT_COLOR_IDS.length],
  };
}
