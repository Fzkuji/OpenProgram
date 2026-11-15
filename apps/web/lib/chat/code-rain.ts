/**
 * What the code rain is drawn with, shared by the empty-chat backdrop
 * (components/chat/welcome/welcome-backdrop.tsx) and the rain that brings in new
 * messages (components/chat/welcome/code-rain-arrivals.tsx).
 */

/** Katakana, Latin capitals, digits and symbols; always drawn mirrored. */
export const CODE_RAIN_GLYPHS =
  "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ" +
  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.=*+-<>|\"_";

export const CODE_RAIN_FONT = `ui-monospace, "SF Mono", Menlo, monospace`;

export function randomGlyph(): string {
  return CODE_RAIN_GLYPHS[Math.floor(Math.random() * CODE_RAIN_GLYPHS.length)];
}

export type CodeRainPalette = { dark: boolean; tail: string; head: string };

/** The resolved light/dark mode the theme bootstrap writes on <html>.
 *  Reading the ink colour instead broke on oklch() tokens (Vivid), which
 *  drew the dark-mode rain on a light page. Always green. */
export function codeRainPalette(): CodeRainPalette {
  const dark = document.documentElement.dataset.themeMode === "dark";
  return dark
    ? { dark, tail: "#22e35b", head: "#eafff0" }
    : { dark, tail: "#118a3a", head: "#063d18" };
}

/** Glyphs are always mirrored. Flip the context once for a whole pass
 *  (inside save/restore) and draw each glyph with ``fillMirrored``, rather
 *  than saving and restoring the context around every glyph. */
export function mirror(ctx: CanvasRenderingContext2D): void {
  ctx.scale(-1, 1);
}

/** Draws one glyph centred on (x, y) in a context ``mirror`` flipped. */
export function fillMirrored(ctx: CanvasRenderingContext2D, ch: string, x: number, y: number): void {
  ctx.fillText(ch, -x, y);
}
