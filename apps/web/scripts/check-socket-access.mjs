/** Guards against DEAD GLOBAL READS: `window.foo` that nothing ever assigns.
 *
 *  A dead read is invisible at runtime — no crash, no warning, just
 *  `undefined` and a silently skipped branch. Two have shipped already:
 *
 *  - `window.ws`: eight files read a socket nobody assigned (the real one
 *    lives in `runtimeState.ws`, via `getSocket()`). Their `load_session`
 *    sends were dropped, so retry / branch / rewind / version-switch never
 *    refreshed the transcript until a manual page reload.
 *  - `window.currentSessionId`: fn-form asked the backend for the "last used
 *    working folder" without a session id, so the lookup was skipped server
 *    side and the field silently fell back to the repo root forever.
 *
 *  Rule enforced here: every `window.<name>` that is READ must have a WRITE
 *  somewhere under web/, or be a known platform / externally-injected
 *  global. Reads are followed through all three shapes this codebase uses —
 *  plain `window.foo`, a named alias (`const w = window as …; w.foo`), and
 *  an inline cast (`(window as unknown as {…}).foo`). Anything unassigned
 *  fails the check.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// fileURLToPath, not .pathname — a repo path containing a space arrives
// percent-encoded otherwise and every readdir misses.
const root = fileURLToPath(new URL("../", import.meta.url));
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist", "build"]);

/** Blank out comments so prose ABOUT a dead global never reads as one.
 *  The old line-prefix test only caught lines STARTING with `*` or `//`,
 *  which let block-comment continuation lines (`* mirrors window._foo`)
 *  through. Replacing comment bodies with spaces preserves line numbers
 *  and column offsets, so reported positions stay accurate. */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/\/\/[^\n]*/g, (m) => " ".repeat(m.length));
}

/** Every .ts/.tsx under web/, minus build output. */
function* sources(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      yield* sources(path);
    } else if (/\.tsx?$/.test(name)) {
      yield path;
    }
  }
}

/** Globals the PLATFORM provides, so the absence of an assignment in our
 *  source is expected rather than a bug.
 *
 *  - Standard `window` members (DOM API surface).
 *  Anything NOT listed must be assigned somewhere in web/ — including the
 *  debug hooks `__centerTabs` / `__desktopTransfer`, which app-shell.tsx
 *  really does assign and therefore pass on their own merit. */
const PLATFORM_GLOBALS = new Set([
  // Injected by the Electron preload via `contextBridge.exposeInMainWorld`
  // (apps/desktop/preload.js), so the write is outside web/ by design. Reads are
  // all null-guarded — in a browser tab the bridge is legitimately absent.
  "openprogramDesktop",
  // Standard DOM / BOM surface.
  "addEventListener", "removeEventListener", "dispatchEvent",
  "location", "history", "navigator", "document", "screen", "frames",
  "parent", "top", "self", "opener", "origin", "name", "closed", "length",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval",
  "requestAnimationFrame", "cancelAnimationFrame",
  "requestIdleCallback", "cancelIdleCallback",
  "queueMicrotask", "structuredClone",
  "localStorage", "sessionStorage", "indexedDB", "caches",
  "innerWidth", "innerHeight", "outerWidth", "outerHeight",
  "scrollX", "scrollY", "pageXOffset", "pageYOffset",
  "devicePixelRatio", "visualViewport", "isSecureContext",
  "matchMedia", "getComputedStyle", "getSelection",
  "scroll", "scrollTo", "scrollBy", "resizeTo", "moveTo", "focus", "blur",
  "alert", "confirm", "prompt", "print", "open", "close", "stop",
  "fetch", "crypto", "performance", "console",
  "postMessage", "atob", "btoa", "CSS", "customElements",
  "Notification", "WebSocket", "Worker", "URL", "Image", "Audio", "Blob",
  "FileReader", "IntersectionObserver", "ResizeObserver", "MutationObserver",
  "AbortController", "EventSource", "speechSynthesis", "clipboardData",
]);

const files = [...sources(root)];

/** Names ASSIGNED on window anywhere in the tree:
 *    window.foo = …      alias.foo = …      delete window.foo
 *  A cast that merely DECLARES the field (`window as unknown as {foo?: T}`)
 *  is deliberately NOT an assignment — every bug this check exists for had
 *  exactly that cast and no writer behind it. */
