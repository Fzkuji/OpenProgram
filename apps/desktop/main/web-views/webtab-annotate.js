// Annotate mode, host side. The overlay (annotate-overlay.js) runs in the
// page's isolated world; this keeps one loop per page that waits on the
// overlay's next() and forwards what happens to the window that owns the
// tab. On Send it screenshots the page with the pins still drawn, so the
// chat gets the notes, their numbers on the page, and the picture.
"use strict";

const { annotateCommandSource } = require("./annotate-overlay");

const COMMANDS = new Set(["start", "exit", "discard", "sent", "unsent"]);
const THEME_KEYS = ["bg", "border", "text", "muted", "accent"];
const LABEL_KEYS = [
  "hint", "placeholder", "delete", "adjust", "size", "spacing", "color",
  "countOne", "countMany", "discard", "send",
];
const NOTE_LIMIT = 50;

function text(value, limit) {
  return typeof value === "string" ? value.slice(0, limit) : "";
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number) : 0;
}

/** What the window may hand the page: short strings under known keys. */
function annotateOptions(options) {
  const labels = {};
  const theme = {};
  for (const key of LABEL_KEYS) {
    const value = text(options?.labels?.[key], 120);
    if (value) labels[key] = value;
  }
  for (const key of THEME_KEYS) {
    // A colour value only: no braces or semicolons to escape a declaration.
    const value = text(options?.theme?.[key], 80).trim();
    if (value && !/[;{}<>]/.test(value)) theme[key] = value;
  }
  return { labels, theme };
}

/** The notes as the window gets them: every field typed and clipped, since
 *  selectors and element text come from the page. */
function sentNotes(notes) {
  if (!Array.isArray(notes)) return [];
  return notes.slice(0, NOTE_LIMIT).map((note, index) => ({
    n: index + 1,
    kind: note?.kind === "region" ? "region" : "element",
    selector: text(note?.selector, 300),
    tag: text(note?.tag, 40),
    text: text(note?.text, 200),
    comment: text(note?.comment, 4000),
    rect: {
      x: finite(note?.rect?.x), y: finite(note?.rect?.y),
      width: finite(note?.rect?.width), height: finite(note?.rect?.height),
    },
    adjust: Array.isArray(note?.adjust)
      ? note.adjust.slice(0, 8).map((change) => ({
        property: text(change?.property, 40),
        from: text(change?.from, 80),
        to: text(change?.to, 80),
      })).filter((change) => change.property && change.to)
      : [],
  }));
}

function createWebTabAnnotate({ ownerOf, recordFor, world }) {
  function run(contents, action, options) {
    return contents.executeJavaScriptInIsolatedWorld(
      world, [{ code: annotateCommandSource(action, options) }], true,
    );
  }

  function emit(record, event) {
    const ctx = ownerOf(record);
    if (!ctx || ctx.win.isDestroyed()) return;
    ctx.win.webContents.send("webtab:annotate-event", { id: record.id, ...event });
  }

  async function screenshot(contents) {
    try {
      const image = await contents.capturePage(undefined, { stayHidden: true });
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) return "";
      return image.toDataURL();
    } catch (_error) {
      return "";
    }
  }

  // One loop per page. A page that navigates away takes its overlay with
  // it; the next start installs a fresh one and a new loop, and the old
  // loop, if its wait ever settles, sees it is no longer current and ends.
  async function loop(record, contents, token) {
    const current = () => record.annotateToken === token && !contents.isDestroyed();
    while (current()) {
      let event = null;
      try { event = await run(contents, "next"); } catch (_error) { event = null; }
      if (!current() || event?.type === "superseded") return;
      if (!event || typeof event !== "object") {
        record.annotateToken = null;
        emit(record, { type: "state", active: false, count: 0 });
        return;
      }
      if (event.type === "state") {
        emit(record, { type: "state", active: !!event.active, count: finite(event.count) });
      } else if (event.type === "send") {
        const image = await screenshot(contents);
        if (!current()) return;
        emit(record, {
          type: "send",
          url: text(event.url, 4096) || contents.getURL(),
          title: text(event.title, 300),
          notes: sentNotes(event.notes),
          screenshot: image,
        });
      }
    }
  }

  async function annotateView(ctx, id, action, options) {
    const record = recordFor(ctx, id);
    const contents = record?.view?.webContents;
    if (!contents || contents.isDestroyed?.() || !COMMANDS.has(action)) return false;
    try {
      if (action !== "start") return (await run(contents, action)) === true;
      // A page shrunk into the floating preview is not marked up.
      if (record.pipLayoutZoom) return false;
      contents.focus();
      const started = await run(contents, "start", annotateOptions(options));
      if (recordFor(ctx, id) !== record || contents.isDestroyed()) return false;
      if (started?.fresh || !record.annotateToken) {
        const token = Symbol("annotate");
        record.annotateToken = token;
        void loop(record, contents, token);
      }
      return true;
    } catch (_error) {
      return false;
    }
  }

  return { annotateView };
}

module.exports = { createWebTabAnnotate, annotateOptions, sentNotes };
