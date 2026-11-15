// The design system's guard. Every size, shape and ink comes from the
// tiers in app/styles/design/ (docs/reference/design/ui/visual-system/design-tokens.html);
// a value written by hand is how pages drifted apart before, so this
// refuses one in any stylesheet or .ts / .tsx file:
//
//   type     font sizes are --fs-* tiers (text-fs-*), never px, rem, an
//            arbitrary text-[…] or the stock text-xs … text-2xl; Settings
//            owns the typeface and equal-width digits.
//   weight   400 / 500 / 600 / 700 only.
//   radius   --radius-* tiers (rounded-*), never a px literal or an
//            arbitrary rounded-[…].
//   ink      text colour is one of the four inks; the retired aliases
//            (--text-tertiary, --text-dim) and duplicate spellings are
//            refused outside the shadcn registry parts.
//   icons    a numeric `size` prop is an icon tier (12 14 16 20 24) or an
//            illustration (28 and up).
//   layers   layout goes in Tailwind classes in the JSX; a CSS module keeps
//            what Tailwind cannot say (pseudo-elements, state and parent
//            selectors, keyframes). A module rule that is one class with
//            only layout properties is counted per file, and the count may
//            only go down (scripts/ui/module-layout-baseline.json; run with
//            --write-layout-baseline after moving such rules out).
//   colour   a colour literal (#hex, rgb(), hsl()) is written only where a
//            colour token is defined: app/styles/themes/, app/styles/design/
//            and custom-property lines in app/styles/. Components name a
//            token; data palettes, drawing APIs and artwork are listed below.
//
// A line that is a deliberate exception says why with `glyph:` (a
// character that is not text) or `shape:` (a radius taken from another
// surface) in a comment on the same line.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));

// The files that define the stacks and apply the setting.
const OWNERS = new Set([
  "app/fonts.ts",
  "app/globals.css",
  "app/layout.tsx",
  "app/styles/design/type.css",
  "app/styles/design/tailwind.css",
  "app/styles/base/document.css",
  "lib/prefs/appearance/font-stacks.ts",
  "lib/prefs/appearance/font-pref.ts",
  "components/settings/sections/general-section.tsx",
]);

// The Code rain theme is pixel art: its glyphs, the welcome canvas and the
// Settings swatch share one terminal face on purpose, whatever the setting.
const ART = new Set([
  "app/styles/chat/welcome/code-rain-style.css",
  "components/settings/settings-page.module.css",
]);

// APIs that take a number of pixels, not CSS: xterm and Mermaid measure
// text themselves, and a canvas `ctx.font` rejects var().
const NUMERIC_SIZE = new Set([
  "components/center-tabs/pages/terminal-page.tsx",
  "components/session-resources/terminal-resources.tsx",
  "lib/chat/markdown/mermaid.ts",
  "lib/documents/raster-editor.ts",
]);

