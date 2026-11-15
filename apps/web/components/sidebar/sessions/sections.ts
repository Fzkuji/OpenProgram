
import { type LegacyConv, bucketKey, bucketLabel, bucketSortKey } from "./helpers";

/* ---- sectioning --------------------------------------------------
 * Turns the filtered+sorted conversation list into labelled sections,
 * mirroring Claude's Recents:
 *   - groupBy "state"   → Working (a task running) / Completed
 *   - groupBy "project" → handled in SessionsList (registry folder tree)
 *   - groupBy "none"    → date buckets when sorted by recency
 *                         (Pinned / Today / Past 7 days / Past 30 days / Older),
 *                         or a single flat header-less run when sorted
 *                         by title
 * `items` keep the incoming order (already pinned-first + sorted), so
 * date buckets come out most-recent-first via insertion order.
 */
interface Section {
  key: string;
  label: string; // "" → flat, no header
  items: LegacyConv[];
}
interface SectionOpts {
  groupBy: "none" | "state" | "project" | "flat";
  sort: "recency" | "created" | "title";
  sortDirection: "asc" | "desc";
  nowTs: number;
  locale: string;
  isWorking: (id: string) => boolean;
  labels: {
    pinned: string;
    recents: string;
    today: string;
    past7: string;
    working: string;
    completed: string;
  };
}

function _dateBucket(
  ts: number,
  nowTs: number,
  labels: SectionOpts["labels"],
  locale: string,
): { key: string; label: string } {
  // Today / 7d / 30d, then current-year months, then years. Bucketing,
  // ordering and month/year labels all live in helpers, shared with
  // /chats so both surfaces agree on what "Today" means.
  const k = bucketKey(ts, nowTs);
  return { key: bucketSortKey(k), label: bucketLabel(k, locale, labels) };
}

export function buildSections(visible: LegacyConv[], o: SectionOpts): Section[] {
  if (o.groupBy === "state") {
    const working = visible.filter((c) => o.isWorking(c.id));
    const done = visible.filter((c) => !o.isWorking(c.id));
    return [
      { key: "st_working", label: o.labels.working, items: working },
      { key: "st_completed", label: o.labels.completed, items: done },
    ].filter((s) => s.items.length > 0);
  }

  // groupBy "project" never reaches here — SessionsList renders the
  // registry-backed folder tree for that mode before calling this.

  if (o.groupBy === "flat") {
    // "None" — one flat "Recents" run: a single header, no date sub-
    // buckets (order follows the sort). Always headed so the list is
    // never an empty / header-less strip.
    return [{ key: "flat", label: o.labels.recents, items: visible }];
  }

  // groupBy "none" → "Date": Pinned (if any) + date buckets; insertion
  // order within each bucket follows the chosen sort.
  const pinned = visible.filter((c) => c.pinned);
  const rest = visible.filter((c) => !c.pinned);
  const buckets = new Map<string, Section>();
  for (const c of rest) {
    // 日期分桶按最后活跃时间："Today" = 今天动过的会话，与 recency
    // 排序一致（否则今天聊过的老会话会挂在旧日期桶里）。
    const b = _dateBucket(
      c.updated_at || c.created_at || 0, o.nowTs, o.labels, o.locale,
    );
    if (!buckets.has(b.key)) buckets.set(b.key, { key: b.key, label: b.label, items: [] });
    buckets.get(b.key)!.items.push(c);
  }
  const out: Section[] = [];
  if (pinned.length) out.push({ key: "pinned", label: o.labels.pinned, items: pinned });
  const sorted = Array.from(buckets.values()).sort((a, b) => a.key.localeCompare(b.key) * (o.sortDirection === "asc" ? -1 : 1));
  out.push(...sorted);
  return out;
}
