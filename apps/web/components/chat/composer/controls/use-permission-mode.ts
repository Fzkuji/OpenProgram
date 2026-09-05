"use client";

/**
 * Permission-mode selector state (per-session, persisted in the store's
 * session scope's settings). Six fixed modes — no provider polling like thinking.
 * See docs/design/runtime/permission-model.md §2.1 / §4.5.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../state/use-composer-settings";
import { wsRequest } from "@/lib/net/ws-request";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { permissionSnapshotPatch } from "@/lib/session-store/permission-state";
import { useTranslation } from "@/lib/i18n";

export type PermissionMode =
  | "ask" | "acceptEdits" | "plan" | "auto" | "bypass";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
  key?: string;   // 数字快捷键提示（1-4）；bypass 无（走 Enable 确认）
  description?: string;
}

// 常规档（批准强度，按危险度递增）+ plan 单列（只读，另一维度）。
// 照 Claude Code 网页端 Mode 菜单：Ask permissions(1) / Accept edits(2) /
// Plan mode(3) / Auto mode(4) / Bypass permissions(Enable 确认，无数字)。
const MODE_LABELS: {
  value: PermissionMode; en: string; zh: string; key?: string;
  enDesc?: string; zhDesc?: string;
}[] = [
  { value: "ask", en: "Ask permissions", zh: "逐次确认", key: "1" },
  { value: "acceptEdits", en: "Accept edits", zh: "接受编辑", key: "2" },
  { value: "plan", en: "Plan mode", zh: "计划模式", key: "3" },
  { value: "auto", en: "Auto mode", zh: "自动判定", key: "4" },
  {
    value: "bypass",
    en: "Bypass permissions",
    zh: "跳过审批",
    key: "5",
    enDesc: "Skips ordinary approvals; explicit rules and sandbox restrictions still apply.",
    zhDesc: "跳过普通审批；显式规则和沙箱限制仍然生效。",
  },
];

const DEFAULT_MODE: PermissionMode = "ask";

export interface PermissionModeHook {
  mode: PermissionMode;
  pending: boolean;
  error: string | null;
  options: PermissionModeOption[];
  menuOpen: boolean;
  setMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
  set: (m: PermissionMode) => void;
}

export function usePermissionMode(): PermissionModeHook {
  // Bound to this composer subtree's session scope.
  const settings = useBoundComposerSettings();
  const sid = useSessionScope((s) => s.sid);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const known = useRef(false);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const setComposerSettings = useBoundSetComposerSettings();
  const { text } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const mode = (
    settings.effective_permission
    || settings.permission_mode
    || DEFAULT_MODE
  ) as PermissionMode;
  const options: PermissionModeOption[] = MODE_LABELS.map((m) => ({
    value: m.value,
    label: text(m.en, m.zh),
    key: m.key,
    description: m.enDesc ? text(m.enDesc, m.zhDesc ?? m.enDesc) : undefined,
  }));
  const apply = useCallback((data: { mode?: unknown; version?: unknown } | null) => {
    if (!data) return;
    const patch = permissionSnapshotPatch(settingsRef.current, data);
    if (patch) setComposerSettings(patch);
  }, [setComposerSettings]);
  useEffect(() => {
    let active = true;
    generation.current += 1;
    known.current = false;
    setError(null);
    setPending(false);
    void wsRequest<{ mode?: unknown; version?: unknown; error?: string }>(
      "set_permission", { session_id: sid }, "permission_changed", { requestId: true },
    ).then((data) => {
      if (!active) return;
      if (data && !data.error) { known.current = true; apply(data); }
    });
    return () => { active = false; };
  }, [sid, apply]);
  const set = useCallback((m: PermissionMode) => {
    if (pending) return;
    setError(null);
    setPending(true);
    const operationGeneration = generation.current;
    const expectedVersion = settingsRef.current.permission_version;
    void (async () => {
      const current = await wsRequest<{ mode?: unknown; version?: number; error?: string }>(
        "set_permission", { session_id: sid }, "permission_changed", { requestId: true },
      );
      if (operationGeneration !== generation.current) return;
      if (current?.error === "session_not_found") {
        setComposerSettings({ permission_mode: m, effective_permission: m });
        return;
      }
      if (!current || current.error || current.version === undefined) {
        setError(text("Could not read permission settings. Reconnect and retry.", "无法读取权限设置，请重新连接后重试。"));
        return;
      }
      known.current = true;
      const result = await wsRequest<{ mode?: unknown; version?: unknown; error?: string }>(
        "set_permission", { session_id: sid, mode: m, expected_version: expectedVersion ?? current.version },
        "permission_changed", { requestId: true },
      );
      if (operationGeneration !== generation.current) return;
      apply(result ?? current);
      if (!result || result.error) setError(result?.error === "permission_version_conflict"
        ? text("Another window changed permissions; check the current mode and retry.", "另一个窗口已更改权限，请检查当前模式后重试。")
        : text("Permission update was not confirmed. Reconnect and retry.", "权限更新尚未确认，请重新连接后重试。"));
    })().finally(() => {
      if (operationGeneration === generation.current) setPending(false);
    });
  }, [pending, sid, setComposerSettings, apply, text]);
  return { mode, options, menuOpen, setMenuOpen, set, pending, error };
}
