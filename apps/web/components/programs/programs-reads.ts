/**
 * The Programs page's reads: one conditional-read cache for the app's
 * lifetime, and the page's first view started before the page opens.
 */
import { jsonFetch } from "@/lib/net/fetch-client";
import { createProgramsCache } from "./programs-cache";
import type { ProgramExplorerEntry } from "./programs-catalog";

export type ExplorerResponse = {
  path: string;
  entries: ProgramExplorerEntry[];
  default_selection?: string | null;
};

export const catalogCache = createProgramsCache(jsonFetch);

/** The folders above `path`, outermost first ("a/b/c" → "a", "a/b"). */
export function parentPaths(path: string) {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

/**
 * What the page shows first, read ahead (hover, idle): the root folder, the
 * folders down to the default selection, and that Program's call logic, the
 * last two side by side. A failure only warns; the page reads again itself.
 */
export async function prefetchPrograms(): Promise<void> {
  try {
    const root = await catalogCache.read<ExplorerResponse>("explorer", "");
    const selection = root.default_selection;
    if (!selection) return;
    await Promise.all([
      catalogCache.read("logic", selection),
      ...parentPaths(selection).map((path) => catalogCache.read("explorer", path)),
    ]);
  } catch (cause) {
    console.warn("[prefetch] programs", cause);
  }
}

/**
 * The page's first view from what is already here: the root folder and, when
 * nothing was selected before, the default selection with its folders open.
 */
export function keptProgramsView(): {
  directories: Record<string, ProgramExplorerEntry[]>;
  selected: string | null;
  expanded: Set<string>;
} | null {
  const root = catalogCache.peek<ExplorerResponse>("explorer", "");
  if (!root) return null;
  const directories: Record<string, ProgramExplorerEntry[]> = { "": root.entries };
  const selected = root.default_selection || null;
  const parents = selected ? parentPaths(selected) : [];
  for (const path of parents) {
    const folder = catalogCache.peek<ExplorerResponse>("explorer", path);
    if (folder) directories[path] = folder.entries;
  }
  return { directories, selected, expanded: new Set(parents) };
}
