export const EXPLORER_INDENT = 27;
export const EXPLORER_BASE_PAD = 16;

export function matchingIndexes(
  value: string,
  query: string,
  fuzzy: boolean,
): number[] | null {
  const name = value.toLowerCase();
  const term = query.trim().toLowerCase();
  if (!term) return null;
  if (!fuzzy) {
    const start = name.indexOf(term);
    return start < 0 ? null : [...term].map((_, index) => start + index);
  }
  const indexes: number[] = [];
  let queryIndex = 0;
  for (let nameIndex = 0; nameIndex < name.length && queryIndex < term.length; nameIndex += 1) {
    if (name[nameIndex] !== term[queryIndex]) continue;
    indexes.push(nameIndex);
    queryIndex += 1;
  }
  return queryIndex === term.length ? indexes : null;
}

export function visibleSearchPaths(
  paths: string[],
  query: string,
  fuzzy: boolean,
): Set<string> {
  const visible = new Set<string>();
  for (const path of paths) {
    const name = path.split("/").pop() ?? path;
    if (!matchingIndexes(name, query, fuzzy)) continue;
    const parts = path.split("/");
    for (let index = 1; index <= parts.length; index += 1) {
      visible.add(parts.slice(0, index).join("/"));
    }
  }
  return visible;
}
