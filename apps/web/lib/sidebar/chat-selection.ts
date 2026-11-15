/** Several sidebar chats picked at once, as Finder does: Cmd/Ctrl-click
 *  adds or removes one, Shift-click takes the run from the last picked row.
 *  The open chat counts as already picked when a selection starts. */
export interface ChatSelection {
  ids: string[];
  anchor: string | null;
}

export const EMPTY_SELECTION: ChatSelection = { ids: [], anchor: null };

export function pickChat(
  order: readonly string[],
  selection: ChatSelection,
  current: string | null,
  id: string,
  mode: "toggle" | "range",
): ChatSelection {
  const seed = selection.ids.length ? selection.ids : current && current !== id && order.includes(current) ? [current] : [];
  if (mode === "range") {
    const anchor = selection.anchor ?? seed[0] ?? id;
    const from = order.indexOf(anchor);
    const to = order.indexOf(id);
    if (from < 0 || to < 0) return { ids: [id], anchor: id };
    const [start, end] = from <= to ? [from, to] : [to, from];
    return { ids: order.slice(start, end + 1), anchor };
  }
  const ids = seed.includes(id) ? seed.filter((other) => other !== id) : [...seed, id];
  return { ids, anchor: id };
}
