// Annotate mode, host side: commands reach the page's isolated world, the
// loop forwards what the overlay reports, and a send carries a screenshot
// and notes cut down to typed, clipped fields.
"use strict";
const assert = require("node:assert/strict");
const { createWebTabAnnotate, annotateOptions, sentNotes } = require("../../main/web-views/webtab-annotate");

function fakePage() {
  const calls = [];
  const events = [];
  let wake = null;
  const contents = {
    focused: 0,
    destroyed: false,
    isDestroyed() { return this.destroyed; },
    focus() { this.focused += 1; },
    getURL: () => "https://example.com/",
    capturePage: async () => ({ isEmpty: () => false, toDataURL: () => "data:image/png;base64,AAAA" }),
    executeJavaScriptInIsolatedWorld(world, sources) {
      const code = sources[0].code;
      calls.push({ world, code });
      if (code.startsWith("window.__openprogramAnnotate ? window.__openprogramAnnotate.next()")) {
        if (events.length) return Promise.resolve(events.shift());
        return new Promise((resolve) => { wake = resolve; });
      }
      if (code.includes("attachShadow")) return Promise.resolve({ fresh: calls.filter((c) => c.code.includes("attachShadow")).length === 1 });
      return Promise.resolve(true);
    },
  };
  const push = (event) => {
    if (wake) { const resolve = wake; wake = null; resolve(event); } else events.push(event);
  };
  return { contents, calls, push };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

(async () => {
  const page = fakePage();
  const sent = [];
  const record = { id: "w:1", view: { webContents: page.contents } };
  const ctx = { win: { isDestroyed: () => false, webContents: { send: (channel, payload) => sent.push([channel, payload]) } } };
  const { annotateView } = createWebTabAnnotate({
    ownerOf: () => ctx,
    recordFor: (owner, id) => (owner === ctx && id === "w:1" ? record : null),
    world: 1207,
  });

  assert.equal(await annotateView(ctx, "w:1", "rm -rf"), false, "unknown commands are refused");
  assert.equal(await annotateView(ctx, "w:9", "start"), false, "a page this window does not own");

  assert.equal(await annotateView(ctx, "w:1", "start", {
    labels: { hint: "Click", bogus: "x" }, theme: { accent: "#3b82f6", bg: "red;}body{display:none" },
  }), true);
  assert.equal(page.contents.focused, 1, "the page takes the keyboard for Esc");
  assert.ok(page.calls.every((call) => call.world === 1207), "everything runs in the pickers' isolated world");
  const entered = page.calls[0].code.slice(page.calls[0].code.lastIndexOf("api.enter("));
  assert.equal(entered.slice(0, entered.indexOf(");")),
    'api.enter({"labels":{"hint":"Click"},"theme":{"accent":"#3b82f6"}}', "only known labels and plain colours reach the page");

  page.push({ type: "state", active: true, count: 2 });
  await settle();
  assert.deepEqual(sent.at(-1), ["webtab:annotate-event", { id: "w:1", type: "state", active: true, count: 2 }]);

  // A second start on the same page re-enters without a second loop.
  await annotateView(ctx, "w:1", "start", {});
  await settle();
  const waits = page.calls.filter((call) => call.code.includes(".next()")).length;

  page.push({
    type: "send", url: "https://example.com/a", title: "A",
    notes: [{ kind: "element", selector: "h1#x", comment: "bigger", rect: { x: 1.4, y: "2", width: 3, height: 4 },
      adjust: [{ property: "font-size", from: "28px", to: "34px" }, { property: "", to: "x" }], extra: "dropped" }],
  });
  await settle();
  const [, send] = sent.at(-1);
  assert.equal(send.type, "send");
  assert.equal(send.screenshot, "data:image/png;base64,AAAA", "the page is captured with its pins");
  assert.deepEqual(send.notes, [{
    n: 1, kind: "element", selector: "h1#x", tag: "", text: "", comment: "bigger",
    rect: { x: 1, y: 2, width: 3, height: 4 }, adjust: [{ property: "font-size", from: "28px", to: "34px" }],
  }]);
  assert.equal(page.calls.filter((call) => call.code.includes(".next()")).length, waits + 1, "one loop per page");

  assert.equal(await annotateView(ctx, "w:1", "sent"), true);
  assert.match(page.calls.at(-1).code, /api\["sent"\]\(\)/);

  // A page that stops answering ends its loop and the window hears the mode is off.
  page.push(null);
  await settle();
  assert.deepEqual(sent.at(-1)[1], { id: "w:1", type: "state", active: false, count: 0 });
  assert.equal(record.annotateToken, null);

  record.pipLayoutZoom = 0.5;
  assert.equal(await annotateView(ctx, "w:1", "start"), false, "a page in the floating preview is not marked up");

  assert.deepEqual(annotateOptions(null), { labels: {}, theme: {} });
  assert.deepEqual(sentNotes("nope"), []);
  assert.equal(sentNotes(Array.from({ length: 80 }, () => ({}))).length, 50);
  console.log("webtab annotate: commands, loop, send OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
