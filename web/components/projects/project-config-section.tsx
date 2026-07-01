"use client";

/**
 * 项目级默认配置——作为该项目下新会话的默认值。存 project settings.json，
 * 空 = 继承（不覆盖）。目前：默认权限档 / 默认工具集 / 默认思考力度。
 */
import { useCallback, useEffect, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";

type Config = {
  permission_mode?: string;
  toolset?: string;
  thinking_effort?: string;
};

function wsSend(payload: unknown): boolean {
  const ws = (window as unknown as { ws?: WebSocket }).ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

export function ProjectConfigSection({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const [cfg, setCfg] = useState<Config>({});

  const refresh = useCallback(async () => {
    const d = await wsRequest<{ config: Config }>(
      "get_project_config", { project_id: projectId }, "project_config",
    );
    if (d) setCfg(d.config || {});
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const setField = (key: keyof Config, value: string) => {
    setCfg((c) => ({ ...c, [key]: value || undefined }));
    wsSend({ action: "set_project_config", project_id: projectId, key, value });
  };

  const rows: {
    key: keyof Config; label: string; opts: { v: string; label: string }[];
  }[] = [
    {
      key: "permission_mode",
      label: text("Default permission mode", "默认权限模式"),
      opts: [
        { v: "", label: text("Inherit", "继承") },
        { v: "ask", label: text("Default", "默认") },
        { v: "acceptEdits", label: text("Accept Edits", "接受编辑") },
        { v: "dontAsk", label: text("Don't Ask", "不再询问") },
        { v: "bypass", label: text("Bypass", "绕过权限") },
        { v: "plan", label: text("Plan Mode", "计划模式") },
      ],
    },
    {
      key: "toolset",
      label: text("Default toolset", "默认工具集"),
      opts: [
        { v: "", label: text("Inherit", "继承") },
        { v: "core", label: "core" },
        { v: "research", label: "research" },
      ],
    },
    {
      key: "thinking_effort",
      label: text("Default thinking", "默认思考力度"),
      opts: [
        { v: "", label: text("Inherit", "继承") },
        { v: "off", label: "off" },
        { v: "low", label: "low" },
        { v: "medium", label: "medium" },
        { v: "high", label: "high" },
      ],
    },
  ];

  return (
    <div>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
        {text(
          "Defaults for new chats in this project. Empty = inherit.",
          "该项目新会话的默认值。留空 = 继承全局。",
        )}
      </p>
      {rows.map((r) => (
        <div key={r.key} style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "8px 0", gap: 16,
        }}>
          <span style={{ fontSize: 14, color: "var(--text-primary)" }}>{r.label}</span>
          <select
            value={cfg[r.key] || ""}
            onChange={(e) => setField(r.key, e.target.value)}
            style={{
              padding: "6px 10px", borderRadius: 8,
              border: "1px solid var(--border)", background: "var(--bg-primary)",
              color: "var(--text-primary)", minWidth: 160,
            }}
          >
            {r.opts.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </div>
      ))}
    </div>
  );
}
