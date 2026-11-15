// The pending inbox: one query read by the sidebar's Pending row, the
// Pending page and the top of the Activity panel.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader({
  stubs: {
    "@tanstack/react-query": "export const useQuery = () => ({}); export const useQueryClient = () => ({});",
    "@/lib/net/fetch-client": "export const jsonFetch = async () => ({ items: [] });",
    "@/lib/chat/conversation-work": "export const onConversationWorkChanged = () => () => {};",
    "@/lib/tabs/navigation/open-session": "export const openSession = () => {};",
    "@/lib/runtime-bridge/session/session-load": "export const sendRuntimeCommand = () => {};",
  },
});

const { groupPending, pendingInOrder } = await import("../../lib/agents/pending-inbox.ts");
const { pendingWhen } = await import("../../components/pending/pending-format.ts");
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

test("the sidebar has a Pending row with a count, the page lists both groups and Activity leads with them", () => {
  const nav = source("components/sidebar/sidebar-primary-nav.tsx");
  assert.match(nav, /href: "\/pending"/);
  assert.match(nav, /item\.id === "navPending" \? pendingCount/);
  const page = source("components/pending/pending-page.tsx");
  assert.match(page, /"等你决定"/);
  assert.match(page, /"新发现"/);
  assert.match(page, /openPendingItem\(item\)/);
  const panel = source("components/right-sidebar/running/running-panel.tsx");
  assert.ok(panel.indexOf('section("pending"') < panel.indexOf('section("agents"'), "pending sits above Agents");
});
