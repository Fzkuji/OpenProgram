import type { ThemeName } from './themes.js';

/**
 * Module-level cache of the terminal's effective light/dark setting, plus
 * a subscriber list so the React tree can re-resolve `auto` when an async
 * OSC 11 query updates the cache after first paint.
 *
 * Detection priority:
 *   1. OSC 11 reply (most accurate; parsed by oscQuery.ts and pushed via
 *      setCachedSystemTheme)
 *   2. $COLORFGBG (synchronous initial guess)
 *   3. fallback to 'dark'
 *
 * Most modern terminals answer OSC 11 in <50ms, so we kick the query off
 * at startup and live with `dark` for that brief window.
 */

let cached: ThemeName = detectFromColorFgBg() ?? 'dark';
const subscribers = new Set<(name: ThemeName) => void>();

export function getSystemThemeName(): ThemeName {
  return cached;
}

export function setCachedSystemTheme(name: ThemeName): void {
  if (cached === name) return;
  cached = name;
  for (const cb of subscribers) cb(name);
}

export function subscribeSystemTheme(cb: (name: ThemeName) => void): () => void {
  subscribers.add(cb);
  return () => { subscribers.delete(cb); };
}

function detectFromColorFgBg(): ThemeName | undefined {
  const colorFgBg = process.env.COLORFGBG;
  if (!colorFgBg) return undefined;
  const parts = colorFgBg.split(';');
  const bg = parts[parts.length - 1];
  if (!bg || bg === 'default') return undefined;
  const n = Number.parseInt(bg, 10);
  if (Number.isNaN(n) || n < 0 || n > 15) return undefined;
  // ANSI palette 0–6 + 8 are dark; 7 + 9–15 are light.
  return n <= 6 || n === 8 ? 'dark' : 'light';
}
