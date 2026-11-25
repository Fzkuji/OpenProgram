"use client";

/**
 * Permission-mode selector state (per-session, persisted in the store's
 * session scope's settings). Four selectable modes; inherited defaults resolve server-side.
 * See docs/capabilities/permissions.html and agent/permissions/state.py.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  useBoundComposerSettings,
  useBoundSetComposerSettings,
} from "../../state/use-composer-settings";
import { branchTarget } from "@/lib/agents/branch-target";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { permissionSnapshotPatch } from "@/lib/session-store/permission-state";
import { useTranslation } from "@/lib/i18n";
import { harnessOf } from "@/lib/models/harnesses";
import { useSessionAgentSettings } from "../../state/use-session-agent-settings";

export type PermissionMode =
  | "ask" | "acceptEdits" | "plan" | "auto" | "bypass";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
  key?: string;   // 数字快捷键提示
  description?: string;
  /** The mode a session starts in (DEFAULT_MODE) — the menu tags it "Default". */
  isDefault?: boolean;
}

// The default first, then the tiers by how much proceeds without asking. Every
// row carries a one-line description. Accept edits is an unused option kept
// valid for stored conversations, so it is listed only while it is current.
const MODE_LABELS: {
  value: PermissionMode; en: string; zh: string; key?: string;
  enDesc?: string; zhDesc?: string;
}[] = [
  {
    value: "bypass", en: "Bypass permissions", zh: "Bypass permissions", key: "1",
    enDesc: "Accepts all permissions",
    zhDesc: "接受所有权限",
  },
  {
    value: "auto", en: "Auto", zh: "Auto", key: "2",
    enDesc: "The model handles permission decisions",
    zhDesc: "由模型处理权限决定",
  },
  {
    value: "ask", en: "Manual", zh: "Manual", key: "3",
    enDesc: "Always ask before making changes",
    zhDesc: "修改前总是询问",
  },
  {
    value: "plan", en: "Plan", zh: "Plan", key: "4",
    enDesc: "Create a plan before making changes",
    zhDesc: "修改前先做计划",
  },
  {
    value: "acceptEdits", en: "Accept edits", zh: "Accept edits", key: "5",
    enDesc: "Automatically accept all file edits",
    zhDesc: "自动接受所有文件编辑",
  },
];

// A conversation a harness (Codex) runs: the same stored modes, named for
// what that harness does with each (runtime/harnesses/codex/engine.py
// PERMISSION_MODES). Plan has no Codex counterpart and reads as Read only.
const HARNESS_LABELS: typeof MODE_LABELS = [
  {
    value: "bypass", en: "Full access", zh: "完全访问", key: "1",
    enDesc: "Runs anything without asking, outside the sandbox",
    zhDesc: "不经询问运行任何操作，不受沙箱限制",
  },
  {
    value: "auto", en: "Auto", zh: "自动", key: "2",
    enDesc: "Codex reviews its own approval requests",
    zhDesc: "由 Codex 审核自己的授权请求",
  },
  {
    value: "acceptEdits", en: "Ask for approval", zh: "需要批准", key: "3",
    enDesc: "Works in the project; asks before anything else",
    zhDesc: "在项目内工作；其他操作先询问",
  },
  {
    value: "ask", en: "Read only", zh: "只读", key: "4",
    enDesc: "Reads files; asks before any change",
    zhDesc: "只读文件；任何修改前先询问",
  },
];

// Claude Code's own modes (runtime/harnesses/claude_code/engine.py PERMISSION_MODES).
const CLAUDE_CODE_LABELS: typeof MODE_LABELS = [
  {
    value: "bypass", en: "Bypass permissions", zh: "跳过授权", key: "1",
    enDesc: "Runs anything without asking",
    zhDesc: "不经询问运行任何操作",
  },
  {
    value: "auto", en: "Auto", zh: "自动", key: "2",
    enDesc: "Claude Code decides which actions need asking",
    zhDesc: "由 Claude Code 判断哪些操作需要询问",
  },
  {
    value: "acceptEdits", en: "Accept edits", zh: "自动接受编辑", key: "3",
    enDesc: "Edits files without asking; asks before commands",
    zhDesc: "直接修改文件；运行命令前先询问",
  },
  {
    value: "ask", en: "Ask", zh: "询问", key: "4",
    enDesc: "Asks before edits and commands",
    zhDesc: "修改和运行命令前都先询问",
  },
  {
    value: "plan", en: "Plan", zh: "Plan", key: "5",
    enDesc: "Plans without making changes",
    zhDesc: "只做计划，不做修改",
  },
];