const assigned = new Set();

/** Names bound to `window` by a cast in this file — `const w = window as …`,
 *  `const win = window as unknown as {…}`. Only these are followed as window
 *  aliases; without the check, any local named `w` matched, and
 *  `use-fn-form-wrapper.ts` binds `w` to a DOM element whose `.offsetHeight`
 *  is not a global read. */
function windowAliases(source) {
  const names = new Set();
  // The right-hand side must be `window` ITSELF — bare, or wrapped in `as`
  // casts — and nothing may follow it. Accepting a trailing `.` or `(` made
  // `const req = window.indexedDB.open(…)` and `const bridge =
  // window.openprogramDesktop` register as aliases, after which every
  // `req.result` / `bridge.windowId` was misreported as a global read.
  const re =
    /\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+?)?=\s*window(?:\s+as\s+[^;\n]+)?\s*;/g;
  let m;
  while ((m = re.exec(source))) names.add(m[1]);
  return names;
}

/** Property accesses reached through an INLINE cast — `(window as unknown as
 *  {…}).foo` — never touch a named alias, so they need their own pattern.
 *  This is the shape the `window.ws` bug shipped in. */
const INLINE_CAST_ACCESS =
  /\(\s*window\s+as\b[^)]*\)\s*\.\s*([A-Za-z_$][\w$]*)/g;

function escapeName(n) {
  return n.replace(/\$/g, "\\$");
}

function writePatterns(aliases) {
  const pats = [
    /\bwindow\s*\.\s*([A-Za-z_$][\w$]*)\s*(?:=[^=]|\+\+|--|\?\?=|\|\|=)/g,
    /\bdelete\s+window\s*\.\s*([A-Za-z_$][\w$]*)/g,
  ];
  for (const a of aliases) {
    const n = escapeName(a);
    pats.push(
      new RegExp(`\\b${n}\\s*\\.\\s*([A-Za-z_$][\\w$]*)\\s*(?:=[^=]|\\+\\+|--|\\?\\?=|\\|\\|=)`, "g"),
      new RegExp(`\\bdelete\\s+${n}\\s*\\.\\s*([A-Za-z_$][\\w$]*)`, "g"),
    );
  }
  return pats;
}

function readPatterns(aliases) {
  const pats = [
    /\bwindow\s*\.\s*([A-Za-z_$][\w$]*)/g,
    new RegExp(INLINE_CAST_ACCESS.source, "g"),
  ];
  for (const a of aliases) {
    pats.push(new RegExp(`\\b${escapeName(a)}\\s*\\.\\s*([A-Za-z_$][\\w$]*)`, "g"));
  }
  return pats;
}

const reads = []; // { name, path, line, text }

// Pass 1 collects writes from EVERY file before pass 2 judges any read —
// a global is legitimately written in one module and read in another.
for (const path of files) {
  const source = stripComments(readFileSync(path, "utf8"));
  const alias = windowAliases(source);
  for (const line of source.split("\n")) {
    for (const re of writePatterns(alias)) {
      let m;
      while ((m = re.exec(line))) assigned.add(m[1]);
    }
  }
}

// Pass 2 records the reads.
for (const path of files) {
  const source = stripComments(readFileSync(path, "utf8"));
  const alias = windowAliases(source);
  source.split("\n").forEach((line, i) => {
    for (const re of readPatterns(alias)) {
      let m;
      while ((m = re.exec(line))) {
        reads.push({
          name: m[1],
          path: path.slice(root.length),
          line: i + 1,
          text: line.trim(),
        });
      }
    }
  });
}

// A read is dead when nothing in the tree writes the name and the platform
// doesn't supply it.
const hits = reads
  .filter((r) => !assigned.has(r.name) && !PLATFORM_GLOBALS.has(r.name))
  .map((r) => `${r.path}:${r.line}: window.${r.name} — ${r.text}`);

assert.deepEqual(
  hits,
  [],
  "dead `window.<name>` read — nothing in web/ assigns these, so they are "
    + "permanently `undefined` at runtime. Read the real value from its "
    + "module singleton instead (e.g. `runtimeState` / `getSocket()` in "
    + `lib/runtime-bridge/state), or assign the global where it is owned:\n${hits.join("\n")}`,
);

console.log(
  `check-socket-access: ok (${reads.length} window reads, `
    + `${assigned.size} assigned globals)`,
);
