#!/usr/bin/env node
// Regenerates components/solar-icons/bodies.ts — the inline
// SVG bodies of every Solar icon the app uses, fetched from the Iconify
// API (set `solar`, Solar Icons by 480 Design, CC BY 4.0). The output is
// committed so builds stay offline; rerun this whenever ICONS changes:
//
//   node apps/web/scripts/icons/fetch-solar.mjs
//
// Every icon uses Solar's Bold Duotone style (a filled glyph plus a
// 50 % lighter secondary layer).
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ICONS = {
  // composer control row + plus menu
  "tuning-2": "bold-duotone",
  "paperclip": "bold-duotone",
  "case-minimalistic": "bold-duotone",
  "settings-minimalistic": "bold-duotone",
  "global": "bold-duotone",
  "box-minimalistic": "bold-duotone",
  "forward": "bold-duotone",
  "list-arrow-down": "bold-duotone",
  "eye": "bold-duotone",
  "eye-closed": "bold-duotone",
  // the composer's effort chip
  "fire": "bold-duotone",
  "plain-2": "bold-duotone",
  "record": "bold-duotone",
  // settings colour pickers: the custom-colour well
  "pipette": "bold-duotone",
  // composer voice input
  "microphone-2": "bold-duotone",
  // model / permission badges, question panel
  "chat-round-dots": "bold-duotone",
  "programming": "bold-duotone",
  "shield-check": "bold-duotone",
  // environment row chips + their menus
  "monitor": "bold-duotone",
  "earth": "bold-duotone",
  // the composer's Goal chip
  "target": "bold-duotone",
  "pip-2": "bold-duotone",
  "folder-with-files": "bold-duotone",
  // sidebar project rows: closed / open (folder-open above)
  "folder": "bold-duotone",
  "folder-open": "bold-duotone",
  "add-folder": "bold-duotone",
  "danger-triangle": "bold-duotone",
  // menu furniture
  "check-circle": "bold-duotone",
  "close-circle": "bold-duotone",
  "alt-arrow-right": "bold-duotone",
  "alt-arrow-down": "bold-duotone",
  "copy": "bold-duotone",
  // model capability glyphs (agent selector)
  "videocamera": "bold-duotone",
  "lightbulb-bolt": "bold-duotone",
  // per-turn file-change card
  "pen-new-square": "bold-duotone",
  // execution strip: file-writing steps
  "pen-2": "bold-duotone",
  "book-2": "bold-duotone",
  "file-text": "bold-duotone",
  // per-folder git pill + its menu
  "git-branch": "bold-duotone",
  "git-pull-request": "bold-duotone",
  "add-circle": "bold-duotone",
  "folder-path-connect": "bold-duotone",
  "square-arrow-right-up": "bold-duotone",
  // execution timeline row tiles (tool-presentation.ts + StepRow fallbacks);
  // programming, pen-new-square, file-text, folder-open, global, plain-2,
  // git-branch and lightbulb-bolt above are reused there too
  "code-square": "bold-duotone",
  "file-search": "bold-duotone",
  "magnifier": "bold-duotone",
  "structure": "bold-duotone",
  "cursor": "bold-duotone",
  "gallery": "bold-duotone",
  "gallery-add": "bold-duotone",
  // agents (sub-agent tools, Activity, the DAG) and your messages in the DAG
  "ghost": "bold-duotone",
  "user-rounded": "bold-duotone",
  "book": "bold-duotone",
  "chat-round-question-mark": "bold-duotone",
  "map": "bold-duotone",
  "calendar": "bold-duotone",
  "box": "bold-duotone",
  "stars": "bold-duotone",
  "database": "bold-duotone",
  "checklist": "bold-duotone",
  "refresh": "bold-duotone",
  "plug-circle": "bold-duotone",
  "sledgehammer": "bold-duotone",
  "cpu": "bold-duotone",
  // menu rows (components/ui/menu/menu-parts.tsx): every action menu's icons
  "pen": "bold-duotone",
  "pin": "bold-duotone",
  "link": "bold-duotone",
  "archive": "bold-duotone",
  "archive-up": "bold-duotone",
  "trash-bin-trash": "bold-duotone",
  "trash-bin-minimalistic": "bold-duotone",
  "file-download": "bold-duotone",
  "layers": "bold-duotone",
  "close-square": "bold-duotone",
  "add-square": "bold-duotone",
  "widget-add": "bold-duotone",
  "settings": "bold-duotone",
  "home-2": "bold-duotone",
  "arrow-right": "bold-duotone",
  "arrow-left": "bold-duotone",
  "crop": "bold-duotone",
  "magnifer-zoom-in": "bold-duotone",
  "magnifer-zoom-out": "bold-duotone",
  "restart": "bold-duotone",
  "printer": "bold-duotone",
  "bookmark": "bold-duotone",
  "bookmark-square": "bold-duotone",
  "history": "bold-duotone",
  "download-minimalistic": "bold-duotone",
  "import": "bold-duotone",
  "export": "bold-duotone",
  "clipboard-text": "bold-duotone",
  "scissors": "bold-duotone",
  "info-circle": "bold-duotone",
  "document-add": "bold-duotone",
  "document-text": "bold-duotone",
  "chat-round-line": "bold-duotone",
  "square-arrow-left": "bold-duotone",
  "square-arrow-right": "bold-duotone",
  "window-frame": "bold-duotone",
  "exit": "bold-duotone",
  "play": "bold-duotone",
  "pause": "bold-duotone",
  "question-circle": "bold-duotone",
  "sort-vertical": "bold-duotone",
  "sort-from-top-to-bottom": "bold-duotone",
  "sort-from-bottom-to-top": "bold-duotone",
  "clock-circle": "bold-duotone",
  "users-group-rounded": "bold-duotone",
  "list": "bold-duotone",
  "widget-2": "bold-duotone",
  "square-share-line": "bold-duotone",
  "star": "bold-duotone",
  "palette": "bold-duotone",
  // DAG canvas controls (dag-view.tsx) and the pane's perspective strip
  "git-graph": "bold-duotone",
  "menu-dots": "bold-duotone",
  "full-screen-square": "bold-duotone",
  "quit-full-screen-square": "bold-duotone",
  "gps": "bold-duotone",
  "notes": "bold-duotone",
};

