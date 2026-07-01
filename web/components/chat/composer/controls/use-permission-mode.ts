"use client";

/**
 * Permission-mode selector state (per-session, persisted in the store's
 * composerSettings). Six fixed modes — no provider polling like thinking.
 * See docs/design/runtime/permission-model.md §2.1 / §4.5.
 */
import { useCallback, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

export type PermissionMode =
  | "ask" | "auto" | "acceptEdits" | "plan" | "dontAsk" | "bypass";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
}

export const PERMISSION_MODE_OPTIONS: PermissionModeOption[] = [
  { value: "ask", label: "逐次确认" },
  { value: "auto", label: "自动（危险才问）" },
  { value: "acceptEdits", label: "自动批改文件" },
  { value: "plan", label: "计划（只读）" },
  { value: "dontAsk", label: "不打断（拒绝需确认的）" },
  { value: "bypass", label: "全部放行" },
];

const DEFAULT_MODE: PermissionMode = "ask";

export interface PermissionModeHook {
  mode: PermissionMode;
  options: PermissionModeOption[];
  menuOpen: boolean;
  setMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
  set: (m: PermissionMode) => void;
}

export function usePermissionMode(): PermissionModeHook {
  const stored = useSessionStore((s) => s.composerSettings.permission_mode);
  const setComposerSettings = useSessionStore((s) => s.setComposerSettings);
  const [menuOpen, setMenuOpen] = useState(false);
  const mode = (stored as PermissionMode) || DEFAULT_MODE;
  const set = useCallback(
    (m: PermissionMode) => setComposerSettings({ permission_mode: m }),
    [setComposerSettings],
  );
  return { mode, options: PERMISSION_MODE_OPTIONS, menuOpen, setMenuOpen, set };
}
