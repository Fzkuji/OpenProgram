import type { ChatMsg } from "./types";

/** Persisted rows keep their value; live/legacy rows missing one get a local time. */
export function validMessageTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function stampList(
  list: ChatMsg[] | undefined,
  fallback: number,
): ChatMsg[] | undefined {
  if (!list) return list;
  let changed = false;
  const next = list.map((child) => {
    const stamped = withMessageTimestamp(child, fallback);
    if (stamped !== child) changed = true;
    return stamped;
  });
  return changed ? next : list;
}

/** Stamp a row (and nested cards) once. Already-valid subtrees keep identity. */
export function withMessageTimestamp(msg: ChatMsg, fallback = Date.now()): ChatMsg {
  const timestamp = validMessageTimestamp(msg.timestamp)
    ? msg.timestamp
    : fallback;
  const runtimeChildren = stampList(msg.runtimeChildren, timestamp);
  const attachCards = stampList(msg.attachCards, timestamp);
  if (
    timestamp === msg.timestamp
    && runtimeChildren === msg.runtimeChildren
    && attachCards === msg.attachCards
  ) {
    return msg;
  }
  return { ...msg, timestamp, runtimeChildren, attachCards };
}
