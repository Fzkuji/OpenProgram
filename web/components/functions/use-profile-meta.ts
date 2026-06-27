"use client";

/**
 * Profile + favorites + icons mutation hook for the /functions page.
 *
 * Owns no state itself — wraps the functions that mutate
 * ``FunctionsMeta`` (favorites array, profiles map, icons map) and
 * persist via the caller-supplied ``saveMeta``. Delete/create/rename
 * need the current profile selection, so the hook takes ``profile``
 * + ``setProfile``.
 */
import { useCallback, useState } from "react";

import type { FunctionsMeta } from "./types";

export interface UseProfileMetaResult {
  cloneMeta: () => FunctionsMeta;
  toggleFav: (name: string, e: React.MouseEvent) => Promise<void>;
  moveToProfile: (name: string, target: string | null) => Promise<void>;
  requestDeleteProfile: (name: string) => void;
  confirmDeleteProfile: () => Promise<void>;
  cancelDeleteProfile: () => void;
  pendingDelete: string | null;
  createProfile: (name: string, defaultContents?: string[]) => Promise<void>;
  renameProfile: (oldName: string, newName: string) => Promise<void>;
  applyIcon: (name: string, icon: string | null) => Promise<void>;
}

export function useProfileMeta(
  meta: FunctionsMeta,
  saveMeta: (next: FunctionsMeta) => Promise<void>,
  profile: string,
  setProfile: (id: string) => void,
): UseProfileMetaResult {
  const cloneMeta = useCallback((): FunctionsMeta => ({
    favorites: [...meta.favorites],
    profiles: Object.fromEntries(
      Object.entries(meta.profiles || {}).map(([k, v]) => [k, [...v]]),
    ),
    icons: { ...meta.icons },
  }), [meta]);

  const toggleFav = useCallback(async (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const next = cloneMeta();
    const idx = next.favorites.indexOf(name);
    if (idx >= 0) next.favorites.splice(idx, 1);
    else next.favorites.push(name);
    await saveMeta(next);
  }, [cloneMeta, saveMeta]);

  const moveToProfile = useCallback(
    async (name: string, target: string | null) => {
      const next = cloneMeta();
      for (const k of Object.keys(next.profiles)) {
        next.profiles[k] = next.profiles[k].filter((x: string) => x !== name);
      }
      if (target) {
        next.profiles[target] = [...(next.profiles[target] || []), name];
      }
      await saveMeta(next);
    },
    [cloneMeta, saveMeta],
  );

  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const requestDeleteProfile = useCallback((name: string) => {
    setPendingDelete(name);
  }, []);

  const cancelDeleteProfile = useCallback(() => {
    setPendingDelete(null);
  }, []);

  const confirmDeleteProfile = useCallback(async () => {
    const name = pendingDelete;
    if (!name) return;
    setPendingDelete(null);
    const next = cloneMeta();
    delete next.profiles[name];
    if (profile === name) setProfile("__all__");
    await saveMeta(next);
  }, [cloneMeta, pendingDelete, profile, saveMeta, setProfile]);

  const createProfile = useCallback(async (name: string, defaultContents?: string[]) => {
    const trimmed = name.trim();
    if (!trimmed || (meta.profiles || {})[trimmed]) return;
    const next = cloneMeta();
    next.profiles[trimmed] = defaultContents ? [...defaultContents] : [];
    await saveMeta(next);
    setProfile(trimmed);
  }, [cloneMeta, meta.profiles, saveMeta, setProfile]);

  const renameProfile = useCallback(
    async (oldName: string, newName: string) => {
      const trimmed = newName.trim();
      if (!trimmed || trimmed === oldName || (meta.profiles || {})[trimmed]) return;
      const next = cloneMeta();
      next.profiles[trimmed] = next.profiles[oldName] || [];
      delete next.profiles[oldName];
      if (profile === oldName) setProfile(trimmed);
      await saveMeta(next);
    },
    [cloneMeta, profile, meta.profiles, saveMeta, setProfile],
  );

  const applyIcon = useCallback(
    async (name: string, icon: string | null) => {
      const next = cloneMeta();
      if (icon) next.icons[name] = icon;
      else delete next.icons[name];
      await saveMeta(next);
    },
    [cloneMeta, saveMeta],
  );

  return {
    cloneMeta,
    toggleFav,
    moveToProfile,
    requestDeleteProfile,
    confirmDeleteProfile,
    cancelDeleteProfile,
    pendingDelete,
    createProfile,
    renameProfile,
    applyIcon,
  };
}
