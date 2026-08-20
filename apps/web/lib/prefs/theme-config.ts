export const THEME_IDS = [
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "aurora-light",
  "custom",
] as const;
export type ThemeId = (typeof THEME_IDS)[number];

export const THEME_STYLES = ["beige", "neutral", "aurora", "custom"] as const;
export type ThemeStyle = (typeof THEME_STYLES)[number];

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
  custom: { dark: "custom", light: "custom" },
};

export const LEGACY_THEME_PREFERENCES = {
  auto: { style: "beige", mode: "auto" },
  "beige-dark": { style: "beige", mode: "dark" },
  "beige-light": { style: "beige", mode: "light" },
  dark: { style: "neutral", mode: "dark" },
  light: { style: "neutral", mode: "light" },
  aurora: { style: "aurora", mode: "dark" },
  custom: { style: "custom", mode: "dark" },
} as const satisfies Record<string, { style: ThemeStyle; mode: ThemeMode }>;

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return typeof value === "string"
    && (THEME_IDS as readonly string[]).includes(value);
}

export function coerceThemeStyle(value: string | null | undefined): ThemeStyle {
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

export function resolveThemePreference(
  style: ThemeStyle,
  mode: ThemeMode,
  systemDark: boolean,
): { theme: ThemeId; resolvedMode: ResolvedThemeMode } {
  const resolvedMode = mode === "auto" ? (systemDark ? "dark" : "light") : mode;
  return { theme: THEME_STYLE_PAIRS[style][resolvedMode], resolvedMode };
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
