// The composer's fill rule: inside the composer and the popups it opens,
// an icon's ink fills a 16 px box and the box grows to exactly 18 px on
// hover. It is opt-in (SolarIcon fit="fill", BrandMark fit="fill",
// AgentAvatar fit="mark", FillIcon), so every other icon keeps its whole
// view box and the 1.12 pop.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "node:test";
import { createElement as h } from "react";
import { installLoader, webRoot } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader();
installDom();

const { generate, solarGlyphs, OUTPUT } = await import("../../scripts/icons/generate-solar-ink-bounds.mjs");
const { SOLAR_INK } = await import("../../components/solar-icons/ink-bounds.ts");
const { SOLAR_BODIES } = await import("../../components/solar-icons/bodies.ts");
const { SolarIcon, solarFillViewBox } = await import("../../components/solar-icons/solar-icon.tsx");
const { inkViewBox } = await import("../../components/ui/fill-icon.tsx");

const root = fileURLToPath(webRoot);
const source = (path) => readFileSync(join(root, path), "utf8");

describe("Solar ink bounds", () => {
  test("every glyph has an entry, and nothing else does", () => {
    assert.deepEqual(Object.keys(SOLAR_INK), Object.keys(SOLAR_BODIES));
    assert.deepEqual(solarGlyphs().map((glyph) => glyph.name), Object.keys(SOLAR_BODIES));
  });

  test("the committed file is what the generator writes now", async () => {
    assert.equal(readFileSync(OUTPUT, "utf8"), await generate(), "run: npm run generate:solar-ink-bounds --workspace apps/web");
  });

  test("a filled glyph's view box is the square around its ink, centred on it", () => {
    for (const name of Object.keys(SOLAR_INK)) {
      const ink = SOLAR_INK[name];
      const [x, y, side] = solarFillViewBox(name).split(" ").map(Number);
      assert.ok(Math.abs(side - Math.max(ink.width, ink.height)) < 0.002, name);
      assert.ok(Math.abs(x + side / 2 - (ink.x + ink.width / 2)) < 0.002, `${name} centre x`);
      assert.ok(Math.abs(y + side / 2 - (ink.y + ink.height / 2)) < 0.002, `${name} centre y`);
    }
    // fire: 16.99 x 19.45 units of ink, so its height fills the box.
    assert.equal(solarFillViewBox("fire"), "1.77 2.55 19.45 19.45");
  });
});

describe("SolarIcon", () => {
  async function svgOf(props) {
    const view = await render(h(SolarIcon, props));
    const svg = view.container.querySelector("svg");
    const result = { viewBox: svg.getAttribute("viewBox"), style: svg.getAttribute("style"), fit: svg.getAttribute("data-icon-fit") };
    await view.unmount();
    return result;
  }

  test("by default it draws the whole view box at the size CSS gives it", async () => {
    const plain = await svgOf({ name: "fire", size: 16 });
    assert.equal(plain.viewBox, "0 0 24 24");
    assert.doesNotMatch(plain.style, /width/);
    assert.equal(plain.fit, null);
  });

  test("fit=fill crops to the ink and pins the box to its size", async () => {
    const filled = await svgOf({ name: "fire", size: 16, fit: "fill" });
    assert.equal(filled.viewBox, solarFillViewBox("fire"));
    assert.match(filled.style, /width:16px;height:16px/);
    assert.equal(filled.fit, "fill");
  });

  test("only a filled glyph pops to 18 px; the paper plane grows like the rest and does not glide", () => {
    const text = source("components/solar-icons/solar-icon.tsx");
    assert.match(text, /\bpop: HOVER_POP,/);
    assert.match(text, /\bpop: HOVER_POP_FILL,/);
    assert.doesNotMatch(text, /\bfly\b/);
    assert.match(text, /fit = "viewBox"/);
    assert.match(source("components/chat/composer/icons.tsx"), /name="plain-2" size=\{16\} \/>/, "the plane grows like the other glyphs and does not glide");
  });
});

