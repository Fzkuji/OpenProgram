// Keyboard tab switching, as in a browser: Cmd+1…8 picks the strip entry at
// that position, Cmd+9 the last one, Cmd+Shift+[ / ] the previous / next one
// (wrapping), Cmd+Shift+T reopens the last closed tab. A split is one strip
// entry and lands on its focused pane.
import { topLevelTabs } from "../../browser/web-tab/web-page-management";
import { centerTabStripEntries, findCenterTabGroup, type CenterTabStripEntry } from "../center-tab-groups";
import type { CenterTabsState } from "../store/types";

type Strip = Pick<CenterTabsState, "tabs" | "groups" | "activeId">;

export type TabShortcut =
  | { action: "new-chat" }
  | { action: "reopen-tab" }
  | { action: "next-tab" }
  | { action: "previous-tab" }
  | { action: "select-tab"; index: number };

function entryTabId(entry: CenterTabStripEntry): string {
  return entry.kind === "tab" ? entry.tabId : entry.group.focusedId;
}

function stripEntries(s: Strip): CenterTabStripEntry[] {
  return centerTabStripEntries({
    tabIds: topLevelTabs(s.tabs, s.groups).map((tab) => tab.id),
    groups: s.groups,
  });
}

/** The tab a switching shortcut activates, or null when it has nowhere to go. */
export function tabForShortcut(s: Strip, shortcut: TabShortcut): string | null {
  const entries = stripEntries(s);
  if (!entries.length) return null;
  if (shortcut.action === "select-tab") {
    // 9 is always the last tab, however many there are.
    const entry = shortcut.index >= 9 ? entries.at(-1) : entries[shortcut.index - 1];
    return entry ? entryTabId(entry) : null;
  }
  if (shortcut.action !== "next-tab" && shortcut.action !== "previous-tab") return null;
  const group = s.activeId ? findCenterTabGroup(s.groups, s.activeId) : undefined;
  const current = entries.findIndex((entry) => entry.kind === "group"
    ? entry.group.id === group?.id : entry.tabId === s.activeId);
  const step = shortcut.action === "next-tab" ? 1 : -1;
  const index = current < 0
    ? (step > 0 ? 0 : entries.length - 1)
    : (current + step + entries.length) % entries.length;
  const id = entryTabId(entries[index]);
  return id === s.activeId ? null : id;
}

/** Map a key press to a shortcut (Cmd on macOS, Ctrl elsewhere). */
export function shortcutForKey(event: Pick<KeyboardEvent, "key" | "code" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">, mac: boolean): TabShortcut | null {
  const primary = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  if (!primary || event.altKey) return null;
  if (event.shiftKey) {
    if (event.code === "BracketRight" || event.key === "}") return { action: "next-tab" };
    if (event.code === "BracketLeft" || event.key === "{") return { action: "previous-tab" };
    if (event.code === "KeyT" || event.key.toLowerCase() === "t") return { action: "reopen-tab" };
    return null;
  }
  if (event.key.toLowerCase() === "n") return { action: "new-chat" };
  if (/^[1-9]$/.test(event.key)) return { action: "select-tab", index: Number(event.key) };
  return null;
}
