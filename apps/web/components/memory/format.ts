/**
 * Small formatting + grouping helpers for the Memory page.
 */
import type { TimelineDay, TimelineYearGroup, TopicPage } from "./types";

/** Human-readable byte size: 512 B / 12.3 KB / 1.4 MB. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Relative timestamp: "just now" / "5m ago" / "3h ago" / "2d ago" /
 *  locale-formatted date for anything older than a week. */
export function formatDate(mtime: number, locale: "en" | "zh" = "en"): string {
  const d = new Date(mtime * 1000);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (locale === "zh") {
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
    if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)} 天前`;
    return d.toLocaleDateString("zh-CN");
  }
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)}d ago`;
  return d.toLocaleDateString();
}

/** Group wiki pages by their top-level folder prefix (e.g.
 *  ``concepts/foo.md`` → "concepts"). Pages at the root land
 *  under the empty-string bucket so the caller can render them
 *  ungrouped. */
export function groupByFolder(pages: TopicPage[]): Map<string, TopicPage[]> {
  const groups = new Map<string, TopicPage[]>();
  for (const p of pages) {
    const parts = p.path.split("/");
    const folder = parts.length > 1 ? parts[0] : "";
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder)!.push(p);
  }
  return groups;
}

/** Group derived Timeline files by calendar year and month. The filename date
 * is the semantic event date; the file mtime is only the last rebuild time. */
export function groupTimelineDays(
  days: TimelineDay[],
  locale: "en" | "zh" = "en",
): TimelineYearGroup[] {
  const language = locale === "zh" ? "zh-CN" : "en-US";
  const years = new Map<string, {
    entries: TimelineYearGroup["entries"];
    months: Map<string, TimelineYearGroup["months"][number]>;
  }>();

  for (const day of [...days].sort((a, b) => b.date.localeCompare(a.date))) {
    const match = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(day.date);
    if (!match) continue;
    const [, year, month, dayLabel] = match;
    const yearNumber = Number(year);
    if (yearNumber < 1) continue;
    const yearGroup: {
      entries: TimelineYearGroup["entries"];
      months: Map<string, TimelineYearGroup["months"][number]>;
    } = years.get(year) ?? { entries: [], months: new Map() };
    if (month === undefined) {
      yearGroup.entries.push({ ...day, label: locale === "zh" ? "全年记录" : "Year overview" });
      years.set(year, yearGroup);
      continue;
    }
    const monthNumber = Number(month);
    if (monthNumber < 1 || monthNumber > 12) continue;
    const monthDate = new Date(0);
    monthDate.setUTCHours(0, 0, 0, 0);
    monthDate.setUTCFullYear(yearNumber, monthNumber - 1, 1);
    const monthKey = `${year}-${month}`;
    const group: TimelineYearGroup["months"][number] = yearGroup.months.get(monthKey) ?? {
      key: monthKey,
      label: new Intl.DateTimeFormat(language, { month: "long", timeZone: "UTC" }).format(monthDate),
      entries: [],
      days: [],
    };
    if (dayLabel === undefined) {
      group.entries.push({ ...day, label: locale === "zh" ? "月度记录" : "Month overview" });
      yearGroup.months.set(monthKey, group);
      years.set(year, yearGroup);
      continue;
    }
    const dayNumber = Number(dayLabel);
    const date = new Date(0);
    date.setUTCHours(0, 0, 0, 0);
    date.setUTCFullYear(yearNumber, monthNumber - 1, dayNumber);
    if (
      date.getUTCFullYear() !== yearNumber
      || date.getUTCMonth() !== monthNumber - 1
      || date.getUTCDate() !== dayNumber
    ) continue;
    group.days.push({
      ...day,
      dayLabel,
      weekday: new Intl.DateTimeFormat(language, { weekday: "long", timeZone: "UTC" }).format(date),
    });
    yearGroup.months.set(monthKey, group);
    years.set(year, yearGroup);
  }

  return Array.from(years, ([year, group]) => {
    const groupedMonths = Array.from(group.months.values());
    return {
      year,
      entryCount: group.entries.length + groupedMonths.reduce(
        (total, month) => total + month.entries.length + month.days.length,
        0,
      ),
      entries: group.entries,
      months: groupedMonths,
    };
  });
}