// Colour literals that are data or drawing, not interface colour: user-
// picked palettes, chart and lane series, artwork, and APIs that take a
// colour string (canvas, WebGL, xterm, Mermaid, pdf.js, SVG masks, the
// web manifest). Their fallbacks are what those APIs draw with.
const COLOUR_DATA = new Set([
  "app/manifest.ts",
  "app/menu-overlay/agent-control/page.tsx",
  "app/styles/chat/welcome/code-rain-style.css",
  // Agent characters keep their own fixed colours, like a generated avatar.
  "components/avatar/bot/catalog.ts",
  "components/center-tabs/pages/launchpad/launchpad-icons.tsx",
  "components/files/file-type-icon.module.css",
  "components/files/preview/pdf-preview.tsx",
  "components/memory/icons.tsx",
  "components/right-sidebar/running/program-progress.tsx",
  "components/settings/search-providers/glyph.tsx",
  "components/settings/token-usage/index.tsx",
  "lib/chat/code-rain.ts",
  "lib/chat/markdown/mermaid.ts",
  "lib/documents/raster-editor.ts",
  // A computed heat scale: hsl() built from the effort level.
  "lib/effort/effort-color.ts",
  // A web page's own colour, read from a picture of it on a canvas.
  "lib/browser/web-tab/picture-edge.ts",
  "lib/prefs/appearance/theme-config.ts",
  "lib/prefs/user-profile.ts",
  "lib/runtime-bridge/dag/interaction/view/minimap.ts",
]);
const COLOUR_OWNERS = /^app\/styles\/(?:themes|design)\//;
const COLOUR_LITERAL = /(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])|\b(?:rgba?|hsla?)\(/;

// The tiers, read from their definitions so the lists cannot fall behind.
const tiersIn = (file, pattern) =>
  new Set([...readFileSync(join(root, file), "utf8").matchAll(pattern)].map((m) => m[1]));
const TIERS = tiersIn("app/styles/design/type.css", /^\s*--fs-([\w-]+):/gm);
const RADII = tiersIn("app/styles/design/shape.css", /^\s*--radius-([\w-]+):\s*\d/gm);
const WEIGHTS = new Set(["400", "500", "600", "700"]);
const ICON_SIZES = new Set([12, 14, 16, 20, 24]);

// The input box is hands-off: its radii and weights stay as drawn.
const HANDS_OFF = /^(?:components\/chat\/composer|app\/styles\/chat\/composer)\//;
// Framer Motion animates a numeric radius between layouts; a var() would
// jump instead of morphing.
const MOTION_RADIUS = new Set(["components/agents/agent-overview.tsx", "components/agents/agents-page.tsx"]);
// APIs that take a radius in pixels (Electron views, canvas).
const NUMERIC_RADIUS = /^lib\/(?:desktop|browser|runtime-bridge)\//;

// A radius is a tier, a share of the box (50%, a squircle's 26%), 0 or a
// keyword;
// calc() over a tier is a concentric corner.
function literalRadius(value) {
  return value
    .replace(/var\(--(?:radius-[\w-]+|ui-[\w-]+-radius|composer-corner|glass-radius)\)/g, "")
    .replace(/!important|\b(?:inherit|initial|unset|revert(?:-layer)?)\b/g, "")
    .replace(/[\d.]+%|\b0(?:px)?\b/g, "")
    .replace(/calc\([^)]*var\(--[^)]*\)[^)]*\)|calc\(|[()*+\-\/,\s]/g, "")
    .trim() !== "";
}

// A size is a scale step, or relative to its parent (em, %, keywords).
// Anything else is a literal; a glyph that is not text says so on its line.
function literalSize(value) {
  const rest = value
    .replace(/var\(--fs-[\w-]+\)/g, "")
    .replace(/!important|\b(?:inherit|initial|unset|revert(?:-layer)?|smaller|larger)\b/g, "")
    .replace(/var\(--[\w-]+-size(?:,\s*)?\)/g, "")
    .replace(/-?[\d.]+(?:em|%)|\b0\b/g, "")
    .replace(/calc\(|[()*+\-\/,\s]/g, "")
    .trim();
  return rest !== "";
}

function* walk(dir) {
  for (const name of readdirSync(join(root, dir))) {
    const path = join(dir, name);
    if (statSync(join(root, path)).isDirectory()) yield* walk(path);
    else if ([".css", ".ts", ".tsx"].includes(extname(name))) yield path;
  }
}

// What is left of a font value once the shared variables, sizes, weights
// and keywords are taken out; anything left is a font named by hand.
function stray(value) {
  return value
    .replace(/var\(--font-(?:sans|mono)[^)]*\)/g, "")
    .replace(/!important|\b(?:inherit|initial|unset|revert(?:-layer)?|normal|italic|bold(?:er)?|lighter)\b/g, "")
    .replace(/[\d.]+(?:px|em|rem|%)?|var\([^)]*\)|calc\([^)]*\)|[\/,\s]/g, "")
    .trim();
}

