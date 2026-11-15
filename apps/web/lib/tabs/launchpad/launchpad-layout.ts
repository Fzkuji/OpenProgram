/**
 * launchpad-layout — where each launcher entry sits on the new-tab
 * Launchpad: explicit pages, as macOS Launchpad keeps them, so taking an
 * item off page 1 does not shift every later page. A page holds up to
 * PAGE_CAPACITY entries; an insert into a full page pushes its last entry
 * onto the next page, the way Launchpad makes room.
 *
 * Every function here is pure: it takes a layout and returns a new one.
 * Item ids are opaque strings ("builtin:files", "app:<id>"); the page
 * decides which ids exist and `reconcile` brings a saved layout in line.
 */

export const COLUMNS = 7;
export const ROWS = 5;
export const PAGE_CAPACITY = COLUMNS * ROWS;

export interface FolderEntry { kind: "folder"; id: string; name: string; items: string[] }
export interface ItemEntry { kind: "item"; id: string }
export type Entry = ItemEntry | FolderEntry;
export interface LaunchpadLayout { pages: Entry[][] }

export const EMPTY_LAYOUT: LaunchpadLayout = { pages: [[]] };

const item = (id: string): ItemEntry => ({ kind: "item", id });

/** Every item id the layout places, folders included. */
export function placedIds(layout: LaunchpadLayout): string[] {
  return layout.pages.flat().flatMap(entry => entry.kind === "folder" ? entry.items : [entry.id]);
}

export function findEntry(layout: LaunchpadLayout, id: string): { page: number; index: number } | null {
  for (let page = 0; page < layout.pages.length; page++) {
    const index = layout.pages[page].findIndex(entry => entry.id === id);
    if (index >= 0) return { page, index };
  }
  return null;
}

export function findFolderOf(layout: LaunchpadLayout, itemId: string): FolderEntry | null {
  for (const entry of layout.pages.flat()) if (entry.kind === "folder" && entry.items.includes(itemId)) return entry;
  return null;
}

/** Folders of one item dissolve into it, empty folders and pages go, and
 *  pages over capacity spill forward. At least one page always remains. */
export function normalize(layout: LaunchpadLayout): LaunchpadLayout {
  const pages: Entry[][] = [];
  let carry: Entry[] = [];
  for (const source of layout.pages) {
    const page = [...carry, ...source.flatMap((entry): Entry[] => {
      if (entry.kind === "item") return [entry];
      if (entry.items.length === 0) return [];
      if (entry.items.length === 1) return [item(entry.items[0])];
      return [entry];
    })];
    carry = page.splice(PAGE_CAPACITY);
    if (page.length) pages.push(page);
  }
  while (carry.length) pages.push(carry.splice(0, PAGE_CAPACITY));
  return { pages: pages.length ? pages : [[]] };
}

/** Drop ids that no longer exist and append new ones after the last entry. */
export function reconcile(layout: LaunchpadLayout, ids: readonly string[]): LaunchpadLayout {
  const live = new Set(ids);
  const seen = new Set<string>();
  const keep = (id: string) => live.has(id) && !seen.has(id) && (seen.add(id), true);
  const pages = layout.pages.map(page => page.flatMap((entry): Entry[] => {
    if (entry.kind === "item") return keep(entry.id) ? [entry] : [];
    return [{ ...entry, items: entry.items.filter(keep) }];
  }));
  const fresh = ids.filter(id => !seen.has(id)).map(item);
  if (fresh.length) pages[pages.length - 1] = [...(pages[pages.length - 1] ?? []), ...fresh];
  return normalize({ pages });
}

/** Take a top-level entry or a folder's item out, returning it as an entry. */
function detach(layout: LaunchpadLayout, id: string): { layout: LaunchpadLayout; entry: Entry } | null {
  const at = findEntry(layout, id);
  if (at) {
    const pages = layout.pages.map(page => [...page]);
    const [entry] = pages[at.page].splice(at.index, 1);
    return { layout: { pages }, entry };
  }
  const folder = findFolderOf(layout, id);
  if (!folder) return null;
  return {
    layout: { pages: layout.pages.map(page => page.map(entry => entry === folder ? { ...folder, items: folder.items.filter(other => other !== id) } : entry)) },
    entry: item(id),
  };
}

/** Put `id` (a top-level entry or an item inside a folder) at `index` on
 *  `page`, counted with `id` already taken out of that page. `page` may be
 *  one past the last page, which opens a new page.
 *  Folders left with a single item dissolve in place, so the page keeps its
 *  shape until the move is normalized. */
export function moveEntry(layout: LaunchpadLayout, id: string, page: number, index: number): LaunchpadLayout {
  const taken = detach(layout, id);
  if (!taken) return layout;
  const pages = taken.layout.pages.map(entries => [...entries]);
  while (pages.length <= page) pages.push([]);
  const target = pages[page];
  target.splice(Math.max(0, Math.min(index, target.length)), 0, taken.entry);
  return normalize({ pages });
}

/** Drop item `dragId` onto `targetId`: into the target folder, or into a new
 *  folder that takes the target's place. A folder never goes into another. */
export function mergeInto(layout: LaunchpadLayout, dragId: string, targetId: string, newFolderId: string): LaunchpadLayout {
  if (dragId === targetId) return layout;
  const dragged = findEntry(layout, dragId);
  if (dragged && layout.pages[dragged.page][dragged.index].kind === "folder") return layout;
  const taken = detach(layout, dragId);
  if (!taken) return layout;
  const pages = taken.layout.pages.map(page => page.map((entry): Entry => {
    if (entry.id !== targetId) return entry;
    if (entry.kind === "folder") return { ...entry, items: [...entry.items, dragId] };
    return { kind: "folder", id: newFolderId, name: "", items: [entry.id, dragId] };
  }));
  return normalize({ pages });
}

export function moveWithinFolder(layout: LaunchpadLayout, folderId: string, itemId: string, index: number): LaunchpadLayout {
  return {
    pages: layout.pages.map(page => page.map(entry => {
      if (entry.kind !== "folder" || entry.id !== folderId || !entry.items.includes(itemId)) return entry;
      const items = entry.items.filter(other => other !== itemId);
      items.splice(Math.max(0, Math.min(index, items.length)), 0, itemId);
      return { ...entry, items };
    })),
  };
}

export function renameFolder(layout: LaunchpadLayout, folderId: string, name: string): LaunchpadLayout {
  return { pages: layout.pages.map(page => page.map(entry => entry.kind === "folder" && entry.id === folderId ? { ...entry, name: name.trim() } : entry)) };
}

/** Parse a stored layout, dropping anything malformed. */
export function parseLayout(raw: unknown): LaunchpadLayout {
  const pages = (raw as { pages?: unknown } | null)?.pages;
  if (!Array.isArray(pages)) return EMPTY_LAYOUT;
  return normalize({
    pages: pages.filter(Array.isArray).map(page => page.flatMap((entry: unknown): Entry[] => {
      const e = entry as { kind?: unknown; id?: unknown; name?: unknown; items?: unknown };
      if (typeof e?.id !== "string") return [];
      if (e.kind === "item") return [item(e.id)];
      if (e.kind === "folder" && Array.isArray(e.items)) {
        return [{ kind: "folder", id: e.id, name: typeof e.name === "string" ? e.name : "", items: e.items.filter((x: unknown): x is string => typeof x === "string") }];
      }
      return [];
    })),
  });
}
