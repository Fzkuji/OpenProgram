/** Which block of a reply owns each thing that hangs off the reply.
 *
 *  A spawned agent's card and a tool's call tree belong to the tool call that
 *  made them. The server decides which (including the evidence old records
 *  need) and names it on the child; a card with no owner is drawn anyway. */
import type { AssistantBlock, ChatMsg } from "../../session-store/types.ts";
import { isProviderSearch } from "./web-sources.ts";

/** Tools that hang a sub-agent's card off the reply. `send_message` runs the
 *  target branch a turn and leaves a card, but only sometimes. */
export const SPAWNING_TOOL_NAMES = new Set(["agent", "task", "send_message"]);

/** The part of a call tree this module reads. */
export interface OwnedRoot {
  name?: string;
  tool_call_id?: string;
  blockId?: string | null;
}

export interface OwnedChildren<Root extends OwnedRoot = OwnedRoot> {
  /** Each block's sub-agent card, by block id. */
  cards: Map<string, ChatMsg>;
  /** Cards no block owns. Drawn after the steps, never lost. */
  unpairedCards: ChatMsg[];
  /** Agents from another conversation or attached by hand. They keep their card. */
  references: ChatMsg[];
  /** Each block's runtime child, by block id. */
  runtimeChildren: Map<string, ChatMsg>;
  unpairedRuntimeChildren: ChatMsg[];
  /** Each block's call tree from the caller chain, by block id. */
  callRoots: Map<string, Root>;
}

/** A stable id for every block, held across a rebuild of the list. Text and
 *  thinking count runs of their type, so a run the final write splits keeps
 *  its first id. An id the server gave counts like one computed here, so a
 *  block streamed after a mid-turn read is numbered as a cold read numbers it. */
export function blockIds(blocks: readonly AssistantBlock[]): string[] {
  const runs = new Map<string, number>();
  const seen = new Map<string, number>();
  let inRun: { type: string; id: string; parts: number } | null = null;
  const given = new Set<string>();
  const named = (id: string) => {
    given.add(id);
    return id;
  };
  return blocks.map((block) => {
    // The server names its blocks; a live block keeps that name when the read arrives.
    if (block.id && !given.has(block.id)) {
      const counted = countGivenId(block.id, block.type, runs, seen);
      inRun = block.type === "text" || block.type === "thinking" ? counted : null;
      return named(block.id);
    }
    const type = block.type;
    if (type === "tool" && block.tool_call_id) {
      inRun = null;
      return named(unique(block.tool_call_id, seen));
    }
    if (type === "steering" && block.message_id) {
      inRun = null;
      return named(unique(`steering:${block.message_id}`, seen));
    }
    if (inRun?.type === type && (type === "text" || type === "thinking")) {
      inRun.parts += 1;
      return named(`${inRun.id}.${inRun.parts}`);
    }
    const n = runs.get(type) ?? 0;
    runs.set(type, n + 1);
    const id = `${type}:${n}`;
    inRun = { type, id, parts: 0 };
    return named(id);
  });
}

/** Advance the counters past an id the server gave, and say which run it is in. */
function countGivenId(
  id: string, type: string, runs: Map<string, number>, seen: Map<string, number>,
): { type: string; id: string; parts: number } | null {
  const repeat = /^(.*)#(\d+)$/.exec(id);
  const base = repeat?.[1] ?? id;
  seen.set(base, Math.max(seen.get(base) ?? 0, Number(repeat?.[2] ?? 0) + 1));
  const run = /^([a-z]+):(\d+)(?:\.(\d+))?$/.exec(id);
  if (!run || run[1] !== type) return null;
  const n = Number(run[2]);
  runs.set(type, Math.max(runs.get(type) ?? 0, n + 1));
  return { type, id: `${type}:${n}`, parts: Number(run[3] ?? 0) };
}

function unique(id: string, seen: Map<string, number>): string {
  const count = seen.get(id) ?? 0;
  seen.set(id, count + 1);
  return count ? `${id}#${count}` : id;
}

/** Works out who owns each child of a reply drawing ``blocks``: the block
 *  the server named (``blockId``, projected in transcript/children.py). A
 *  child no block of this reply owns is unpaired and drawn after the steps. */
export function ownedChildren<Root extends OwnedRoot>(
  blocks: readonly AssistantBlock[],
  ids: readonly string[],
  row: { attachCards?: ChatMsg[]; runtimeChildren?: ChatMsg[]; callRoots?: Root[] },
): OwnedChildren<Root> {
  const known = new Set(blocks.flatMap((block, index) =>
    block.type === "tool" && !isProviderSearch(block) ? [ids[index]] : []));
  const placed = <Child extends { blockId?: string | null }>(children: readonly Child[]) => {
    const owned = new Map<string, Child>();
    const unowned: Child[] = [];
    for (const child of children) {
      if (child.blockId && known.has(child.blockId) && !owned.has(child.blockId)) owned.set(child.blockId, child);
      else unowned.push(child);
    }
    return { owned, unowned };
  };
  const cards = placed((row.attachCards ?? []).filter((card) => card.rowKind !== "external_reference"));
  const runtime = placed(row.runtimeChildren ?? []);
  return {
    cards: cards.owned, unpairedCards: cards.unowned,
    references: (row.attachCards ?? []).filter((card) => card.rowKind === "external_reference"),
    runtimeChildren: runtime.owned, unpairedRuntimeChildren: runtime.unowned,
    callRoots: placed(row.callRoots ?? []).owned,
  };
}

/** What a strip's sentence says about the agents it holds. */
export function spawnFacts(cards: readonly ChatMsg[], text: (en: string, zh: string) => string) {
  return {
    spawnNames: cards.map((card) => (card.attach?.label || "").trim()
      || (card.attach?.head_id || "").slice(0, 8)
      || text("sub-agent", "子代理")),
    spawnStates: cards.map((card): string => card.attach?.status || "completed"),
    spawnHeads: cards.map((card) => card.attach?.head_id),
  };
}
