// The Downloads page: outside the desktop app it only says downloads live
// there; in the app it lists the bridge's entries, a completed file opens on
// click, an active download offers Cancel, a finished one Show in folder,
// Clear empties the history, and the list follows the bridge's changes and
// the search box.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/desktop/desktop-bridge": "export const desktopBridge = () => globalThis.__bridge ?? null;",
    "@/components/ui/search-input": [
      "import { createElement as h } from 'react';",
      "export const SearchInput = ({ value, onChange, placeholder, 'aria-label': label }) =>",
      "  h('input', { type: 'search', value, placeholder, 'aria-label': label, onChange: (event) => onChange(event.target.value) });",
    ].join("\n"),
  },
});
installDom();

const { createElement: h } = await import("react");
const { DownloadsPage } = await import("../../components/center-tabs/pages/library/downloads-page.tsx");

const entry = (id, state, received, total, extra = {}) => ({
  id, filename: `${id}.bin`, path: `/downloads/${id}.bin`, url: `https://example.test/${id}`,
  state, receivedBytes: received, totalBytes: total, startedAt: 1, updatedAt: 2, active: state === "progressing", ...extra,
});

function fakeDownloads(initial) {
  const calls = [];
  let entries = initial;
  let changed = null;
  let unsubscribed = 0;
  const api = {
    list: async (options) => { calls.push(["list", options]); return entries.filter((e) => !options.query || e.filename.includes(options.query)); },
    open: async (id) => { calls.push(["open", id]); return true; },
    show: async (id) => { calls.push(["show", id]); return true; },
    cancel: async (id) => { calls.push(["cancel", id]); return true; },
    clear: async () => { calls.push(["clear"]); entries = entries.filter((e) => e.active); return true; },
    onChanged: (callback) => { changed = callback; return () => { unsubscribed++; }; },
  };
  return { api, calls, set: (next) => { entries = next; }, change: () => changed?.(null), unsubscribed: () => unsubscribed };
}

// The page lists 100 ms after mounting; let that timer and the promise land.
const listed = () => new Promise((resolve) => setTimeout(resolve, 130));
const rows = (container) => [...container.querySelectorAll(".browsing-history-row")];
const byLabel = (scope, label) => scope.querySelector(`[aria-label="${label}"]`);
const buttonNamed = (container, text) => [...container.querySelectorAll("button")].find((b) => b.textContent === text);

afterEach(() => { delete globalThis.__bridge; });

test("without the desktop bridge the page says downloads are managed by the desktop app", async () => {
  const view = await render(h(DownloadsPage));
  assert.match(view.container.textContent, /Downloads are managed by the desktop app only\./);
  assert.equal(view.container.querySelector("input"), null, "no search box without a list");
  await view.unmount();
});

test("in the desktop app the list opens, cancels, shows and clears through the bridge and follows its changes", async () => {
  const downloads = fakeDownloads([
    entry("report", "completed", 2048, 2048, { filename: "report.pdf" }),
    entry("video", "progressing", 512, 4096),
    entry("partial", "interrupted", 100, 0),
  ]);
  globalThis.__bridge = { downloads: downloads.api };
  const view = await render(h(DownloadsPage));
  await view.act(listed);
  const { container } = view;
  assert.deepEqual(downloads.calls, [["list", { query: "" }]]);

  const [report, video, partial] = rows(container);
  assert.equal(rows(container).length, 3);
  assert.deepEqual(
    rows(container).map((row) => row.querySelector(".browsing-history-time").textContent),
    ["Completed", "Downloading", "Interrupted"],
  );
  assert.deepEqual(
    rows(container).map((row) => row.querySelector(".browsing-history-host").textContent),
    ["2.0 KB / 2.0 KB", "512 B / 4.0 KB", "100 B"],
  );

  // A completed file opens; anything else cannot be opened yet.
  const openReport = report.querySelector(".browsing-history-title");
  assert.equal(openReport.disabled, false);
  assert.equal(video.querySelector(".browsing-history-title").disabled, true);
  assert.equal(partial.querySelector(".browsing-history-title").disabled, true);
  await view.act(async () => { openReport.click(); });
  assert.deepEqual(downloads.calls.at(-1), ["open", "report"]);

  // An active download offers Cancel and nothing else; a finished one Show in folder.
  assert.ok(byLabel(video, "Cancel download"));
  assert.equal(byLabel(video, "Show in folder"), null);
  await view.act(async () => { byLabel(video, "Cancel download").click(); });
  assert.deepEqual(downloads.calls.at(-1), ["cancel", "video"]);
  assert.equal(byLabel(report, "Cancel download"), null);
  await view.act(async () => { byLabel(partial, "Show in folder").click(); });
  assert.deepEqual(downloads.calls.at(-1), ["show", "partial"]);

  // The bridge announcing a change refreshes the list.
  downloads.set([entry("video", "progressing", 1024, 4096)]);
  await view.act(async () => { downloads.change(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(rows(container).length, 1);
  assert.equal(buttonNamed(container, "Clear"), undefined, "nothing finished, nothing to clear");

  downloads.set([entry("video", "completed", 4096, 4096), entry("other", "cancelled", 10, 100)]);
  await view.act(async () => { downloads.change(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(rows(container).length, 2);
  await view.act(async () => { buttonNamed(container, "Clear").click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.deepEqual(downloads.calls.slice(-2).map(([name]) => name), ["clear", "list"], "Clear re-lists afterwards");
  assert.match(container.textContent, /No downloads yet/);

  // The search box filters through the bridge.
  downloads.set([entry("report", "completed", 2048, 2048, { filename: "report.pdf" }), entry("video", "completed", 4096, 4096)]);
  const search = byLabel(container, "Search downloads");
  await view.act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(search), "value").set.call(search, "nothing");
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await listed();
  });
  assert.deepEqual(downloads.calls.at(-1), ["list", { query: "nothing" }]);
  assert.match(container.textContent, /No matching downloads/);

  // One subscription per query (the first was replaced when the search changed); none survives unmounting.
  const before = downloads.unsubscribed();
  await view.unmount();
  assert.equal(downloads.unsubscribed(), before + 1, "unmounting stops listening to the bridge");
});
