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

export const DEFAULT_FONT: FontKey = "inter";

/** Cookie for equal-width digits (font-variant-numeric: tabular-nums on
 *  all text). On unless the cookie says "off"; the <head> script mirrors it
 *  as html[data-tabular-nums="off"] before first paint. */
export const TABULAR_NUMS_COOKIE = "agentic_tabular_nums";
export const CHAT_WIDTH_COOKIE = "agentic_chat_width";

// Latin face + CJK fallback per option. Every stack starts with a real,
// always-resolvable name (never an undefined `var()`), so it is a valid
// font-family value on its own — no dependency on any CSS variable being
// defined elsewhere.
// The Chinese, Japanese and Korean faces come before the system font:
// iOS WebKit draws a character -apple-system / system-ui lacks as a boxed
// "?" instead of falling through to the next family, so a CJK face named
// after them is never reached on an iPhone. Where these faces are absent
// (Windows, Linux) they are skipped and the stack is unchanged.
const CJK_FIRST = ["'PingFang SC'", "'Hiragino Sans'", "'Apple SD Gothic Neo'"];

export const FONT_STACKS: Record<FontKey, string> = {
  // OS-native stack (pure system look). No Inter — picks up SF on
  // macOS, Segoe on Windows, etc.
  system: [
    ...CJK_FIRST,
    "system-ui",
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
    "'Microsoft YaHei'",
    "'Hiragino Sans GB'",
    "sans-serif",
  ].join(", "),
  // Inter is bundled locally: its preloaded Latin face from next/font
  // (app/fonts.ts, defined on <html>) first, then the @fontsource faces
  // (app/globals.css). The var() carries its own fallback, so the stack
  // stays valid wherever the variable is not defined.
  inter: [
    "var(--font-inter-latin, 'Inter Variable')",
    "'Inter Variable'",
    "'Inter'",
    ...CJK_FIRST,
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
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
    // 本地打包（见 app/globals.css 的 @fontsource-variable import），
    // 不依赖系统装没装。后面的兜底保持原样。
    "var(--font-mono-latin, 'JetBrains Mono Variable')",
    "'JetBrains Mono Variable'",
    "'JetBrains Mono'",
    ...CJK_FIRST,
    "ui-monospace",
    "Menlo",
    "Monaco",
    "Consolas",
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
