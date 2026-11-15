"use client";

/**
 * The one icon of an application: the Launchpad tile art. The Launchpad
 * (52px), the Applications page (44px) and the Abilities detail header
 * (48px) all draw it from here, so an application looks the same
 * everywhere. A glyph name (`icon` in application.json, `ICON` in a
 * built-in application) picks a drawing of its own or the generic tile
 * in a tone; without one the tone is hashed from the application's id.
 * The tile gradients are fixed brand art, the same in every theme.
 */

import { LaunchpadIcon, TONES, type IconArt, type Tone } from "@/components/center-tabs/pages/launchpad/launchpad-icons";

/** Glyphs with a Launchpad drawing of their own. */
export const GLYPH_ART: Partial<Record<string, IconArt>> = {
  calculator: "calculator", "book-open": "reader", "file-search": "analysis", "file-pdf": "pdf", telescope: "research",
  browser: "browser", office: "office", recording: "recording",
};
/** Tile colours of the generic drawing for the other glyphs; unknown ones take one by name. */
export const GLYPH_TONES: Partial<Record<string, Tone>> = {
  "book-open": "orange", calculator: "indigo", "file-search": "teal", "file-text": "blue", "file-pdf": "red", telescope: "indigo",
  "notebook-pen": "orange", table: "green", "chart-column": "teal", database: "indigo",
  image: "pink", music: "pink", video: "red", map: "green", calendar: "red", globe: "blue",
  code: "graphite", terminal: "graphite", mail: "blue", "message-square": "green",
  "list-checks": "green", languages: "violet", "pen-tool": "orange", palette: "violet",
  sigma: "indigo", meeting: "teal", "flask-conical": "teal", briefcase: "orange", bot: "violet", sparkles: "violet",
};
const TONE_NAMES = Object.keys(TONES) as Tone[];

/** The drawing a glyph name stands for; `generic` when it has none. */
export function artFor(icon: string | undefined): IconArt {
  return (icon && GLYPH_ART[icon]) || "generic";
}

/** The generic tile's tone: the glyph's own, else one hashed from `id`. */
export function toneFor(icon: string | undefined, id: string): Tone {
  const named = icon && GLYPH_TONES[icon];
  if (named) return named;
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TONE_NAMES[hash % TONE_NAMES.length];
}

export function ApplicationIcon({ icon, id, size }: { icon?: string; id: string; size: number }) {
  return <LaunchpadIcon art={artFor(icon)} tone={toneFor(icon, id)} size={size} />;
}
