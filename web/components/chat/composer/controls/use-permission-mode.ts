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
  | "ask" | "acceptEdits" | "plan" | "dontAsk" | "bypass";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
}

// 常规档（批准强度，按危险度递增）+ plan 单列（只读，另一维度）。
// 专业术语，双语随界面语言切换。
const MODE_LABELS: { value: PermissionMode; en: string; zh: string }[] = [
  { value: "ask", en: "Confirm each action", zh: "逐次确认" },
  { value: "acceptEdits", en: "Auto-accept edits", zh: "自动批准编辑" },
  { value: "dontAsk", en: "Never prompt · deny risky", zh: "免确认（高危拒绝）" },
  { value: "bypass", en: "Bypass all", zh: "全部放行" },
  { value: "plan", en: "Plan · read-only", zh: "计划模式（只读）" },
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
  }));
  const set = useCallback(
    (m: PermissionMode) => setComposerSettings({ permission_mode: m }),
    [setComposerSettings],
  );
  return { mode, options, menuOpen, setMenuOpen, set };
}
