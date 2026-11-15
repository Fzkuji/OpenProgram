import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { readGlobalStyles } from "../../scripts/testing/feature-source.mjs";

// The official shadcn components all come from one style, radix-nova (the
// docs site's). The kit drifted through Luma and Rhea; these pins catch a
// part that slides back to another style's recipe.
const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("every components/ui part names radix-nova, none another style", () => {
  for (const part of ["command", "popover", "tooltip", "tabs", "kbd", "sidebar", "dialog", "alert-dialog", "breadcrumb", "context-menu", "dropdown-menu"]) {
    const source = read(`components/ui/${part}.tsx`);
    assert.match(source, /radix-nova/, part);
    assert.doesNotMatch(source, /radix-(rhea|luma)/, part);
  }
});

test("the kit's own controls size with the px tier tokens, never rem heights", () => {
  const button = read("components/ui/button.tsx");
  // rem heights (h-7, size-8 …) render at 7/8 on the 14px root; only
  // the px tokens give the tier heights.
  assert.doesNotMatch(button, /\b(h|size)-\d/, "Button sizes use the px tokens");
  for (const file of ["components/ui/toggle.tsx", "components/ui/input.tsx", "components/ui/select.tsx"]) {
    assert.doesNotMatch(read(file), /[":]h-[789]\b/, `${file} uses the px tokens`);
  }
});

// A hand-written button in a stylesheet takes its height from a tier
// token, never a literal px value, so a new button cannot start a sixth
// size. EXEMPT lists the controls that are not buttons of a tier, with
// the reason in surface-system.md ("Control height tiers").
const BUTTONISH = /(btn|button|Btn|Button|action|Action|close|Close|remove|Remove|toggle|Toggle|chip|Chip|swatch|Switch|pill|Pill|trigger|Trigger|\btab\b|\bTab\b|input|Input|search|Search|select|Select)/;
const NOT_A_BUTTON = /(svg|img|Icon\b|icon\b|::|dot|Dot|Row\b|row\b|bar\b|Bar\b|list|List|panel|Panel|strip\b|Strip\b|group|Group|wrap|Wrap)/;
const EXEMPT = [
  ".controlsRow :is(button", ".envChips :is(button", // icon glyph size inside the chip
  ".toolSwitchKnob", ".chatInput :global(.cm-md-chip)", // a switch knob; a chip inside the text
  ".pill{", ".effectsPreviewTab", // a label tag; a drawing of the tab strip
  ".browserActionCue", ".widthHandleButton", ".trigger", // a cursor cue; the 44px drag handle; the account footer row
  ".chatInput{", ".chatInput {", ".effort-input-thumb", // the composer text box; a slider thumb
];

function* cssFiles(dir) {
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) yield* cssFiles(rel);
    else if (entry.name.endsWith(".css")) yield rel;
  }
}

test("hand-written buttons take their height from a tier token", () => {
  const offenders = [];
  for (const file of [...cssFiles("components"), ...cssFiles("app")]) {
    const css = read(file).replace(/\/\*[\s\S]*?\*\//g, "");
    for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const last = selector.split(",").map((part) => part.trim().split(/\s+/).pop() ?? "");
      if (!last.some((part) => BUTTONISH.test(part) && !NOT_A_BUTTON.test(part))) continue;
      const height = body.match(/(?<![-\w])(?:height|min-height)\s*:\s*(\d+(?:\.\d+)?)px/);
      // Only control-sized values: textareas, checkboxes and glyphs are
      // not on the ladder.
      if (!height || +height[1] < 18 || +height[1] > 36) continue;
      const rule = selector.trim().replace(/\s+/g, " ");
      if (EXEMPT.some((exempt) => (rule + "{").includes(exempt))) continue;
      offenders.push(`${file}: ${rule.slice(0, 60)} ${height[1]}px`);
    }
  }
  assert.deepEqual(offenders, [], "use --ui-control-inline / --ui-button-h-xs / -sm / --ui-button-h / -lg");
});

// The same rule for class strings: a rem height (h-7, size-8) renders at
// 7/8 of its name on the 14px root and a px one (size-[22px]) starts
// a size of its own. Controls read the tier tokens instead
// (h-(--ui-button-h-sm), size-(--ui-button-h-xs) …). The exemptions are
// icon tiles and layout boxes, not controls.
const TSX_EXEMPT = new Set([
  "components/chat/recording/record-dialog.tsx size-7", // icon tile
  "components/projects/computer-use-apps-section.tsx size-8", // app icon tile
  "components/sidebar/nav-classes.ts h-[20px]", // icon slot
  "components/sidebar/nav-classes.ts size-[20px]", // icon slot
  "components/sidebar/sessions/sessions-list.tsx h-[24px]", // group header row
  "components/agents/agent-overview.tsx size-[32px]", // Agent avatar tile
  "components/right-sidebar/running/running-panel.tsx size-[22px]", // Agent avatar tile
]);
const OWN_SIZES = new Set(["button.tsx", "toggle.tsx", "input.tsx", "select.tsx"].map((name) => `components/ui/${name}`));

function* sourceFiles(dir) {
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) yield* sourceFiles(rel);
    else if (/\.tsx?$/.test(entry.name)) yield rel;
  }
}

test("class strings size controls with the tier tokens", () => {
  const offenders = [];
  for (const file of [...sourceFiles("components"), ...sourceFiles("app")]) {
    if (OWN_SIZES.has(file)) continue;
    for (const [utility] of read(file).matchAll(/(?<![\w:\-[])(?:(?:min-)?h|size)-(?:7|8|9|\[(?:2\d|3[0-4])px\])(?![\w\-\]])/g)) {
      if (!TSX_EXEMPT.has(`${file} ${utility}`)) offenders.push(`${file}: ${utility}`);
    }
  }
  assert.deepEqual(offenders, [], "use h-(--ui-button-h…) / size-(--ui-button-h…)");
});

test("a filled Button shows no light ring between its fill and its shadow", () => {
  // The fill runs under the transparent border; clipping it to the padding
  // box leaves a 1px ring of the page showing between fill and shadow.
  assert.doesNotMatch(read("components/ui/button.tsx"), /bg-clip-padding/);
});

test("cn knows every text-fs-* size, so it keeps the text colour beside one", () => {
  const steps = [...readGlobalStyles().matchAll(/--text-(fs-[\w]+):/g)].map((match) => match[1]);
  assert.ok(steps.length > 5);
  const utils = read("lib/utils.ts");
  for (const step of steps) assert.match(utils, new RegExp(`"${step}"`), step);
});
