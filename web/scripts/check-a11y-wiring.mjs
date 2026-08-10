/** Keyboard reachability + screen-reader labelling must not regress.
 *
 *  Two failure modes this guards, both of which look fine on screen and
 *  are invisible to a mouse user, so nothing else catches them:
 *
 *  1. An element with `role="button"` on a non-button tag. The role only
 *     changes what a screen reader announces — it does NOT make the
 *     element focusable and does NOT make Enter/Space fire onClick. So a
 *     role without `tabIndex` + `onKeyDown` is a control a keyboard user
 *     cannot reach at all. `lib/utils.ts#activateOnKey` is the shared
 *     handler; this checks every role="button" site pairs with both.
 *
 *  2. A hand-rolled modal (a backdrop <div>, not components/ui/dialog —
 *     which is Radix and already does this) that lacks Escape, a Tab
 *     trap, or focus restore. `lib/use-modal-a11y.ts#useModalA11y`
 *     supplies all three; this checks each such panel actually calls it.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next") continue;
    const p = dir + "/" + name;
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

const files = [...walk(root + "components"), ...walk(root + "app")];

/** Index just past a JSX opening tag's `>`. Brace-depth aware, so the
 *  `>` in `onClick={() => …}` (and any comparison inside an expression
 *  attribute) does not end the tag early. */
function tagEnd(chunk) {
  let depth = 0;
  for (let i = 0; i < chunk.length; i++) {
    const c = chunk[i];
    if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (c === ">" && depth === 0) return i + 1;
  }
  return chunk.length;
}

/* ---- 1) role="button" implies a tab stop + key activation ---------- */

// Element-by-element rather than per-file: a file may hold several, and
// only the ones carrying the role are subject to the rule.
const roleGaps = [];
for (const file of files) {
  const src = readFileSync(file, "utf8");
  // Split on tag openings so each chunk holds one element's attributes.
  const parts = src.split(/<(?=[A-Za-z])/);
  for (let n = 0; n < parts.length; n++) {
    const chunk = parts[n];
    if (!/role=\{?["']button["']/.test(chunk)) continue;
    // A Radix `asChild` trigger clones tabIndex + the key handlers onto
    // its single child at runtime, so that child is already reachable —
    // writing them out again would be dead weight. Recognised by the
    // immediately preceding tag.
    if (/Trigger[\s\S]*\basChild\b[^<]*$/.test(parts[n - 1] ?? "")) continue;
    // Only the attribute list, not the children that follow it. The tag
    // ends at the first `>` that is not part of an arrow function (`=>`)
    // or a comparison inside a JSX expression — scan and skip those.
    const attrs = chunk.slice(0, tagEnd(chunk));
    const focusable = /tabIndex=/.test(attrs);
    const activates = /onKeyDown=/.test(attrs);
    if (!focusable || !activates) {
      roleGaps.push(
        `${file.slice(root.length)}: role="button" without ` +
          `${focusable ? "" : "tabIndex "}${activates ? "" : "onKeyDown"}`.trim(),
      );
    }
  }
}
assert.deepEqual(
  roleGaps,
  [],
  'every role="button" needs tabIndex + onKeyDown (use activateOnKey from ' +
    "lib/utils) — the role alone leaves the control keyboard-unreachable:\n" +
    roleGaps.join("\n"),
);

/* ---- 2) hand-rolled modal panels use the shared a11y hook --------- */

const modalGaps = [];
for (const file of files) {
  const src = readFileSync(file, "utf8");
  // The tell for a hand-rolled modal: a backdrop element whose click
  // dismisses it. Radix-based dialogs never look like this.
  if (!/className=\{?["'`]?[^"'`\n]*[Bb]ackdrop/.test(src)) continue;
  if (src.includes("useModalA11y")) continue;
  modalGaps.push(file.slice(root.length));
}
assert.deepEqual(
  modalGaps,
  [],
  "hand-rolled modal backdrops must call useModalA11y (lib/use-modal-a11y) " +
    "for Escape + Tab trap + focus restore, or be rebuilt on " +
    "components/ui/dialog:\n" +
    modalGaps.join("\n"),
);

/* ---- 3) the two helpers keep the behaviour they promise ----------- */

const utils = readFileSync(root + "lib/utils.ts", "utf8");
assert.match(
  utils,
  /e\.key !== "Enter" && e\.key !== " "/,
  "activateOnKey must handle BOTH Enter and Space — that is the native " +
    "button contract it exists to reproduce",
);

const modalHook = readFileSync(root + "lib/use-modal-a11y.ts", "utf8");
for (const [needle, why] of [
  [/e\.key === "Escape"/, "Escape must close the panel"],
  [/e\.key !== "Tab"/, "Tab must be trapped inside the panel"],
  [/returnTo\.current\?\.focus/, "focus must return to the trigger on close"],
]) {
  assert.match(modalHook, needle, `useModalA11y: ${why}`);
}

console.log(
  `check-a11y-wiring: ok (${files.length} components scanned)`,
);