const problems = [];
for (const dir of ["app", "components", "lib"]) {
  for (const path of walk(dir)) {
    if (OWNERS.has(path)) continue;
    const lines = readFileSync(join(root, path), "utf8").split("\n");
    lines.forEach((line, index) => {
      const at = `${relative(root, join(root, path))}:${index + 1}`;
      if (line.trim().startsWith("//") || line.trim().startsWith("*")) return;
      for (const m of line.matchAll(/font-family\s*:\s*([^;}"`]*)/g)) {
        if (stray(m[1]) && !ART.has(path)) problems.push(`${at} names a font: ${m[0].trim()}`);
      }
      if (!/glyph:/.test(line)) {
        for (const m of line.matchAll(/font-size\s*:\s*([^;}"`]*)/g)) {
          if (literalSize(m[1])) problems.push(`${at} sets a literal size; use var(--fs-*): ${m[0].trim()}`);
        }
      }
      if (/\btext-\[(?!var\(--fs-)[^\]]*\d(?:px|rem|em)\]/.test(line)) {
        problems.push(`${at} uses an arbitrary Tailwind size; use text-fs-*`);
      }
      // The stock sizes once meant one px outside .shadcn-scale and
      // another inside it; a tier name means the same everywhere.
      if (/(?:^|[\s"'`:!])text-(?:xs|sm|base|lg|[2-9]?xl)\b(?![-\w\[])/.test(line)) {
        problems.push(`${at} uses a stock Tailwind size; use text-fs-*`);
      }
      for (const m of line.matchAll(/(?:var\(--fs-([\w-]+)\)|\btext-fs-([\w-]+))/g)) {
        if (!TIERS.has(m[1] ?? m[2])) problems.push(`${at} names ${m[0].replace("var(", "")}, which is not a type tier`);
      }
      if (!NUMERIC_SIZE.has(path) && !/glyph:/.test(line)) {
        for (const m of line.matchAll(/fontSize\s*:\s*("[^"]*"|'[^']*'|`[^`]*`|[^,}\n]+)/g)) {
          const value = m[1].trim().replace(/^["'`]|["'`]$/g, "");
          if (/^[\d.]+$/.test(value) || literalSize(value)) problems.push(`${at} sets a literal size; use "var(--fs-*)": ${m[0].trim()}`);
        }
      }
      if (path.endsWith(".css")) {
        // The shorthand also resets font-variant-numeric, which silently
        // switches equal-width digits off for the whole subtree.
        for (const m of line.matchAll(/(?:^|[;{\s])font\s*:\s*([^;}]*)/g)) {
          if (!/^(?:inherit|revert-layer)$/.test(m[1].trim())) {
            problems.push(`${at} uses the font shorthand; write font-size / font-family instead`);
          }
        }
      }
      for (const m of line.matchAll(/fontFamily\s*:\s*(["'])([^"']*)\1/g)) {
        if (stray(m[2])) problems.push(`${at} names a font: ${m[0].trim()}`);
      }
      if (/--font-(?!sans\b|mono\b)[\w-]+/.test(line)) {
        problems.push(`${at} uses a font variable other than --font-sans / --font-mono`);
      }
      if (/\bfont-\[/.test(line))problems.push(`${at} uses an arbitrary Tailwind font`);
      if (/tabular-nums|font-variant-numeric/.test(line)) {
        problems.push(`${at} forces equal-width digits; Settings decides that`);
      }

      // weight
      if (!HANDS_OFF.test(path)) {
        for (const m of line.matchAll(/(?:font-weight\s*:\s*|fontWeight\s*:\s*["']?)(\d+)/g)) {
          if (!WEIGHTS.has(m[1])) problems.push(`${at} uses weight ${m[1]}; use 400 / 500 / 600 / 700`);
        }
      }

      // radius
      if (!HANDS_OFF.test(path) && !/shape:/.test(line)) {
        for (const m of line.matchAll(/border(?:-[a-z]+)*-radius\s*:\s*([^;}"`]*)/g)) {
          if (literalRadius(m[1])) problems.push(`${at} sets a literal radius; use var(--radius-*): ${m[0].trim()}`);
        }
        if (/(?<![\w-])rounded(?:-[trblse]{1,2})?-\[/.test(line)) problems.push(`${at} uses an arbitrary radius; use rounded-<tier>`);
        if (!path.endsWith(".css") && /["'`](?:[^"'`]*\s)?rounded(?=[\s"'`])/.test(line)) problems.push(`${at} uses bare rounded; name a tier (rounded-xs …)`);
        if (/(?<![\w-])rounded(?:-[trblse]{1,2})?-(?:3xl|4xl)\b/.test(line)) problems.push(`${at} uses a radius above the tiers`);
        for (const m of line.matchAll(/var\(--radius-([\w-]+)\)/g)) {
          if (!RADII.has(m[1]) && m[1] !== "full") problems.push(`${at} names --radius-${m[1]}, which is not a radius tier`);
        }
        if (/var\(--radius\)/.test(line)) problems.push(`${at} uses --radius; name a tier`);
        if (!MOTION_RADIUS.has(path) && !NUMERIC_RADIUS.test(path)) {
          for (const m of line.matchAll(/borderRadius\s*:\s*(\d+(?:\.\d+)?)\s*[,}]/g)) {
            if (m[1] !== "0") problems.push(`${at} sets a numeric radius; use "var(--radius-*)"`);
          }
        }
      }

      // ink
      if (/\bhsla?\(var\(--/.test(line)) problems.push(`${at} wraps a role in hsl(); every role is a full colour, write var(--role)`);
      if (/--text-(?:tertiary|dim|faint)\b/.test(line)) problems.push(`${at} uses a retired text alias; use --text-secondary / --text-muted`);
      if (/(?<![\w-])text-\[var\(--text-(?:bright|primary|secondary|muted)\)/.test(line)) problems.push(`${at} spells an ink as text-[var(--text-*)]; use text-text-*`);
      if (!path.startsWith("components/ui/") && !path.startsWith("app/styles/design/") && /(?<![\w-])text-(?:muted-)?foreground(?![\w-])/.test(line)) {
        problems.push(`${at} uses the shadcn ink name outside components/ui; use text-text-*`);
      }

      // colour
      if (!HANDS_OFF.test(path) && !COLOUR_DATA.has(path) && !COLOUR_OWNERS.test(path)
          && !/glyph:/.test(line) && !/mask(?:-image)?\s*:/.test(line)
          && !(path.startsWith("app/styles/") && /^\s*--[\w-]+\s*:/.test(line))) {
        const bare = line.replace(/#0000\b/g, "");
        if (COLOUR_LITERAL.test(bare)) problems.push(`${at} writes a colour literal; name a colour token`);
      }

      // icons
      if (path.endsWith(".tsx") && !HANDS_OFF.test(path)) {
        for (const m of line.matchAll(/\bsize=\{(\d+)\}/g)) {
          const n = Number(m[1]);
          if (n < 28 && !ICON_SIZES.has(n)) problems.push(`${at} uses icon size ${n}; use 12 / 14 / 16 / 20 / 24`);
        }
      }
    });
  }
}

// layers: plain-layout rules in CSS modules may only get fewer.
const LAYOUT_PROPS = new Set(("display flex flex-direction flex-wrap flex-flow flex-grow flex-shrink "
  + "flex-basis order align-items align-self justify-content justify-self place-items gap row-gap "
  + "column-gap padding padding-top padding-bottom padding-left padding-right padding-inline "
  + "padding-block margin margin-top margin-bottom margin-left margin-right margin-inline "
  + "margin-block width height min-width max-width min-height max-height overflow overflow-x "
  + "overflow-y position top left right bottom inset grid-template-columns grid-template-rows").split(" "));
const BASELINE_PATH = "scripts/ui/module-layout-baseline.json";
const layoutCounts = {};
for (const path of walk("components")) {
  if (!path.endsWith(".module.css")) continue;
  const css = readFileSync(join(root, path), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  let count = 0;
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].trim();
    const props = m[2].split(";").filter((d) => d.includes(":")).map((d) => d.split(":")[0].trim());
    if (/^\.[A-Za-z][\w-]*$/.test(selector) && props.length && props.every((p) => LAYOUT_PROPS.has(p))) count += 1;
  }
  if (count) layoutCounts[path] = count;
}
if (process.argv.includes("--write-layout-baseline")) {
  const sorted = Object.fromEntries(Object.entries(layoutCounts).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(join(root, BASELINE_PATH), `${JSON.stringify(sorted, null, 2)}\n`);
} else {
  const baseline = JSON.parse(readFileSync(join(root, BASELINE_PATH), "utf8"));
  for (const [path, count] of Object.entries(layoutCounts)) {
    const allowed = baseline[path] ?? 0;
    if (count > allowed) {
      problems.push(`${path} has ${count} layout-only module rules (was ${allowed}); write layout as Tailwind classes in the JSX`);
    }
  }
}

assert.deepEqual(problems, [], `Values must come from the design tiers:\n${problems.join("\n")}`);
console.log("design tokens: ok");
