/**
 * Font stacks + the cookie key, in a framework-neutral module with NO
 * "use client" and NO React. This lets the SSR root layout (a server
 * component) import the exact same stacks the client uses, so the very
 * first painted frame already carries the user's chosen font — read
 * from the cookie server-side — instead of flashing a default and then
 * switching once client JS runs.
 *
 * Cookie (not localStorage) on purpose: the server can read a cookie
 * during SSR and inline the correct `--font-sans` into `<html>`.
 * localStorage is invisible to the server, which is exactly why the old
 * approach had to guess a default and let a client script catch up.
 */

export type FontKey = "system" | "inter" | "serif" | "mono";

/** Cookie name holding the FontKey. Mirrored by the client setter. */
export const FONT_COOKIE = "agentic_font";

/** Legacy localStorage key — still written for backward compat and
 *  cross-tab sync; the cookie is the SSR source of truth. */
export const FONT_STORAGE_KEY = "agentic_font";

export const DEFAULT_FONT: FontKey = "inter";

// Latin face + CJK fallback per option. Every stack starts with a real,
// always-resolvable name (never an undefined `var()`), so it is a valid
// font-family value on its own — no dependency on any CSS variable being
// defined elsewhere.
export const FONT_STACKS: Record<FontKey, string> = {
  // OS-native stack (pure system look). No Inter — picks up SF on
  // macOS, Segoe on Windows, etc.
  system: [
    "system-ui",
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "'Hiragino Sans GB'",
    "sans-serif",
  ].join(", "),
  // Inter Variable is bundled locally (see app/globals.css).
  inter: [
    "'Inter Variable'",
    "'Inter'",
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "'Hiragino Sans GB'",
    "sans-serif",
  ].join(", "),
  serif: [
    "'Source Serif Pro'",
    "'Iowan Old Style'",
    "Georgia",
    "'Times New Roman'",
    "'Songti SC'",
    "SimSun",
    "'Noto Serif CJK SC'",
    "serif",
  ].join(", "),
  mono: [
    "'JetBrains Mono'",
    "ui-monospace",
    "Menlo",
    "Monaco",
    "Consolas",
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "monospace",
  ].join(", "),
};

/** Normalise an arbitrary cookie value to a valid FontKey. */
export function coerceFontKey(v: string | undefined | null): FontKey {
  return v != null && v in FONT_STACKS ? (v as FontKey) : DEFAULT_FONT;
}

export function fontStack(key: FontKey): string {
  return FONT_STACKS[key];
}
