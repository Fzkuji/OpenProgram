export const THEME_IDS = [
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "aurora-light",
  "custom",
  "custom-light",
] as const;
export type ThemeId = (typeof THEME_IDS)[number];

/** Named theme packages shown in Settings. Custom is no longer a package. */
export const THEME_STYLES = ["beige", "neutral", "aurora"] as const;
export type ThemeStyle = (typeof THEME_STYLES)[number];

/** Stored style values that still need a one-time migrate off Custom. */
export const LEGACY_THEME_STYLES = ["beige", "neutral", "aurora", "custom"] as const;

export const THEME_MODES = ["auto", "dark", "light"] as const;
export type ThemeMode = (typeof THEME_MODES)[number];
export type ResolvedThemeMode = Exclude<ThemeMode, "auto">;

export const DEFAULT_THEME_STYLE: ThemeStyle = "beige";
export const DEFAULT_THEME_MODE: ThemeMode = "auto";

export const THEME_STYLE_PAIRS: Record<
  ThemeStyle,
  Record<ResolvedThemeMode, ThemeId>
> = {
  beige: { dark: "beige-dark", light: "beige-light" },
  neutral: { dark: "dark", light: "light" },
  aurora: { dark: "aurora", light: "aurora-light" },
};

/** Package default --accent-orange, copied from apps/web/app/styles/themes/*.css. */
export const THEME_DEFAULT_ACCENTS: Record<ThemeId, string> = {
  "beige-dark": "#d97757",
  "beige-light": "#c15f3c",
  dark: "#6ea8fe",
  light: "#2563eb",
  aurora: "#4fd6c0",
  "aurora-light": "#0f766e",
  custom: "#d97757",
  "custom-light": "#c15f3c",
};

/** Preset chips in Appearance: one swatch from each package's light and dark accent. */
export const ACCENT_PRESETS = [
  THEME_DEFAULT_ACCENTS["beige-dark"],
  THEME_DEFAULT_ACCENTS["beige-light"],
  THEME_DEFAULT_ACCENTS.dark,
  THEME_DEFAULT_ACCENTS.light,
  THEME_DEFAULT_ACCENTS.aurora,
  THEME_DEFAULT_ACCENTS["aurora-light"],
] as const;

export const LEGACY_THEME_PREFERENCES = {
  auto: { style: "beige", mode: "auto" },
  "beige-dark": { style: "beige", mode: "dark" },
  "beige-light": { style: "beige", mode: "light" },
  dark: { style: "neutral", mode: "dark" },
  light: { style: "neutral", mode: "light" },
  aurora: { style: "aurora", mode: "dark" },
  "aurora-light": { style: "aurora", mode: "light" },
  custom: { style: "beige", mode: "dark" },
  "custom-light": { style: "beige", mode: "light" },
} as const satisfies Record<string, { style: ThemeStyle; mode: ThemeMode }>;

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return typeof value === "string"
    && (THEME_IDS as readonly string[]).includes(value);
}

export function isCustomStyleOrId(
  style?: string | null,
  resolvedId?: string | null,
): boolean {
  return style === "custom"
    || resolvedId === "custom"
    || resolvedId === "custom-light";
}

export function coerceThemeStyle(value: string | null | undefined): ThemeStyle {
  if (value === "custom") return DEFAULT_THEME_STYLE;
  return typeof value === "string"
    && (THEME_STYLES as readonly string[]).includes(value)
    ? value as ThemeStyle
    : DEFAULT_THEME_STYLE;
}

export function coerceThemeMode(value: string | null | undefined): ThemeMode {
  return typeof value === "string"
    && (THEME_MODES as readonly string[]).includes(value)
    ? value as ThemeMode
    : DEFAULT_THEME_MODE;
}

export function normalizeAccentHex(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  const match = trimmed.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  if (match[1].length === 3) {
    return `#${[...match[1]].map((char) => char + char).join("").toLowerCase()}`;
  }
  return `#${match[1].toLowerCase()}`;
}

/** Empty / invalid = use the current package accent. */
export function coerceAccentColor(value: string | null | undefined): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  return normalizeAccentHex(value);
}

