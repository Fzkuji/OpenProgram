/**
 * Semantic theme tokens used across the Ink TUI.
 *
 * Same shape as the original `colors` const so component code keeps working
 * by simply replacing `import { colors }` with `const colors = useColors()`.
 *
 * Design notes:
 *   - `assistant.bg` and `system.bg` stay `undefined` so the terminal's own
 *     bg shines through. Only the user-message block paints a bg, mirroring
 *     Claude Code's chat layout.
 *   - Light variants use dark `text` so foreground stays readable when the
 *     terminal background is a bright color (the common daytime case).
 *   - The `*-dim` variants drop saturation; helpful in low-contrast terminals
 *     or when the high-saturation orange feels loud.
 */

import type { Color } from '../runtime/index';

export interface ColorTheme {
  // Common roles
  primary: Color;
  secondary: Color;
  success: Color;
  warning: Color;
  error: Color;
  muted: Color;
  accent: Color;
  text: Color;
  border: Color;
  welcomeTitle: Color;

  // Chat-turn roles
  user: { bg: Color | undefined; fg: Color; glyph: Color };
  assistant: { bg: Color | undefined; fg: Color; glyph: Color };
  system: { bg: Color | undefined; fg: Color; glyph: Color };

  // Tool-call rendering
  tool: { running: Color; done: Color; error: Color };
}

export const THEME_NAMES = ['dark', 'dark-dim', 'light', 'light-dim'] as const;
/** A renderable palette. Always resolvable. */
export type ThemeName = (typeof THEME_NAMES)[number];

/**
 * What the user can save as a setting. `auto` is resolved at runtime to
 * one of the concrete ThemeName values via `getSystemThemeName()`.
 */
export const THEME_SETTINGS = ['auto', ...THEME_NAMES] as const;
export type ThemeSetting = (typeof THEME_SETTINGS)[number];

export const THEME_LABELS: Record<ThemeSetting, string> = {
  auto: 'Auto — match the terminal background',
  dark: 'Dark — high-contrast text with orange frame',
  'dark-dim': 'Dark dim — lower saturation for dark terminals',
  light: 'Light — crisp text with restrained orange',
  'light-dim': 'Light dim — softer contrast for bright terminals',
};

const dark: ColorTheme = {
  primary: '#f97316',
  secondary: '#94a3b8',
  success: '#4ade80',
  warning: '#fbbf24',
  error: '#fb7185',
  muted: '#a1a1aa',
  accent: '#38bdf8',
  text: '#e5e7eb',
  border: '#52525b',
  welcomeTitle: 'ansi:cyanBright',
  user: { bg: '#2a211a', fg: '#f7efe7', glyph: '#f97316' },
  assistant: { bg: undefined, fg: '#e5e7eb', glyph: '#4ade80' },
  system: { bg: undefined, fg: '#a1a1aa', glyph: '#a1a1aa' },
  tool: { running: '#fbbf24', done: '#a1a1aa', error: '#fb7185' },
};

const darkDim: ColorTheme = {
  primary: '#d08342',
  secondary: '#8b949e',
  success: '#86a886',
  warning: '#c89642',
  error: '#c76b6b',
  muted: '#8a8f98',
  accent: '#4aa3b8',
  text: '#d7dce2',
  border: '#3f4650',
  welcomeTitle: 'ansi:cyanBright',
  user: { bg: '#221d19', fg: '#ded6cf', glyph: '#d08342' },
  assistant: { bg: undefined, fg: '#d7dce2', glyph: '#86a886' },
  system: { bg: undefined, fg: '#8a8f98', glyph: '#8a8f98' },
  tool: { running: '#c89642', done: '#8a8f98', error: '#c76b6b' },
};

const light: ColorTheme = {
  // Black text on whatever the terminal's bg is (likely white-ish).
  // Accents pick up the warm orange so the brand still reads, but at a
  // saturation that contrasts cleanly with a bright background.
  primary: '#c44a17',
  secondary: '#5a5a5a',
  success: '#2c7a36',
  warning: '#a46500',
  error: '#a8261b',
  muted: '#5e5e5e',
  accent: '#a13b13',
  text: '#1a1a1a',
  border: '#bdbdbd',
  welcomeTitle: 'ansi:black',
  // Light cream tint for the user block so it reads as a quoted region
  // without going darker than the page itself.
  user: { bg: '#f4e4d4', fg: '#3a1f12', glyph: '#c44a17' },
  assistant: { bg: undefined, fg: '#1a1a1a', glyph: '#2c7a36' },
  system: { bg: undefined, fg: '#5e5e5e', glyph: '#5e5e5e' },
  tool: { running: '#a46500', done: '#5e5e5e', error: '#a8261b' },
};

const lightDim: ColorTheme = {
  primary: '#9b4a2a',
  secondary: '#6e6e6e',
  success: '#436e48',
  warning: '#8a6420',
  error: '#8b3a32',
  muted: '#6e6e6e',
  accent: '#8a4a2a',
  text: '#262626',
  border: '#c8c8c8',
  welcomeTitle: 'ansi:black',
  user: { bg: '#ecdfd1', fg: '#3a2418', glyph: '#9b4a2a' },
  assistant: { bg: undefined, fg: '#262626', glyph: '#436e48' },
  system: { bg: undefined, fg: '#6e6e6e', glyph: '#6e6e6e' },
  tool: { running: '#8a6420', done: '#6e6e6e', error: '#8b3a32' },
};

export const THEMES: Record<ThemeName, ColorTheme> = {
  dark,
  'dark-dim': darkDim,
  light,
  'light-dim': lightDim,
};

export const DEFAULT_THEME: ThemeName = 'dark';
/**
 * First-launch default. `auto` means we ask the terminal what its bg is
 * via OSC 11 and resolve to a concrete dark/light or dim variant. Falls
 * back to dark if the terminal doesn't reply.
 */
export const DEFAULT_SETTING: ThemeSetting = 'auto';

export function getTheme(name: ThemeName): ColorTheme {
  return THEMES[name] ?? THEMES[DEFAULT_THEME];
}

export function isThemeName(s: string): s is ThemeName {
  return (THEME_NAMES as readonly string[]).includes(s);
}

export function isThemeSetting(s: string): s is ThemeSetting {
  return (THEME_SETTINGS as readonly string[]).includes(s);
}
