export interface HistoryEntryLike {
  visitedAt: number;
}

export interface HistoryDateGroup<T extends HistoryEntryLike> {
  key: string;
  date: Date;
  entries: T[];
}

export function groupHistoryByLocalDate<T extends HistoryEntryLike>(
  entries: T[],
): HistoryDateGroup<T>[] {
  const groups = new Map<string, HistoryDateGroup<T>>();
  for (const entry of entries) {
    const date = new Date(entry.visitedAt);
    if (Number.isNaN(date.getTime())) continue;
    const key = [date.getFullYear(), date.getMonth() + 1, date.getDate()]
      .map((value, index) => (index === 0 ? String(value) : String(value).padStart(2, "0")))
      .join("-");
    const group = groups.get(key);
    if (group) group.entries.push(entry);
    else groups.set(key, { key, date, entries: [entry] });
  }
  return [...groups.values()];
}
