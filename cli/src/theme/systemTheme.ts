import type { ThemeName } from './themes.js';

/**
 * Resolve the terminal's effective background to a concrete light/dark theme.
 *
 * Heuristic via $COLORFGBG (xterm convention: "fg;bg" or "fg;default;bg").
 * The bg field is the 0..15 ANSI palette index. 0..6 + 8 are dark, 7 + 9..15
 * are light. iTerm2, Terminal.app, GNOME Terminal, and most modern emulators
 * set this. When unset, default to `dark` so we don't flash white-on-white.
 *
 * Claude Code goes further with an OSC 11 round-trip to get the exact bg
 * color from terminals that don't export COLORFGBG. We skip that for now —
 * the env var covers the common case and stays synchronous.
 */
export function getSystemThemeName(): ThemeName {
  const colorFgBg = process.env.COLORFGBG;
  if (!colorFgBg) return 'dark';
  // Parse the trailing field as the bg index.
  const parts = colorFgBg.split(';');
  const bg = parts[parts.length - 1];
  if (!bg) return 'dark';
  // "default" → assume dark (most terminals default to dark).
  if (bg === 'default') return 'dark';
  const n = Number.parseInt(bg, 10);
  if (Number.isNaN(n)) return 'dark';
  // ANSI palette 7 (light gray) and 9..15 (bright variants) are bright.
  if (n === 7 || (n >= 9 && n <= 15)) return 'light';
  return 'dark';
}