// A few glyphs come from other Iconify sets where Solar has no good fit;
// they keep their own viewBox. Key → "prefix:name".
//   Font Awesome Free (fa6-solid) © Fonticons, CC BY 4.0 — the file card's
//   undo / review buttons, chosen for their solid weight at 13 px.
//   Solar's plain Bold cursor — one solid shape, the white keyline under
//   the agent pointer over controlled apps.
const EXTRA = {
  "fa-rotate-left": "fa6-solid:rotate-left",
  "fa-eye": "fa6-solid:eye",
  "cursor-solid": "solar:cursor-bold",
};

// Local tweaks to a fetched body, applied before it is written out.
const ADJUST = {
  // The microphone head (every path after the stand) is stretched 10 %
  // upward from its bottom edge at y = 16.5, at the same width, so the
  // capsule reads taller at 16 px; its top lands at y = 0.55. The glyph
  // then spans 0.55–22.5, so the whole of it moves down 0.475 to sit
  // centred on y = 12 (1.025–22.975).
  "microphone-2": (body) =>
    body.replace(/^<g fill="currentColor">(<path [^>]*\/>)(.*)<\/g>$/, (_, stand, head) =>
      `<g fill="currentColor" transform="translate(0 0.475)">${stand}` +
      `<g transform="matrix(1 0 0 1.1 0 -1.65)">${head}</g></g>`),
};

const ids = Object.entries(ICONS).map(([name, style]) => `${name}-${style}`);
// Batched: one URL with every id grows past what the API accepts.
const data = { icons: {} };
for (let at = 0; at < ids.length; at += 40) {
  const url = `https://api.iconify.design/solar.json?icons=${ids.slice(at, at + 40).join(",")}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Iconify API ${res.status} for ${url}`);
  const batch = await res.json();
  if (batch.not_found?.length) throw new Error(`Not in Solar: ${batch.not_found.join(", ")}`);
  Object.assign(data.icons, batch.icons);
}

const lines = Object.entries(ICONS).map(([name, style]) => {
  const fetched = data.icons[`${name}-${style}`]?.body;
  if (!fetched) throw new Error(`Missing body for ${name}-${style}`);
  const body = ADJUST[name] ? ADJUST[name](fetched) : fetched;
  if (body === fetched && ADJUST[name]) throw new Error(`Adjustment for ${name} no longer matches`);
  return `  /** solar:${name}-${style} */\n  "${name}": ${JSON.stringify(body)},`;
});

const viewBoxes = [];
for (const [key, id] of Object.entries(EXTRA)) {
  const [prefix, name] = id.split(":");
  const r = await fetch(`https://api.iconify.design/${prefix}.json?icons=${name}`);
  if (!r.ok) throw new Error(`Iconify API ${r.status} for ${id}`);
  const set = await r.json();
  const icon = set.icons?.[name];
  if (!icon) throw new Error(`Missing ${id}`);
  const w = icon.width ?? set.width ?? 24;
  const h = icon.height ?? set.height ?? 24;
  lines.push(`  /** ${id} */\n  "${key}": ${JSON.stringify(icon.body)},`);
  viewBoxes.push(`  "${key}": "0 0 ${w} ${h}",`);
}

const out = `// AUTO-GENERATED by \`apps/web/scripts/icons/fetch-solar.mjs\` — do not hand-edit.
// Solar Icons © 480 Design, CC BY 4.0 — https://github.com/480-Design/Solar-Icon-Set
// fa-* glyphs: Font Awesome Free © Fonticons, CC BY 4.0 — https://fontawesome.com/license/free
// Bodies are Iconify SVG contents (24×24 unless SOLAR_VIEWBOXES says
// otherwise); every fill is currentColor so the glyph takes the
// surrounding text colour.

export const SOLAR_BODIES = {
${lines.join("\n")}
} as const;

export type SolarIconName = keyof typeof SOLAR_BODIES;

/** viewBox for the non-Solar glyphs; Solar bodies are all 24×24. */
export const SOLAR_VIEWBOXES: Partial<Record<SolarIconName, string>> = {
${viewBoxes.join("\n")}
};
`;

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "../../components/solar-icons/bodies.ts");
writeFileSync(target, out);
console.log(`wrote ${lines.length} icons → ${target}`);
