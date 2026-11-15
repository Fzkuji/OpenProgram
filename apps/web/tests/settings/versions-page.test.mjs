// Settings > Versions: the sentences it shows, and how notices come up one at a time.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    "/lib/tabs/navigation/open-session.ts": "export const openSession = (id) => { globalThis.__opened = id; };",
    "/lib/format-utils/toast.ts": "export const showToast = (message) => { globalThis.__toasts.push(message); };",
  },
});
const window = installDom();
globalThis.HTMLFormElement = window.HTMLFormElement;
globalThis.__toasts = [];

const { createElement: h } = await import("react");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { VersionsSettings } = await import("../../components/settings/sections/versions-settings.tsx");
const {
  createNoticeRunner, fallbackDescription, keptDescription, noticeToast, runningDescription, savedEditsTitle, versionName,
} = await import("../../lib/settings/versions.ts");

const en = (english) => english;

const page = (overrides = {}) => ({
  managed: true,
  current: { id: "b", number: 14, since: 1794100000, on_probation: false, returned_from: null },
  fallback: { id: "a", number: 13 },
  versions: [],
  unapplied: [],
  ...overrides,
});

test("the running row says how the version got here", () => {
  assert.match(runningDescription(page(), en), /^Passed its tests and has been working since /);
  assert.match(runningDescription(page({ current: { ...page().current, on_probation: true } }), en),
    /still being checked. If a check fails, OpenProgram returns to Version 13\./);
  const returned = page({ current: { ...page().current, returned_from: { id: "c", number: 15, at: 1794100000 } } });
  assert.match(runningDescription(returned, en), /^Version 15 stopped starting, so OpenProgram came back to this version on /);
});

test("the fallback row names the version it returns to", () => {
  assert.equal(fallbackDescription(page(), en),
    "Version 13. If Version 14 stops starting, OpenProgram goes back to it by itself. You don't need to do anything.");
  assert.equal(fallbackDescription(page({ fallback: null }), en), "There is no earlier version to return to yet.");
});

test("a kept version reads as its summary and then its date", () => {
  const version = { id: "a", number: 12, summary: "Fixed scheduled jobs", built_at: 1794100000, current: false, can_undo_change: true };
  assert.match(keptDescription(version, en), /^Fixed scheduled jobs\. [A-Z][a-z]{2} \d+, \d\d:\d\d$/);
  assert.equal(keptDescription({ ...version, summary: "", built_at: null }, en), "");
  assert.equal(versionName(null, en), "This version");
});

test("saved edits count their files", () => {
  assert.equal(savedEditsTitle({ stamp: "s", files: 1, found_at: null }, en), "1 file");
  assert.match(savedEditsTitle({ stamp: "s", files: 3, found_at: 1794100000 }, en), /^3 files, found /);
});

test("the two notices read as drawn and link where the answer is", () => {
  const returned = noticeToast({ id: "r", kind: "returned", at: 1, number: 15, session_id: "s 1" }, en);
  assert.equal(returned.message, "Version 15 didn't work, so OpenProgram went back to the last good version.");
  assert.deepEqual(returned.link, { label: "See why", href: "/s/s%201" });
  assert.equal(noticeToast({ id: "r", kind: "returned", at: 1 }, en).link.href, "/settings/versions");
  const edits = noticeToast({ id: "e", kind: "direct_edits", at: 1, files: 3 }, en);
  assert.equal(edits.message, "Your direct edits to 3 files are saved, but not in use.");
  assert.deepEqual(edits.link, { label: "Open Versions", href: "/settings/versions" });
});

test("notices come up one at a time, and each only once", async () => {
  const log = [];
  let release = () => {};
  const poll = createNoticeRunner({
    fetchNotices: async () => [{ id: "one" }, { id: "two" }],
    acknowledge: async (ids) => { log.push(["ack", ...ids]); },
    show: (notice) => log.push(["show", notice.id]),
    wait: () => new Promise((resolve) => { release = resolve; }),
  });
  const running = poll();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(log, [["show", "one"], ["ack", "one"]]);
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  release();
  await running;
  await poll();
  assert.deepEqual(log, [["show", "one"], ["ack", "one"], ["show", "two"], ["ack", "two"]]);
});

test("a failed read shows nothing and does not stop the next one", async () => {
  const shown = [];
  let fail = true;
  const poll = createNoticeRunner({
    fetchNotices: async () => { if (fail) throw new Error("restarting"); return [{ id: "one" }]; },
    acknowledge: async () => {},
    show: (notice) => shown.push(notice.id),
    wait: async () => {},
  });
  await poll();
  fail = false;
  await poll();
  assert.deepEqual(shown, ["one"]);
});

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function mount(overview) {
  const posts = [];
  globalThis.fetch = async (url, init) => {
    if (init?.method === "POST") {
      posts.push([url, JSON.parse(init.body)]);
      return { ok: true, status: 200, text: async () => JSON.stringify({ session_id: "versions_1" }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(overview) };
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const view = await render(h(QueryClientProvider, { client }, h(VersionsSettings)));
  await view.act(settle);
  await view.act(settle);
  return { view, posts };
}

function button(view, row, label) {
  const field = [...view.container.querySelectorAll("[data-slot=field]")].find((el) => el.textContent.includes(row));
  return [...(field?.querySelectorAll("button") ?? [])].find((el) => el.textContent === label);
}

const keptPage = page({
  versions: [
    { id: "b", number: 14, summary: "Added a summary", built_at: 1794100000, commit: "c", current: true, can_undo_change: true },
    { id: "a", number: 13, summary: "Fixed a bug", built_at: 1794000000, commit: "d", current: false, can_undo_change: true },
  ],
  unapplied: [{ stamp: "s1", files: 3, found_at: 1794100000 }],
});

test("the page shows the mockup's sections and buttons", async () => {
  const { view } = await mount(keptPage);
  const body = view.container.textContent;
  for (const heading of ["Right now", "Running now", "Returns here if something breaks", "Kept versions", "Edits saved but not in use"]) {
    assert.ok(body.includes(heading), heading);
  }
  assert.ok(button(view, "Added a summary", "Undo change"));
  assert.equal(button(view, "Added a summary", "Go back"), undefined, "the running version has nothing to go back to");
  assert.ok(button(view, "Fixed a bug", "Go back"));
  await view.unmount?.();
});

test("Go back asks the server and opens the conversation without a dialog", async () => {
  globalThis.__opened = null;
  const { view, posts } = await mount(keptPage);
  await view.act(async () => { button(view, "Fixed a bug", "Go back").click(); await settle(); await settle(); });
  assert.deepEqual(posts, [["/api/versions/undo", { version: "a", how: "go_back" }]]);
  assert.equal(globalThis.__opened, "versions_1");
  await view.unmount?.();
});

test("an install without versions says so", async () => {
  const { view } = await mount({ managed: false, versions: [], unapplied: [] });
  assert.ok(view.container.textContent.includes("This copy doesn't keep versions"));
  assert.equal(view.container.querySelectorAll("button").length, 0);
  await view.unmount?.();
});

test("both notices are the warning toast at one width, and only those are", () => {
  const source = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
  const notices = source("components/settings/version-notices.tsx");
  assert.match(notices, /tone: "warn"/);
  assert.match(notices, /uniformWidth: true/);
  assert.match(source("components/ui/toast-host.tsx"), /it\.uniformWidth \? " w-\[520px\]/);
});