// DeepSeek Harness's: it either runs its tools or asks for each one.
const DEEPSEEK_HARNESS_LABELS: typeof MODE_LABELS = [
  {
    value: "bypass", en: "Full access", zh: "完全访问", key: "1",
    enDesc: "Runs its tools without asking",
    zhDesc: "不经询问运行工具",
  },
  {
    value: "ask", en: "Ask", zh: "询问", key: "2",
    enDesc: "Asks before each tool it runs",
    zhDesc: "每次运行工具前先询问",
  },
];

/** The modes a conversation's engine offers, named for what it does with each. */
function labelsFor(harness: string | null): typeof MODE_LABELS {
  if (harness === "claude-code") return CLAUDE_CODE_LABELS;
  if (harness === "deepseek-harness") return DEEPSEEK_HARNESS_LABELS;
  return harness ? HARNESS_LABELS : MODE_LABELS;
}

/** A stored mode as the engine's own menu shows it. */
function shownMode(harness: string | null, stored: PermissionMode): PermissionMode {
  if (harness === "deepseek-harness") return stored === "bypass" ? "bypass" : "ask";
  if (harness && harness !== "claude-code" && stored === "plan") return "ask";
  return stored;
}

const DEFAULT_MODE: PermissionMode = "bypass";

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
  const branchId = settings.active_branch_id ?? null;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const setComposerSettings = useBoundSetComposerSettings();
  const { text } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const chat = useSessionAgentSettings().chat;
  const harnessId = chat?.harness ?? harnessOf(chat?.provider) ?? null;
  const inHarness = !!harnessId;
  const stored = (
    settings.effective_permission
    || settings.permission_mode
    || DEFAULT_MODE
  ) as PermissionMode;
  const mode: PermissionMode = shownMode(harnessId, stored);
  const options: PermissionModeOption[] = labelsFor(harnessId)
    .filter((m) => inHarness || m.value !== "acceptEdits" || mode === "acceptEdits")
    .map((m) => ({
      value: m.value,
      label: text(m.en, m.zh),
      key: m.key,
      description: m.enDesc ? text(m.enDesc, m.zhDesc ?? m.enDesc) : undefined,
      isDefault: m.value === DEFAULT_MODE,
    }));
  const apply = useCallback((data: { mode?: unknown; version?: unknown } | null) => {
    if (!data) return;
    const patch = permissionSnapshotPatch(settingsRef.current, data);
    if (patch) setComposerSettings(patch);
  }, [setComposerSettings]);
  useEffect(() => {
    let active = true;
    generation.current += 1;
    setError(null);
    setPending(false);
    void wsRequest<{ mode?: unknown; version?: unknown; error?: string }>(
      "set_permission", { session_id: sid, ...branchTarget(branchId) }, "permission_changed", { requestId: true },
    ).then((data) => {
      if (!active) return;
      if (data && !data.error) apply(data);
    });
    return () => { active = false; };
  }, [sid, branchId, apply]);
  const set = useCallback((m: PermissionMode) => {
    if (pending) return;
    setError(null);
    setPending(true);
    const operationGeneration = generation.current;
    const expectedVersion = settingsRef.current.permission_version;
    void (async () => {
      // A confirmed version is sufficient for the atomic update. Send it now,
      // without delaying the change behind another settings read.
      const current = expectedVersion !== undefined ? { version: expectedVersion } : await wsRequest<{ mode?: unknown; version?: number; error?: string }>(
        "set_permission", { session_id: sid, ...branchTarget(branchId) }, "permission_changed", { requestId: true },
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
      const result = await wsRequest<{ mode?: unknown; version?: unknown; error?: string }>(
        "set_permission", { session_id: sid, ...branchTarget(branchId), mode: m, expected_version: expectedVersion ?? current.version },
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
  }, [pending, sid, branchId, setComposerSettings, apply, text]);
  return { mode, options, menuOpen, setMenuOpen, set, pending, error };
}
