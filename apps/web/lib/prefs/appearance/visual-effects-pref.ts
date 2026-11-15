"use client";

import { useEffect, useState } from "react";
import {
  DEFAULT_VISUAL_EFFECTS,
  normalizeVisualEffects,
  parseVisualEffects,
  VISUAL_EFFECTS_STORAGE_KEY,
  type VisualEffects,
  type VisualEffectKey,
} from "./visual-effects-config";

let current: VisualEffects = { ...DEFAULT_VISUAL_EFFECTS };
const subscribers = new Set<(value: VisualEffects) => void>();

function read(): VisualEffects {
  try { return parseVisualEffects(window.localStorage.getItem(VISUAL_EFFECTS_STORAGE_KEY)); }
  catch { return { ...DEFAULT_VISUAL_EFFECTS }; }
}

export function applyVisualEffects(value: VisualEffects): void {
  if (typeof document === "undefined") return;
  const normalized = normalizeVisualEffects(value);
  const root = document.documentElement.style;
  root.setProperty("--drag-surface-opacity", `${100 - normalized.transparency}%`);
  root.setProperty("--drag-backdrop-blur", `${normalized.blur}px`);
  root.setProperty("--drag-shadow-scale", `${normalized.shadow / 100}`);
}

function update(value: VisualEffects, persist: boolean): void {
  current = normalizeVisualEffects(value);
  if (typeof window !== "undefined") {
    if (persist) {
      try { window.localStorage.setItem(VISUAL_EFFECTS_STORAGE_KEY, JSON.stringify(current)); }
      catch { /* The current document still updates if storage is unavailable. */ }
    }
    applyVisualEffects(current);
  }
  subscribers.forEach((subscriber) => subscriber({ ...current }));
}

export function useVisualEffectsPref() {
  const [effects, setEffects] = useState<VisualEffects>(current);
  useEffect(() => {
    const subscriber = (value: VisualEffects) => setEffects(value);
    subscribers.add(subscriber);
    update(read(), false);
    const onStorage = (event: StorageEvent) => {
      if (event.key === VISUAL_EFFECTS_STORAGE_KEY || event.key === null) update(read(), false);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      subscribers.delete(subscriber);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return {
    effects,
    setEffect: (key: VisualEffectKey, value: number) => update({ ...current, [key]: value }, true),
    resetEffects: () => update(DEFAULT_VISUAL_EFFECTS, true),
  };
}
