// Rule: the environment row's squeeze levels are pure CSS on the data-* states
// useCompactEnvironmentRow sets (tested in tests/chat/composer/compact-environment-row.test.mjs):
// labels animate out (max-width/opacity, never display:none), and the
// measuring pass lays chips out at natural width with transitions off.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(
  new URL("../../components/chat/composer/environment-row/environment-row.module.css", import.meta.url),
  "utf8",
);

// Measuring: the right-aligned chip's auto margin is zeroed so the last
// chip's edge is the real need, and nothing animates while measuring.
assert.match(css, /\.envChips\[data-measuring\] > \.surfaceChip[^{]*\{\s*margin-left:\s*0 !important/);
assert.match(css, /\.envChips\[data-measuring\][\s\S]*transition:\s*none !important/);

// Squeeze levels cap, then hide, the git branch label; compact keeps the diff dot.
assert.match(css, /\.envChips\[data-squeeze\][^{]*\.git-seg-label\)[^{]*\{\s*max-width:\s*12ch/);
assert.match(css, /\.envChips\[data-squeeze="2"\] :global\(\.git-seg-label\)/);
assert.match(css, /\.envChips\[data-compact="true"\] :global\(\.git-seg-dot\)\s*\{[^}]*width:\s*8px[^}]*opacity:\s*1/);

// Labels collapse by animating max-width and opacity to 0.
assert.match(css, /transition:[\s\S]*max-width 170ms ease[\s\S]*opacity 130ms ease/);
assert.match(css, /\.envChips\[data-compact="true"\][\s\S]*max-width:\s*0/);
assert.match(css, /\.envChips\[data-compact="true"\][\s\S]*opacity:\s*0/);
assert.doesNotMatch(css.slice(css.indexOf('.envChips[data-compact="true"]')), /display:\s*none/,
  "compact labels must animate out, not disappear with display:none");

console.log("composer environment-row CSS check passed");
