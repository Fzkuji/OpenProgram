/**
 * A character as a flat glyph: one filled path, its eyes cut through.
 *
 * Where a character stands in for an icon inside a coloured chip (the
 * graph's reply and sub-agent nodes, the Activity panel's agent rows), it
 * takes the chip's colour like any icon and is drawn flat: the body's path
 * in the current colour, the eyes holes showing the chip behind. No
 * renderer, no animation, so it can be cloned into SVG as cheaply as a
 * Solar glyph.
 *
 * The body outlines wind clockwise (holes such as a mug's handle wind the
 * other way), and under every eye the body's winding number is 1, so each
 * eye is a pill drawn counter-clockwise: the nonzero fill rule cuts it out.
 * A test checks both for every shape.
 *
 * The view box scales and centres the body the way botFit sizes a live
 * character, so the glyph fills its box like an icon.
 */
import { examples, type AvatarDefinition } from "open-bot-avatar";

import { DEFAULT_BOT_SHAPE, botFit, isBotShape, type BotShape } from "./catalog";
import { GALLERY_DEFINITIONS } from "./gallery";

/** The renderer's resting eye, in drawing units before the eyes' scale
 *  (open-bot-avatar's EYE_W and EYE_H). */
export const GLYPH_EYE = { width: 4, height: 11 } as const;

const definitions = new Map<string, AvatarDefinition>();
for (const definition of [...examples, ...GALLERY_DEFINITIONS]) definitions.set(definition.id, definition);

const n = (value: number) => Math.round(value * 100) / 100;

/** One eye: a pill of the resting size round (cx, cy), counter-clockwise. */
function eyePath(cx: number, cy: number, scale: number): string {
  const w = GLYPH_EYE.width * scale;
  const h = GLYPH_EYE.height * scale;
  const r = w / 2;
  const x = cx - r;
  const top = cy - h / 2 + r;
  const bottom = cy + h / 2 - r;
  return `M${n(x)} ${n(top)}V${n(bottom)}A${n(r)} ${n(r)} 0 0 0 ${n(x + w)} ${n(bottom)}V${n(top)}A${n(r)} ${n(r)} 0 0 0 ${n(x)} ${n(top)}Z`;
}

export interface BotGlyph {
  /** Body and eye holes in the 0 0 100 100 drawing box; fill rule nonzero. */
  path: string;
  /** The body alone and the eyes alone, for a glyph drawn in two colours. */
  body: string;
  eyes: string;
  /** The view box that fits the body like an icon in its box. */
  viewBox: string;
}

const glyphs = new Map<string, BotGlyph>();

export function botGlyph(shape: string): BotGlyph {
  const id: BotShape = isBotShape(shape) ? shape : DEFAULT_BOT_SHAPE;
  let glyph = glyphs.get(id);
  if (glyph) return glyph;
  const definition = definitions.get(id) ?? definitions.get(DEFAULT_BOT_SHAPE)!;
  const { x, y, spacing, scale } = definition.eyes;
  const eyes = eyePath(x - spacing / 2, y, scale) + eyePath(x + spacing / 2, y, scale);
  const path = definition.body.path + eyes;
  const fit = botFit(id);
  const side = 100 / fit.scale;
  glyph = { path, body: definition.body.path, eyes, viewBox: `${n(-fit.left * side)} ${n(-fit.top * side)} ${n(side)} ${n(side)}` };
  glyphs.set(id, glyph);
  return glyph;
}
