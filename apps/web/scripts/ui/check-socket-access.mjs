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
import ts from "typescript";

// fileURLToPath, not .pathname — a repo path containing a space arrives
// percent-encoded otherwise and every readdir misses.
const root = fileURLToPath(new URL("../../", import.meta.url));
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist", "build"]);

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

const reads = []; // { name, path, line, text }

/** Return the expression after removing the casts used by window aliases. */
function unwrapWindowCast(node) {
  while (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) {
    node = node.expression;
  }
  return node;
}

/** Names bound to `window` by a cast in this file. Property access is collected
 * from the AST so strings, import paths, comments, and unrelated local names
 * cannot become globals accidentally. */
function windowAliases(sourceFile) {
  const names = new Set();
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.initializer
      && ts.isIdentifier(node.name)
      && ts.isIdentifier(unwrapWindowCast(node.initializer))
      && unwrapWindowCast(node.initializer).text === "window") {
      names.add(node.name.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return names;
}

function isWindowBase(node, aliases) {
  const base = unwrapWindowCast(node);
  return ts.isIdentifier(base) && (base.text === "window" || aliases.has(base.text));
}

function isWindowWrite(node) {
  const parent = node.parent;
  if (ts.isBinaryExpression(parent) && parent.left === node) {
    return ts.isAssignmentOperator(parent.operatorToken.kind);
  }
  if (ts.isPrefixUnaryExpression(parent) || ts.isPostfixUnaryExpression(parent)) {
    return parent.operator === ts.SyntaxKind.PlusPlusToken || parent.operator === ts.SyntaxKind.MinusMinusToken;
  }
  return ts.isDeleteExpression(parent);
}

function collectAccesses(sourceFile, path, target = reads) {
  const aliases = windowAliases(sourceFile);
  const lines = sourceFile.text.split("\n");
  function visit(node) {
    if (ts.isPropertyAccessExpression(node) && isWindowBase(node.expression, aliases)) {
      const line = sourceFile.getLineAndCharacterOfPosition(node.name.getStart(sourceFile)).line + 1;
      target.push({
        name: node.name.text,
        path: path.slice(root.length),
        line,
        text: lines[line - 1].trim(),
      });
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
}

/** Keep the parser boundary covered by the checker itself. These cases are
 * deliberately source-shaped so a future scanner change cannot regress into
 * matching text inside strings/import paths or following unrelated objects. */
function runScannerSelfChecks() {
  const parse = (source) => ts.createSourceFile(
    "socket-access-self-check.ts",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const accesses = (source) => {
    const found = [];
    collectAccesses(parse(source), "socket-access-self-check.ts", found);
    return found.map(({ name }) => name);
  };

  assert.deepEqual(
    accesses('import styles from "./document-window.module.css";\nconst message = "window. Close";'),
    [],
    "scanner self-check: import paths and strings must not become window reads",
  );

  const missing = accesses([
    "window.missingPlain;",
    "const alias = window as unknown as { missingAlias?: string };",
    "alias.missingAlias;",
    "(window as unknown as { missingInline?: string }).missingInline;",
  ].join("\n"));
  assert.deepEqual(
    missing.filter((name) => !PLATFORM_GLOBALS.has(name)),
    ["missingPlain", "missingAlias", "missingInline"],
    "scanner self-check: plain, alias, and inline-cast missing globals must be found",
  );

  assert.deepEqual(
    accesses("const bridge = window.openprogramDesktop;\nbridge.windowId;"),
    ["openprogramDesktop"],
    "scanner self-check: a property read must not become a window alias",
  );
}

runScannerSelfChecks();

// Pass 1 collects writes from EVERY file before pass 2 judges any read —
// a global is legitimately written in one module and read in another.
for (const path of files) {
  const source = readFileSync(path, "utf8");
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true,
    path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const aliases = windowAliases(sourceFile);
  function visit(node) {
    if (ts.isPropertyAccessExpression(node) && isWindowBase(node.expression, aliases) && isWindowWrite(node)) {
      assigned.add(node.name.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
}

// Pass 2 records the reads.
for (const path of files) {
  const source = readFileSync(path, "utf8");
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true,
    path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  collectAccesses(sourceFile, path);
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
