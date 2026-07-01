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

// 常规档（"要不要批准"维度，按危险度递增）+ plan 单列（只读规划，另一维度）。
export const PERMISSION_MODE_OPTIONS: PermissionModeOption[] = [
  { value: "ask", label: "每步都问我" },
  { value: "auto", label: "只有危险操作才问" },
  { value: "acceptEdits", label: "改文件不用问" },
  { value: "dontAsk", label: "别打扰我（危险操作直接跳过）" },
  { value: "bypass", label: "全部自动执行" },
  { value: "plan", label: "只读规划（不改任何东西）" },  // 特殊模式，UI 里用分隔线分开
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
