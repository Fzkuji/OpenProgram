// CSS rules of the center tab strip that no node test can see (transitions,
// stacking, pointer-events, drag regions): the follow-the-pointer tab never
// transitions transform, raised layers keep their z-index, overlays never
// block a drop. Strip behaviour lives in tests/tabs/tab-*.test.mjs.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import postcss from "postcss";

const web = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, web), "utf8");
const css = [
  "components/center-tabs/strip/tab-strip.module.css",
  "components/center-tabs/browser/browser.module.css",
  "components/center-tabs/pages/pages.module.css",
].map(read).join("\n");
const baseCss = readdirSync(new URL("app/styles/base/", web)).filter((name) => name.endsWith(".css")).sort()
  .map((name) => read(`app/styles/base/${name}`)).join("\n");
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");
/** Every declaration of `property` in rules listing `selector`, in source order. */
function declarations(selector, property) {
  const values = [];
  postcss.parse(css).walkRules((node) => {
    if (node.selectors?.includes(selector)) node.walkDecls(property, (decl) => values.push(decl.value));
  });
  return values;
}
function rule(selector) {
  const start = css.indexOf(`${selector} {`);
  assert.ok(start >= 0, `${selector} rule not found`);
  return stripComments(css.slice(start, css.indexOf("}", start) + 1));
}

// 1. The dragged tab's transform is written by JS every pointermove: never transition it.
const dragTransitions = declarations('.tab[data-pointer-drag="true"]', "transition");
assert.ok(dragTransitions.length > 0, "the dragged tab still eases its other properties");
for (const value of dragTransitions) {
  assert.doesNotMatch(value, /\btransform\b|\ball\b/, "the follow-the-pointer tab must never transition transform");
}
// 2. ...and it is lifted above the sliding bystanders (both a tab and a split tab).
for (const selector of ['.tab[data-pointer-drag="true"]', '.compoundTab[data-pointer-drag="true"]']) {
  assert.match(declarations(selector, "z-index").at(-1) ?? "", /--z-drag|^\d+$/, `${selector} must be raised`);
}
// 3. Bystanders slide aside by transform only, with a non-bouncy curve.
assert.match(css, /\.tab \{[^}]*transition: transform var\(--motion-medium\) var\(--ease-standard\);/s,
  "the base .tab transition stays transform-only (the live reorder slide)");
assert.match(css, /\.compoundTab \{[^}]*transition:[^}]*transform var\(--motion-medium\) var\(--ease-standard\);/s);
assert.doesNotMatch(css, /transition:[^;]*cubic-bezier\([^)]*-[\d.]/, "no overshooting curve on the strip");
assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*\.tab,\s*\.compoundTab \{\s*transition-duration: 0ms;/s);
// 4. Leaving the strip never shrinks or outlines the dragged tab.
assert.doesNotMatch(css, /\[data-detach-intent="true"\] \{[^}]*(outline|scale):/);
// 5. Close-with-the-mouse freeze: only RELEASED widths ease back; the pinned state must not
//    transition width (it would balloon the row from the stylesheet value for a frame).
assert.match(css, /\.tab\[data-width-frozen="released"\],\s*\.compoundTab\[data-width-frozen="released"\] \{[^}]*width var\(--motion-medium\)[^}]*flex-basis var\(--motion-medium\)[^}]*max-width var\(--motion-medium\)/s);
assert.doesNotMatch(css, /\.tab\[data-width-frozen\](?!=)[^,{]*[,{]/, "the width transition must not match the pinned state");
// 6. A split tab: the one role=tab target sits over the members; the close button above it.
assert.match(css, /\.compoundTarget \{[^}]*z-index:\s*1;/s);
assert.match(css, /\.compoundMembers \{[^}]*pointer-events:\s*none;/s);
assert.match(css, /\.compoundMemberClose \{[^}]*position:\s*relative;[^}]*z-index:\s*2;[^}]*pointer-events:\s*auto;/s);
// 7. The cross-window drop cue lives in the strip band and never blocks the drop.
const pill = rule(".transferHoverPill");
assert.match(pill, /pointer-events: none/);
assert.doesNotMatch(pill, /inset:\s*0/, "the cue must not span the content area");
assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*\.transferHoverPill \{\s*animation: none/);
assert.match(css, /\.strip\[data-transfer-hover\][\s\S]*?box-shadow: inset 0 -2px 0 var\(--accent-orange\)/);
assert.match(css, /:global\(\.desktop-tab-row\):has\(\.strip\[data-transfer-hover\]\)/, "the traffic-light inset tints too");
// 8. Desktop drag region: the strip controls stay clickable and the window edges resizable.
assert.match(css, /:global\(html\.is-desktop\) \.plusBtn,\s*:global\(html\.is-desktop\) \.menuBtn \{[^}]*-webkit-app-region: no-drag;|:global\(html\.is-desktop\) \.menuBtn \{\s*-webkit-app-region: no-drag;/s);
assert.match(baseCss, /html\.is-desktop \.desktop-resize-edge \{[\s\S]*?-webkit-app-region: no-drag;/);
assert.match(baseCss, /html\.is-desktop \.desktop-resize-n \{ top: 0; left: 0; right: 0; height: 5px; \}/);
// 9. The desktop tab flow scrolls sideways without a visible scrollbar.
assert.match(css, /:global\(html\.is-desktop\) \.tabsFlow \{[^}]*overflow-x: auto;[^}]*overflow-y: hidden;[^}]*scrollbar-width: none;/s);
