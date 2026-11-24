// The inbox: one query read by the count on the sidebar's Tasks row, the
// Tasks page's Inbox tab and the top of the Activity panel.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader({
  stubs: {
    "@tanstack/react-query": "export const useQuery = () => ({}); export const useQueryClient = () => ({});",
    "@/lib/net/fetch-client": "export const jsonFetch = async () => ({ items: [] });",
    "@/lib/net/ws/changes": "export const useChanged = () => {};",
    "@/lib/tabs/navigation/open-session": "export const openSession = () => {};",
    "@/lib/runtime-bridge/session/session-load": "export const sendRuntimeCommand = () => {};",
  },
});

const { groupPending, pendingInOrder } = await import("../../lib/agents/pending-inbox.ts");
const { pendingWhen } = await import("../../components/tasks/inbox-format.ts");
const { openingTab, coerceTasksTab } = await import("../../components/tasks/tasks-tabs.ts");
const source = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
const en = (english) => english;
const zh = (_english, chinese) => chinese;

const item = (kind, at) => ({ kind, at, session_id: "c", report_id: `${kind}${at}`, title: "", body: "", agent_id: "a", agent_name: "A", session_title: "" });

test("decisions come before news, each keeping the server's newest-first order", () => {
  const items = [item("finding", 9), item("approval", 8), item("done", 7), item("approval", 6)];
  const { decide, news } = groupPending(items);
  assert.deepEqual(decide.map(row => row.at), [8, 6]);
  assert.deepEqual(news.map(row => row.at), [9, 7]);
  assert.deepEqual(pendingInOrder(items).map(row => row.at), [8, 6, 9, 7]);
});

test("ages read in plain words", () => {
  const now = 100000;
  assert.equal(pendingWhen(now - 10, en, now), "just now");
  assert.equal(pendingWhen(now - 600, zh, now), "10 分钟前");
  assert.equal(pendingWhen(now - 7200, en, now), "2 h ago");
  assert.equal(pendingWhen(now - 3 * 86400, zh, now), "3 天前");
});

test("the page opens on a named tab, else on an Inbox with items, else on the last tab chosen", () => {
  assert.equal(openingTab("scheduled", 3, "inbox"), "scheduled");
  assert.equal(openingTab(null, 3, "scheduled"), "inbox");
  assert.equal(openingTab(null, 0, "inbox"), "inbox");
  assert.equal(openingTab(null, 0, null), "scheduled");
  assert.equal(coerceTasksTab("calendar"), null);
});

test("one Tasks row carries the Inbox count, the Inbox lists both groups and Activity leads with them", () => {
  const nav = source("components/sidebar/sidebar-primary-nav.tsx");
  assert.match(nav, /href: "\/tasks"/);
  assert.doesNotMatch(nav, /href: "\/pending"|href: "\/scheduler"/);
  assert.match(nav, /item\.id === "navTasks" \? inboxCount/);
  const page = source("components/tasks/tasks-inbox.tsx");
  assert.match(page, /"等你决定"/);
  assert.match(page, /"新发现"/);
  assert.match(page, /openPendingItem\(item\)/);
  assert.match(page, /item\.task_id\s*\? <CalendarDays/, "a scheduled task's result wears the calendar, not an avatar");
  assert.match(source("app/(shell)/pending/page.tsx"), /redirect\("\/tasks\?tab=inbox"\)/);
  assert.match(source("app/(shell)/scheduler/page.tsx"), /redirect\("\/tasks\?tab=scheduled"\)/);
  const panel = source("components/right-sidebar/running/running-panel.tsx");
  assert.ok(panel.indexOf('section("pending"') > 0 && panel.indexOf('section("pending"') < panel.indexOf("<WorkChart key"), "pending sits above the task chart");
});
