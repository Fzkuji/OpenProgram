"use client";

/**
 * Permission-mode selector state (per-session, persisted in the store's
 * composerSettings). Six fixed modes — no provider polling like thinking.
 * See docs/design/runtime/permission-model.md §2.1 / §4.5.
 */
import { useCallback, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

export type PermissionMode =
  | "ask" | "acceptEdits" | "plan" | "auto" | "bypass";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
  key?: string;   // 数字快捷键提示（1-4）；bypass 无（走 Enable 确认）
}

// 常规档（批准强度，按危险度递增）+ plan 单列（只读，另一维度）。
// 照 Claude Code 网页端 Mode 菜单：Ask permissions(1) / Accept edits(2) /
// Plan mode(3) / Auto mode(4) / Bypass permissions(Enable 确认，无数字)。
const MODE_LABELS: { value: PermissionMode; en: string; zh: string; key?: string }[] = [
  { value: "ask", en: "Ask permissions", zh: "逐次确认", key: "1" },
  { value: "acceptEdits", en: "Accept edits", zh: "接受编辑", key: "2" },
  { value: "plan", en: "Plan mode", zh: "计划模式", key: "3" },
  { value: "auto", en: "Auto mode", zh: "自动判定", key: "4" },
  { value: "bypass", en: "Bypass permissions", zh: "绕过权限" },
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
  const { text } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const mode = (stored as PermissionMode) || DEFAULT_MODE;
  const options: PermissionModeOption[] = MODE_LABELS.map((m) => ({
    value: m.value,
    label: text(m.en, m.zh),
    key: m.key,
  }));
  const set = useCallback(
    (m: PermissionMode) => setComposerSettings({ permission_mode: m }),
    [setComposerSettings],
  );
  return { mode, options, menuOpen, setMenuOpen, set };
}
