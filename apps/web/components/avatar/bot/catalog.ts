/**
 * What a Bot character can look like: the shapes and the colours.
 *
 * Kept apart from the renderer (./runtime.ts) so the picker, the identity
 * rule and the tests can list both without loading it.
 */
import { fitInk } from "../../brand-mark/fit";

/** Shape ids. The first seven ship with the open-bot-avatar package, the
 *  rest are in ./gallery.ts. The order is the picker's order and the order
 *  the identity rule counts in, so new shapes go at the end. */
export const BOT_SHAPES = [
  "chat", "controller", "mug", "lightbulb", "sprout", "watering-can", "barbell",
  "book", "brain", "bull", "bunny", "camera", "cat", "cloud", "ghost",
  "magnifier", "paint-brush", "palette", "rocket", "shield", "star", "witch-hat",
] as const;
export type BotShape = (typeof BOT_SHAPES)[number];

/** Body colours. A character is a picture, like a generated avatar: its
 *  colours are its own and do not follow the theme, so the same Bot looks
 *  the same everywhere. The order is the picker's order and the order the
 *  identity rule counts in, so new colours go at the end. */
export const BOT_COLORS = {
  blue: "#1a7cff",
  orange: "#ff6a1a",
  green: "#1fbf68",
  violet: "#8b5cf6",
  pink: "#f0409c",
  teal: "#14b8a6",
  yellow: "#f6a117",
  red: "#f0384a",
} as const;
export type BotColor = keyof typeof BOT_COLORS;
export const BOT_COLOR_IDS = Object.keys(BOT_COLORS) as BotColor[];

/** The eyes are the same on every colour. */
export const BOT_EYE_COLOR = "#ffffff";

export const DEFAULT_BOT_SHAPE: BotShape = "chat";
export const DEFAULT_BOT_COLOR: BotColor = "blue";

export function isBotShape(value: unknown): value is BotShape {
  return typeof value === "string" && (BOT_SHAPES as readonly string[]).includes(value);
}
export function isBotColor(value: unknown): value is BotColor {
  return typeof value === "string" && Object.hasOwn(BOT_COLORS, value);
}

/** Where each body sits in its 0 0 100 100 drawing box: [left, top,
 *  right, bottom]. The drawings leave different margins (the cloud is
 *  wide and short, the ghost narrow and tall), so a character drawn at the
 *  box's size looks smaller than an icon of the same size. A test checks
 *  the table against the drawings. */
export const BOT_EXTENTS: Record<BotShape, readonly [number, number, number, number]> = {
  "chat": [10, 14, 90, 86.5],
  "controller": [8.5, 21.5, 91.5, 81],
  "mug": [14, 20, 87, 86],
  "lightbulb": [18, 8, 82, 88],
  "sprout": [17.5, 11, 83.5, 90],
  "watering-can": [8.5, 30.5, 91.5, 86],
  "barbell": [8, 17, 92, 83],
  "book": [8, 23.5, 92, 84],
  "brain": [12, 16.5, 89.5, 82],
  "bull": [9, 9, 91, 84],
  "bunny": [20, 11.5, 80, 86],
  "camera": [9, 20, 91, 80],
  "cat": [18, 11, 88, 86],
  "cloud": [9, 24, 91, 76],
  "ghost": [23, 12, 77, 88],
  "magnifier": [12, 12, 90, 90],
  "paint-brush": [21, 8.5, 83.5, 85.5],
  "palette": [11, 18, 90, 84],
  "rocket": [14, 9, 86, 92],
  "shield": [14, 14, 86, 87.5],
  "star": [13, 15, 87, 86],
  "witch-hat": [8, 10, 92, 87],
};

/** How much of its slot a character fills, measured like an icon's ink:
 *  the body's geometric mean side is BOT_OPTICAL_SIDE of the slot (a
 *  16px Solar glyph's ink is about 14 x 13.5px, a mean side of 0.86), and
 *  no side is longer than BOT_MAX_SIDE of it. */
export const BOT_OPTICAL_SIDE = 0.8;
export const BOT_MAX_SIDE = 0.94;

export interface BotFit {
  /** The drawing box's size as a multiple of the slot. */
  scale: number;
  /** Where the drawing box's top-left corner sits, as fractions of the slot. */
  left: number;
  top: number;
}

/** Scale and place a character's drawing box so its body is centred in its
 *  slot and fills it about as much as an icon fills the same slot. */
export function botFit(shape: string): BotFit {
  const [x0, y0, x1, y1] = BOT_EXTENTS[isBotShape(shape) ? shape : DEFAULT_BOT_SHAPE];
  const width = (x1 - x0) / 100;
  const height = (y1 - y0) / 100;
  const scale = Math.min(BOT_OPTICAL_SIDE / Math.sqrt(width * height), BOT_MAX_SIDE / Math.max(width, height));
  return {
    scale,
    left: 0.5 - (scale * (x0 + x1)) / 200,
    top: 0.5 - (scale * (y0 + y1)) / 200,
  };
}

/** Scale and place a character's drawing box by the composer's fill rule
 *  (fitInk in @/components/brand-mark/fit), with the body as the ink: its
 *  longer side is the slot's side, centred. Used only for the composer's
 *  Agent chip and the Agent menu; every other character uses botFit. */
export function botMarkFit(shape: string): BotFit {
  const [x0, y0, x1, y1] = BOT_EXTENTS[isBotShape(shape) ? shape : DEFAULT_BOT_SHAPE];
  const slot = 100;
  const fit = fitInk({ x: x0, y: y0, width: x1 - x0, height: y1 - y0, viewBox: [0, 0, 100, 100] }, slot);
  return { scale: fit.width / slot, left: fit.left / slot, top: fit.top / slot };
}
