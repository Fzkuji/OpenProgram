/**
 * Theme preference (Settings → General → Theme): Standard, Code rain or
 * Gradient. Code rain gives the chat the Matrix look, with a density for
 * the empty chat's rain; Gradient puts the indigo / violet / teal backdrop
 * behind the new-tab Launchpad. Standard (neither) is the default. Saved
 * on this device (localStorage) and broadcast to every mounted reader,
 * including other windows.
 */
"use client";

import { useEffect, useState } from "react";

export const WELCOME_BACKDROP_STORAGE_KEY = "openprogram.welcome.codeRain";
export const WELCOME_BACKDROP_DENSITY_KEY = "openprogram.welcome.codeRainDensity";
export const GRADIENT_THEME_STORAGE_KEY = "openprogram.theme.gradient";

/** Density in percent: 100 puts about 1.35 drops on every column. */
export const CODE_RAIN_DENSITY = { min: 10, max: 100, default: 40 } as const;

export type BackdropTheme = "standard" | "code-rain" | "gradient";
type State = { enabled: boolean; density: number; gradient: boolean };

let current: State = { enabled: false, density: CODE_RAIN_DENSITY.default, gradient: false };
const subscribers = new Set<(value: State) => void>();

function clampDensity(value: number): number {
  if (!Number.isFinite(value)) return CODE_RAIN_DENSITY.default;
  return Math.min(CODE_RAIN_DENSITY.max, Math.max(CODE_RAIN_DENSITY.min, Math.round(value)));
}

function read(): State {
  try {
    const stored = window.localStorage.getItem(WELCOME_BACKDROP_DENSITY_KEY);
    const enabled = window.localStorage.getItem(WELCOME_BACKDROP_STORAGE_KEY) === "on";
    return {
      enabled,
      density: stored === null ? CODE_RAIN_DENSITY.default : clampDensity(Number(stored)),
      // The themes are exclusive; code rain wins a stale pair from another window.
      gradient: !enabled && window.localStorage.getItem(GRADIENT_THEME_STORAGE_KEY) === "on",
    };
  } catch {
    return { enabled: false, density: CODE_RAIN_DENSITY.default, gradient: false };
  }
}

function update(next: State, persist: boolean): void {
  current = next;
  // The code rain style's CSS keys off this (code-rain-style.css), the
  // Launchpad's gradient off data-backdrop (launchpad.module.css).
  document.documentElement.dataset.codeRain = next.enabled ? "on" : "off";
  document.documentElement.dataset.backdrop = next.gradient ? "gradient" : "standard";
  if (persist) {
    try {
      window.localStorage.setItem(WELCOME_BACKDROP_STORAGE_KEY, next.enabled ? "on" : "off");
      window.localStorage.setItem(WELCOME_BACKDROP_DENSITY_KEY, String(next.density));
      window.localStorage.setItem(GRADIENT_THEME_STORAGE_KEY, next.gradient ? "on" : "off");
    } catch { /* The current document still updates if storage is unavailable. */ }
  }
  subscribers.forEach((subscriber) => subscriber(next));
}

export function useWelcomeBackdropPref() {
  const [state, setState] = useState(current);
  useEffect(() => {
    subscribers.add(setState);
    update(read(), false);
    const onStorage = (event: StorageEvent) => {
      if (
        event.key === WELCOME_BACKDROP_STORAGE_KEY
        || event.key === WELCOME_BACKDROP_DENSITY_KEY
        || event.key === GRADIENT_THEME_STORAGE_KEY
        || event.key === null
      ) update(read(), false);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      subscribers.delete(setState);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  const theme: BackdropTheme = state.enabled ? "code-rain" : state.gradient ? "gradient" : "standard";
  return {
    enabled: state.enabled,
    density: state.density,
    theme,
    setEnabled: (enabled: boolean) => update({ ...current, enabled, gradient: enabled ? false : current.gradient }, true),
    setTheme: (next: BackdropTheme) => update({ ...current, enabled: next === "code-rain", gradient: next === "gradient" }, true),
    setDensity: (density: number) => update({ ...current, density: clampDensity(density) }, true),
  };
}
