/**
 * Activity — what the sidebar's bell shows in place of the project list:
 * the conversations of the last 7 days that need the user's eye, newest
 * first, grouped Today / Yesterday / then by date. Codex's sidebar bell
 * is the reference (docs/reference/design/ui/sidebar/activity-view.html).
 *
 * A conversation qualifies with the same state its status marker shows:
 * waiting for the user, running, failed and not yet seen, or finished and
 * not yet seen. Archived conversations never do.
 */

import { activityTs, type LegacyConv } from "./helpers";

/** The marker states that put a conversation in Activity. */
export type ActivityState = "needs_input" | "working" | "failed" | "unread";

export const ACTIVITY_DAYS = 7;

/** The state a conversation's marker shows when it belongs in Activity,
 *  else null. Same priority as the row's marker: waiting outranks running,
 *  running outranks an unread result. */
export function activityState(conv: LegacyConv, running: boolean): ActivityState | null {
  if (conv.archived) return null;
  if (conv.status === "needs_input") return "needs_input";
  if (running) return "working";
  if (conv.unread) return conv.status === "failed" ? "failed" : "unread";
  return null;
}

export interface ActivityItem {
  conv: LegacyConv;
  state: ActivityState;
}

export interface ActivitySection {
  key: string;
  label: string;
  items: ActivityItem[];
}

function dayStart(ts: number): number {
  const d = new Date(ts * 1000);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Activity's rows, grouped by the calendar day of their last activity. */
export function activitySections(
  conversations: readonly LegacyConv[],
  o: {
    nowTs: number;
    locale: string;
    isRunning: (id: string) => boolean;
    labels: { today: string; yesterday: string };
  },
): ActivitySection[] {
  const cutoff = o.nowTs - ACTIVITY_DAYS * 86400;
  const items: ActivityItem[] = [];
  for (const conv of conversations) {
    if (activityTs(conv) < cutoff) continue;
    const state = activityState(conv, o.isRunning(conv.id));
    if (state) items.push({ conv, state });
  }
  items.sort((a, b) => activityTs(b.conv) - activityTs(a.conv));
  const today = dayStart(o.nowTs);
  const dateFormat = new Intl.DateTimeFormat(o.locale.startsWith("zh") ? "zh-CN" : "en-US", { month: "short", day: "numeric" });
  const sections = new Map<string, ActivitySection>();
  for (const item of items) {
    const ts = activityTs(item.conv) || o.nowTs;
    const day = dayStart(ts);
    const ago = Math.round((today - day) / 86_400_000);
    const key = `day-${day}`;
    if (!sections.has(key)) {
      const label = ago <= 0 ? o.labels.today : ago === 1 ? o.labels.yesterday : dateFormat.format(new Date(day));
      sections.set(key, { key, label, items: [] });
    }
    sections.get(key)!.items.push(item);
  }
  return [...sections.values()];
}

/** Whether any conversation waits for the user: the bell's amber dot. */
export function anyNeedsInput(conversations: Readonly<Record<string, LegacyConv>>): boolean {
  for (const conv of Object.values(conversations)) {
    if (!conv.archived && conv.status === "needs_input") return true;
  }
  return false;
}

/** The unread conversations "Mark all as read" clears. */
export function unreadIds(conversations: readonly LegacyConv[]): string[] {
  return conversations.filter((conv) => conv.unread && !conv.archived).map((conv) => conv.id);
}
