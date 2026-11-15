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
 *     trap, or focus restore. `lib/hooks/use-modal-a11y.ts#useModalA11y`
 *     supplies all three; this checks each such panel actually calls it.
 */
import assert from "node:assert/strict";
import ts from "typescript";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));

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

/* ---- 1) role="button" implies a tab stop + key activation ---------- */

// Parse JSX so generics inside expression attributes cannot split a tag.
const roleGaps = [];
for (const file of files) {
  const tree = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const attributes = (node) => node.attributes.properties.filter(ts.isJsxAttribute);
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const attrs = attributes(node);
      const role = attrs.find((attr) => attr.name.getText(tree) === "role");
      const value = role?.initializer;
      const literal = value && ts.isJsxExpression(value) ? value.expression : value;
      if (literal && ts.isStringLiteral(literal) && literal.text === "button") {
        const element = ts.isJsxOpeningElement(node) ? node.parent : node;
        const parent = element.parent;
        const trigger = ts.isJsxElement(parent) ? parent.openingElement : null;
        const radixChild = trigger && /Trigger$/.test(trigger.tagName.getText(tree))
          && attributes(trigger).some((attr) => attr.name.getText(tree) === "asChild");
        const names = new Set(attrs.map((attr) => attr.name.getText(tree)));
        if (!radixChild && (!names.has("tabIndex") || !names.has("onKeyDown"))) {
          roleGaps.push(`${file.slice(root.length)}: role="button" without ` +
            `${names.has("tabIndex") ? "" : "tabIndex "}${names.has("onKeyDown") ? "" : "onKeyDown"}`.trim());
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
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
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let dismissibleBackdrop = false;
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const attrs = node.attributes.properties.filter(ts.isJsxAttribute);
      const className = attrs.find(attr => attr.name.getText(tree) === "className");
      if (className && /[Bb]ackdrop/.test(className.getText(tree))
          && attrs.some(attr => attr.name.getText(tree) === "onClick")) {
        dismissibleBackdrop = true;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  if (!dismissibleBackdrop) continue;
  if (src.includes("useModalA11y")) continue;
  modalGaps.push(file.slice(root.length));
}
assert.deepEqual(
  modalGaps,
  [],
  "hand-rolled modal backdrops must call useModalA11y (lib/hooks/use-modal-a11y) " +
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

const modalHook = readFileSync(root + "lib/hooks/use-modal-a11y.ts", "utf8");
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
