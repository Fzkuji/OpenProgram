"use client";

/**
 * Folder + favorites + icons mutation hook for the /functions page.
 *
 * Owns no state itself — wraps the 8 small functions that mutate
 * ``FunctionsMeta`` (favorites array, folders map, icons map) and
 * persist via the caller-supplied ``saveMeta``. Three of them
 * (deleteFolder / createFolder / renameFolder) also need to update
 * the currently-selected folder; that's the only reason the hook
 * takes ``folder`` + ``setFolder`` instead of being a pure helper.
 *
 * Extracted from functions-page.tsx so the main file stays focused on
 * the catalog grid + DnD + context menus.
 */
import { useCallback } from "react";

import type { FunctionsMeta } from "./types";

export interface UseFolderMetaResult {
  cloneMeta: () => FunctionsMeta;
  toggleFav: (name: string, e: React.MouseEvent) => Promise<void>;
  moveToFolder: (name: string, target: string | null) => Promise<void>;
  deleteFolder: (name: string) => Promise<void>;
  createFolder: (name: string) => Promise<void>;
  renameFolder: (oldName: string, newName: string) => Promise<void>;
  applyIcon: (name: string, icon: string | null) => Promise<void>;
}

export function useFolderMeta(
  meta: FunctionsMeta,
  saveMeta: (next: FunctionsMeta) => Promise<void>,
  folder: string,
  setFolder: (id: string) => void,
): UseFolderMetaResult {
  // Deep-ish copy — every mutator builds a fresh object so React
  // sees a new reference and so concurrent mutations don't share
  // the same array references.
  const cloneMeta = useCallback((): FunctionsMeta => ({
    favorites: [...meta.favorites],
    folders: Object.fromEntries(
      Object.entries(meta.folders).map(([k, v]) => [k, [...v]]),
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

  const moveToFolder = useCallback(
    async (name: string, target: string | null) => {
      const next = cloneMeta();
      for (const k of Object.keys(next.folders)) {
        next.folders[k] = next.folders[k].filter((x) => x !== name);
      }
      if (target) {
        next.folders[target] = [...(next.folders[target] || []), name];
      }
      await saveMeta(next);
    },
    [cloneMeta, saveMeta],
  );

  const deleteFolder = useCallback(async (name: string) => {
    if (
      !confirm(
        `Delete folder "${name}"? Functions will be moved to Uncategorized.`,
      )
    ) {
      return;
    }
    const next = cloneMeta();
    delete next.folders[name];
    if (folder === name) setFolder("__all__");
    await saveMeta(next);
  }, [cloneMeta, folder, saveMeta, setFolder]);

  const createFolder = useCallback(async (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || meta.folders[trimmed]) return;
    const next = cloneMeta();
    next.folders[trimmed] = [];
    await saveMeta(next);
    setFolder(trimmed);
  }, [cloneMeta, meta.folders, saveMeta, setFolder]);

  const renameFolder = useCallback(
    async (oldName: string, newName: string) => {
      const trimmed = newName.trim();
      if (!trimmed || trimmed === oldName || meta.folders[trimmed]) return;
      const next = cloneMeta();
      next.folders[trimmed] = next.folders[oldName] || [];
      delete next.folders[oldName];
      if (folder === oldName) setFolder(trimmed);
      await saveMeta(next);
    },
    [cloneMeta, folder, meta.folders, saveMeta, setFolder],
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
    moveToFolder,
    deleteFolder,
    createFolder,
    renameFolder,
    applyIcon,
  };
}
