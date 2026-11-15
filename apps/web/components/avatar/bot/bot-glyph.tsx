/** A character as a flat glyph in React: see ./glyph.ts. It takes the
 *  current text colour, like an icon. */
import { botGlyph } from "./glyph";

export function BotGlyph({ shape, size, className }: { shape: string; size: number; className?: string }) {
  const { path, viewBox } = botGlyph(shape);
  return (
    <svg width={size} height={size} viewBox={viewBox} className={className} aria-hidden="true" data-bot-glyph={shape}
      style={{ display: "block", flexShrink: 0 }}>
      <path d={path} fill="currentColor" />
    </svg>
  );
}
