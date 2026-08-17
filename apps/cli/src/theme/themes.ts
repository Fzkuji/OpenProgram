import type { Color } from '../runtime/index';

export type ThemeColor = Color | undefined;

export interface ColorTheme {
  // Common roles
  primary: Color;
  secondary: ThemeColor;
  success: Color;
  warning: Color;
  error: Color;
  muted: ThemeColor;
  accent: Color;
  text: ThemeColor;
  border: ThemeColor;

  // App surface
  welcome: {
    appTitle: Color;
    sectionCount: Color;
    sectionOverflow: Color;
    sectionTitle: ThemeColor;
  };
  bottomBar: {
    effortXhigh: Color;
  };
  channelQr: {
    hint: ThemeColor;
    status: Color;
  };

  // Chat-turn roles
  user: { bg: ThemeColor; fg: ThemeColor; glyph: Color };
  assistant: { bg: ThemeColor; fg: ThemeColor; glyph: Color };
  system: { bg: ThemeColor; fg: ThemeColor; glyph: Color };

  // Tool-call rendering
  tool: { running: Color; done: ThemeColor; error: Color };
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
  auto: 'Auto — use terminal-native foreground',
  dark: 'Dark — terminal foreground with OpenProgram accent',
  'dark-dim': 'Dark dim — terminal foreground with muted accent',
  light: 'Light — terminal foreground with OpenProgram accent',
  'light-dim': 'Light dim — terminal foreground with muted accent',
};

const CLAUDE_ORANGE = '#d97757';
const CLAUDE_CLAY = '#c6613f';
const EFFORT_XHIGH = '#991b1b';
const TERMINAL_TEXT: ThemeColor = undefined;
const TERMINAL_MUTED: Color = 'ansi:blackBright';

const base: ColorTheme = {
  primary: CLAUDE_ORANGE,
  secondary: TERMINAL_MUTED,
  success: 'ansi:green',
  warning: 'ansi:yellow',
  error: 'ansi:red',
  muted: TERMINAL_MUTED,
  accent: CLAUDE_CLAY,
  text: TERMINAL_TEXT,
  border: TERMINAL_MUTED,
  welcome: {
    appTitle: CLAUDE_ORANGE,
    sectionCount: CLAUDE_ORANGE,
    sectionOverflow: CLAUDE_CLAY,
    sectionTitle: TERMINAL_TEXT,
  },
  bottomBar: {
    effortXhigh: EFFORT_XHIGH,
  },
  channelQr: {
    hint: TERMINAL_MUTED,
    status: CLAUDE_ORANGE,
  },
  user: { bg: undefined, fg: TERMINAL_TEXT, glyph: CLAUDE_ORANGE },
  assistant: { bg: undefined, fg: TERMINAL_TEXT, glyph: 'ansi:green' },
  system: { bg: undefined, fg: TERMINAL_MUTED, glyph: TERMINAL_MUTED },
  tool: { running: 'ansi:yellow', done: TERMINAL_MUTED, error: 'ansi:red' },
};

const dark: ColorTheme = {
  ...base,
};

const darkDim: ColorTheme = {
  ...base,
  primary: CLAUDE_CLAY,
  accent: CLAUDE_CLAY,
  welcome: {
    appTitle: CLAUDE_ORANGE,
    sectionCount: CLAUDE_CLAY,
    sectionOverflow: CLAUDE_ORANGE,
    sectionTitle: TERMINAL_TEXT,
  },
  channelQr: {
    hint: TERMINAL_MUTED,
    status: CLAUDE_CLAY,
  },
  user: { ...base.user, glyph: CLAUDE_CLAY },
};

const light: ColorTheme = {
  ...base,
};

const lightDim: ColorTheme = {
  ...darkDim,
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