describe("FillIcon", () => {
  const svg = (box, strokes = []) => ({
    getBBox: () => box,
    querySelectorAll: () => strokes.map((width) => ({ width })),
  });

  test("an svg is cropped to the square around its geometry plus half its stroke", () => {
    const original = globalThis.getComputedStyle;
    globalThis.getComputedStyle = (shape) => ({ stroke: "currentColor", strokeWidth: `${shape.width}px` });
    try {
      // lucide ArrowLeft: geometry 5..19 both ways, stroke 2.
      assert.equal(inkViewBox(svg({ x: 5, y: 5, width: 14, height: 14 }, [2])), "4 4 16 16");
      // A wide drawing keeps its centre.
      assert.equal(inkViewBox(svg({ x: 2, y: 8, width: 20, height: 8 })), "2 2 20 20");
      assert.equal(inkViewBox(svg({ x: 0, y: 0, width: 0, height: 0 })), null);
    } finally {
      globalThis.getComputedStyle = original;
    }
  });

  test("it grows to exactly 18 px on its control's hover", () => {
    const text = source("components/ui/fill-icon.tsx");
    assert.match(text, /variants=\{HOVER_POP_FILL\}/);
    assert.match(text, /followHostHover\(/);
  });
});

describe("scope", () => {
  function sources(directory) {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sources(path);
      return /\.tsx?$/.test(entry.name) ? [path] : [];
    });
  }

  const files = ["app", "components", "lib"]
    .flatMap((folder) => sources(join(root, folder)))
    .map((path) => relative(root, path).split("\\").join("/"));

  // The composer, its chips and menus, and the cards it opens.
  const COMPOSER = [
    "components/chat/composer/",
    "components/chat/top-bar/",
    "components/chat/badges/context-badge.tsx",
    "components/chat/badges/context-breakdown-panel.tsx",
    "components/chat/badges/goal-chip.tsx",
  ];
  const inComposer = (file) => COMPOSER.some((prefix) => file.startsWith(prefix));
  // The components that offer the rule (and name it in their comments).
  const OFFER = new Set([
    "components/solar-icons/solar-icon.tsx",
    "components/brand-mark/brand-mark.tsx",
    "components/channels/channel-logo.tsx",
    "components/ui/fill-icon.tsx",
  ]);

  test("the fill rule is used only in the composer and its popups", () => {
    const outside = files
      .filter((file) => !inComposer(file) && !OFFER.has(file))
      .filter((file) => /fit="fill"|<FillIcon\b|fit="mark"/.test(source(file)));
    assert.deepEqual(outside, []);
  });

  test("an Agent character fills its slot only in the Agent chip and the Agent menu", () => {
    const users = files.filter((file) => /fit="mark"/.test(source(file)));
    assert.deepEqual(users, ["components/chat/top-bar/menus/agent-config-menu.tsx"]);
    const bot = source("components/avatar/bot/bot-avatar.ts");
    assert.match(bot, /fit: fitRule = "avatar"/);
    assert.match(bot, /variants: fitRule === "mark" \? HOVER_POP_FILL : HOVER_POP,/);
  });

  test("the stop square is untouched: no fill, 8 px of a 16 px box", () => {
    const icons = source("components/chat/composer/icons.tsx");
    assert.match(icons, /<svg viewBox="0 0 24 24">\s*<rect x="6" y="6" width="12" height="12" rx="2" \/>/);
  });

  test("an Agent picture, letter tile or DiceBear avatar pops to 18 px in the chip and menu too", () => {
    const avatar = source("components/avatar/avatar.tsx");
    assert.match(avatar, /const placed = \(avatar: ReactNode\) => fit === "mark"\s*\? <MarkPop size=\{size\}>/);
    assert.equal((avatar.match(/return placed\(/g) ?? []).length, 3);
    assert.match(avatar, /variants=\{HOVER_POP_FILL\}/);
  });


  test("the collapsed effort capsule's fire is centred: 8 + 16 + 8 = 32 px", () => {
    const pill = source("components/chat/composer/controls/effort/thinking-effort-pill.tsx");
    assert.match(pill, /"effort-pill-collapsed h-full flex items-center px-\[8px\] cursor-pointer"/);
  });

  test("the Goal chip uses the Solar target, filled", () => {
    const goal = source("components/chat/badges/goal-chip.tsx");
    assert.match(goal, /<SolarIcon name="target" size=\{16\} fit="fill" className="workdir-icon" \/>/);
    assert.doesNotMatch(goal, /\bTarget\b/);
  });
});
