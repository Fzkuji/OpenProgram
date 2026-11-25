/**
 * Marks drawn on an image: the screenshot tool's and the composer's image
 * annotator's one set of tools, after Snow Shot's (rectangle, ellipse,
 * arrow, pen, highlighter, mosaic, text, numbered step).
 *
 * Marks are kept in the image's own pixels, so the PNG that comes out has
 * the image's size whatever the zoom it was drawn at. Line widths and text
 * sizes are in CSS px and multiplied by `scale` (image pixels per CSS px)
 * when drawn, so a mark looks the same on a Retina screenshot and a plain
 * one.
 */

export type Point = { x: number; y: number };

export type Tool = "rect" | "ellipse" | "arrow" | "pen" | "highlighter" | "mosaic" | "text" | "number";

export const TOOLS: Tool[] = ["rect", "ellipse", "arrow", "pen", "highlighter", "mosaic", "text", "number"];

export type Mark =
  | { kind: "rect" | "ellipse" | "arrow" | "mosaic"; color: string; width: number; from: Point; to: Point }
  | { kind: "pen" | "highlighter"; color: string; width: number; points: Point[] }
  | { kind: "text"; color: string; width: number; at: Point; text: string }
  | { kind: "number"; color: string; width: number; at: Point; n: number };

export const COLORS = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#3b82f6", "#ffffff", "#111111"];

/** Stroke widths, CSS px: thin, medium, thick. */
export const WIDTHS = [2, 4, 8];

/** Text and step-number sizes per stroke width, CSS px. */
export function textSize(width: number): number {
  return width <= 2 ? 14 : width <= 4 ? 18 : 26;
}

export function numberRadius(width: number): number {
  return width <= 2 ? 10 : width <= 4 ? 13 : 18;
}

/** Mosaic cell size, CSS px. */
const MOSAIC_CELL = 10;

/** The next step number: one more than the largest so far. */
export function nextNumber(marks: Mark[]): number {
  return marks.reduce((n, mark) => (mark.kind === "number" ? Math.max(n, mark.n) : n), 0) + 1;
}

export function normalized(from: Point, to: Point) {
  return {
    x: Math.min(from.x, to.x),
    y: Math.min(from.y, to.y),
    width: Math.abs(to.x - from.x),
    height: Math.abs(to.y - from.y),
  };
}

/** A shape too small to mean anything (a click without a drag). */
export function isEmpty(mark: Mark): boolean {
  if ("points" in mark) return mark.points.length < 2;
  if (mark.kind === "text") return !mark.text.trim();
  if (mark.kind === "number") return false;
  const box = normalized(mark.from, mark.to);
  return mark.kind === "arrow" ? Math.hypot(box.width, box.height) < 4 : box.width < 3 || box.height < 3;
}

const pixelated = new WeakMap<object, { cell: number; canvas: HTMLCanvasElement }>();

/** The image shrunk by one mosaic cell per pixel, drawn back up without smoothing. */
function mosaicOf(source: HTMLCanvasElement | HTMLImageElement | ImageBitmap, cell: number) {
  const cached = pixelated.get(source);
  if (cached && cached.cell === cell) return cached.canvas;
  const width = "naturalWidth" in source ? source.naturalWidth : source.width;
  const height = "naturalHeight" in source ? source.naturalHeight : source.height;
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.ceil(width / cell));
  canvas.height = Math.max(1, Math.ceil(height / cell));
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  }
  pixelated.set(source, { cell, canvas });
  return canvas;
}

function arrowHead(ctx: CanvasRenderingContext2D, from: Point, to: Point, line: number) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const length = Math.min(Math.hypot(to.x - from.x, to.y - from.y) * 0.5, Math.max(12, line * 4));
  const spread = Math.PI / 7;
  // The shaft stops where the head starts, so a thick arrow keeps its point.
  const base = { x: to.x - Math.cos(angle) * length * 0.8, y: to.y - Math.sin(angle) * length * 0.8 };
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(base.x, base.y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - length * Math.cos(angle - spread), to.y - length * Math.sin(angle - spread));
  ctx.lineTo(to.x - length * Math.cos(angle + spread), to.y - length * Math.sin(angle + spread));
  ctx.closePath();
  ctx.fill();
}

function stroke(ctx: CanvasRenderingContext2D, points: Point[]) {
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.stroke();
}

