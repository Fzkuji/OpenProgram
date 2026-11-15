/** Shared appearance of dragged tabs and pane-label previews. */
export const VISUAL_EFFECTS_STORAGE_KEY = "agentic_visual_effects";
export const VISUAL_EFFECTS_LIMITS = {
  transparency: { min: 0, max: 100 },
  blur: { min: 0, max: 40 },
  shadow: { min: 0, max: 100 },
} as const;
export const DEFAULT_VISUAL_EFFECTS = { transparency: 20, blur: 16, shadow: 25 };
export type VisualEffects = typeof DEFAULT_VISUAL_EFFECTS;
export type VisualEffectKey = keyof VisualEffects;

/** Invalid or missing fields use defaults; finite values are bounded. */
export function normalizeVisualEffects(input: unknown): VisualEffects {
  const values = input && typeof input === "object" ? input as Partial<VisualEffects> : {};
  const next = { ...DEFAULT_VISUAL_EFFECTS };
  for (const key of Object.keys(next) as VisualEffectKey[]) {
    const value = values[key];
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    const { min, max } = VISUAL_EFFECTS_LIMITS[key];
    next[key] = Math.round(Math.min(max, Math.max(min, value)));
  }
  return next;
}

export function parseVisualEffects(raw: string | null): VisualEffects {
  try { return normalizeVisualEffects(raw ? JSON.parse(raw) : null); }
  catch { return { ...DEFAULT_VISUAL_EFFECTS }; }
}

/** Shared by both drag implementations and the settings preview. */
export const DRAG_SURFACE_STYLE = {
  background: "var(--drag-surface-background)",
  backdropFilter: "var(--drag-surface-backdrop)",
  boxShadow: "var(--drag-surface-shadow)",
} as const;