export function deriveAccentHover(hex: string): string {
  const normalized = normalizeAccentHex(hex);
  if (!normalized) return hex;
  const r = Number.parseInt(normalized.slice(1, 3), 16);
  const g = Number.parseInt(normalized.slice(3, 5), 16);
  const b = Number.parseInt(normalized.slice(5, 7), 16);
  const to = (n: number) => Math.max(0, Math.min(255, Math.round(n * 0.86)))
    .toString(16)
    .padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

export function accentOverrideTokens(hex: string): {
  "--accent-orange": string;
  "--accent-fill": string;
  "--accent-orange-hover": string;
} {
  const accent = normalizeAccentHex(hex) ?? hex;
  return {
    "--accent-orange": accent,
    "--accent-fill": accent,
    "--accent-orange-hover": deriveAccentHover(accent),
  };
}

export function resolveThemePreference(
  style: string | null | undefined,
  mode: ThemeMode,
  systemDark: boolean,
): { theme: ThemeId; resolvedMode: ResolvedThemeMode } {
  const resolvedMode = mode === "auto" ? (systemDark ? "dark" : "light") : mode;
  const resolvedStyle = coerceThemeStyle(style);
  return { theme: THEME_STYLE_PAIRS[resolvedStyle][resolvedMode], resolvedMode };
}

export function legacyThemePreference(
  value: string | null | undefined,
  schemaVersion = "2",
): { style: ThemeStyle; mode: ThemeMode } {
  if (schemaVersion !== "2" && (value === "dark" || value === "light")) {
    return { style: "beige", mode: value };
  }
  const preference = value && value in LEGACY_THEME_PREFERENCES
    ? LEGACY_THEME_PREFERENCES[value as keyof typeof LEGACY_THEME_PREFERENCES]
    : LEGACY_THEME_PREFERENCES.auto;
  return { ...preference };
}

export type AppearancePreferenceBag = {
  style?: string | null;
  mode?: string | null;
  legacy?: string | null;
  schema?: string | null;
  customCss?: string | null;
  customCssEnabled?: string | null;
  accent?: string | null;
};

export type MigratedAppearancePreferences = {
  style: ThemeStyle;
  mode: ThemeMode;
  customCssEnabled: boolean;
  accent: string | null;
  migratedFromCustom: boolean;
};

/**
 * Schema 3 stays mode × style. Stored `custom` / `custom-light` become
 * beige, and any saved CSS turns the overlay on.
 */
export function migrateAppearancePreferences(
  input: AppearancePreferenceBag,
): MigratedAppearancePreferences {
  const schema = input.schema ?? "1";
  const storedStyleIsCustom = input.style === "custom";
  const migratedFromCustom = isCustomStyleOrId(input.style, input.legacy);
  let style = input.style;
  let mode = input.mode;
  const styleKnown = typeof style === "string"
    && (THEME_STYLES as readonly string[]).includes(style);
  const modeKnown = typeof mode === "string"
    && (THEME_MODES as readonly string[]).includes(mode);
  if (!styleKnown || !modeKnown) {
    const legacy = legacyThemePreference(input.legacy, schema);
    if (!styleKnown) style = migratedFromCustom ? DEFAULT_THEME_STYLE : legacy.style;
    if (!modeKnown) mode = legacy.mode;
  }
  if (style === "custom") style = DEFAULT_THEME_STYLE;
  const normalizedStyle = coerceThemeStyle(style);
  const normalizedMode = coerceThemeMode(mode);

  const hasCss = Boolean(input.customCss?.trim());
  const enabledFlag = input.customCssEnabled;
  let customCssEnabled: boolean;
  if (enabledFlag === "1" || enabledFlag === "true") {
    customCssEnabled = true;
  } else if (enabledFlag === "0" || enabledFlag === "false") {
    customCssEnabled = false;
  } else {
    customCssEnabled = hasCss;
  }
  if (storedStyleIsCustom && hasCss) customCssEnabled = true;

  return {
    style: normalizedStyle,
    mode: normalizedMode,
    customCssEnabled,
    accent: coerceAccentColor(input.accent),
    migratedFromCustom,
  };
}

/** Rewrite obsolete Custom-as-theme selectors onto the overlay attribute. */
export function rewriteCustomCssForOverlay(css: string): string {
  return css
    .replace(
      /\[data-theme\s*=\s*(["']?)custom-light\1\]/g,
      '[data-custom-css="on"][data-theme-mode="light"]',
    )
    .replace(
      /\[data-theme\s*=\s*(["']?)custom\1\]/g,
      '[data-custom-css="on"][data-theme-mode="dark"]',
    );
}

/**
 * Starter snippet for the CSS overlay. Targets `html` / current package
 * ids, not a fake Custom theme. Users may still target any data-theme.
 */
export const CUSTOM_CSS_TEMPLATE = `/* Overlay on the current theme package (Beige / Neutral / Aurora).
   Enable custom CSS to apply. You can still target any data-theme. */

html {
  /* --accent-orange: #6ea8fe; */
  /* --accent-fill: #6ea8fe; */
  /* --accent-orange-hover: #2563eb; */
}

html[data-theme="beige-dark"] {
  color-scheme: dark;

  --bg-primary: #1e1e20;
  --bg-secondary: #171719;
  --bg-tertiary: #252529;
  --bg-input: #2a2a2e;
  --bg-hover: rgba(255, 255, 255, 0.06);
  --bg-selected: rgba(255, 255, 255, 0.10);

  --text-bright: #ededf0;
  --text-primary: #b6b6bb;
  --text-secondary: #92929a;
  --text-muted: #74747c;

  --border: rgba(255, 255, 255, 0.07);
  --border-light: rgba(255, 255, 255, 0.12);

  --accent-orange: #6ea8fe;
  --accent-fill: #3b82f6;
  --accent-orange-hover: #2563eb;
}

html[data-theme="beige-light"] {
  color-scheme: light;

  --bg-primary: #faf9f5;
  --bg-secondary: #f0eee5;
  --bg-tertiary: #e8e6dc;
  --bg-input: #ffffff;
  --text-bright: #141413;
  --text-primary: #3d3d3a;
  --text-secondary: #5e5d59;
  --text-muted: #91908c;
  --border: rgba(20, 20, 19, 0.08);
  --border-light: #dedcd1;
  --accent-orange: #c15f3c;
  --accent-fill: #c15f3c;
  --accent-orange-hover: #a94e30;
}
`;