/**
 * Draw `marks` onto `ctx`, whose origin is image pixel (0, 0) shifted by
 * `offset` (a crop). `source` is the image under the marks, which mosaic
 * reads; `scale` is image pixels per CSS px.
 */
export function drawMarks(
  ctx: CanvasRenderingContext2D,
  marks: Mark[],
  options: { scale: number; source: HTMLCanvasElement | HTMLImageElement | ImageBitmap; offset?: Point; font?: string },
) {
  const { scale, source } = options;
  const offset = options.offset ?? { x: 0, y: 0 };
  const font = options.font ?? "Inter, -apple-system, 'PingFang SC', 'Segoe UI', sans-serif";
  ctx.save();
  ctx.translate(-offset.x, -offset.y);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const mark of marks) {
    const line = mark.width * scale;
    ctx.save();
    ctx.strokeStyle = mark.color;
    ctx.fillStyle = mark.color;
    ctx.lineWidth = line;
    if (mark.kind === "rect") {
      const box = normalized(mark.from, mark.to);
      ctx.beginPath();
      ctx.roundRect(box.x, box.y, box.width, box.height, Math.min(3 * scale, box.width / 2, box.height / 2));
      ctx.stroke();
    } else if (mark.kind === "ellipse") {
      const box = normalized(mark.from, mark.to);
      ctx.beginPath();
      ctx.ellipse(box.x + box.width / 2, box.y + box.height / 2, box.width / 2, box.height / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (mark.kind === "arrow") {
      arrowHead(ctx, mark.from, mark.to, line);
    } else if (mark.kind === "pen") {
      stroke(ctx, mark.points);
    } else if (mark.kind === "highlighter") {
      ctx.globalAlpha = 0.4;
      ctx.lineCap = "butt";
      ctx.lineWidth = Math.max(line * 3, 12 * scale);
      stroke(ctx, mark.points);
    } else if (mark.kind === "mosaic") {
      const box = normalized(mark.from, mark.to);
      ctx.beginPath();
      ctx.rect(box.x, box.y, box.width, box.height);
      ctx.clip();
      ctx.imageSmoothingEnabled = false;
      const width = "naturalWidth" in source ? source.naturalWidth : source.width;
      const height = "naturalHeight" in source ? source.naturalHeight : source.height;
      ctx.drawImage(mosaicOf(source, Math.max(4, Math.round(MOSAIC_CELL * scale))), 0, 0, width, height);
    } else if (mark.kind === "text") {
      const size = textSize(mark.width) * scale;
      ctx.font = `600 ${size}px ${font}`;
      ctx.textBaseline = "top";
      // A thin outline in the opposite tone keeps the words readable on any background.
      ctx.lineWidth = Math.max(2, size / 8);
      ctx.strokeStyle = mark.color === "#ffffff" || mark.color === "#facc15" ? "rgba(0,0,0,0.55)" : "rgba(255,255,255,0.85)";
      mark.text.split("\n").forEach((row, i) => {
        ctx.strokeText(row, mark.at.x, mark.at.y + i * size * 1.25);
        ctx.fillText(row, mark.at.x, mark.at.y + i * size * 1.25);
      });
    } else if (mark.kind === "number") {
      const radius = numberRadius(mark.width) * scale;
      ctx.beginPath();
      ctx.arc(mark.at.x, mark.at.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = mark.color === "#ffffff" || mark.color === "#facc15" ? "#111111" : "#ffffff";
      ctx.font = `700 ${radius * 1.15}px ${font}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(mark.n), mark.at.x, mark.at.y + radius * 0.05);
    }
    ctx.restore();
  }
  ctx.restore();
}

/** One undoable history: marks now, and what undo and redo lead to. */
export interface History {
  past: Mark[][];
  present: Mark[];
  future: Mark[][];
}

export const emptyHistory: History = { past: [], present: [], future: [] };

export function commit(history: History, present: Mark[]): History {
  return { past: [...history.past, history.present], present, future: [] };
}

export function undo(history: History): History {
  if (!history.past.length) return history;
  return {
    past: history.past.slice(0, -1),
    present: history.past[history.past.length - 1],
    future: [history.present, ...history.future],
  };
}

export function redo(history: History): History {
  if (!history.future.length) return history;
  return { past: [...history.past, history.present], present: history.future[0], future: history.future.slice(1) };
}
