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
        { v: "", label: text("Not set", "不设") },
        { v: "ask", label: text("Ask permissions", "逐次确认") },
        { v: "acceptEdits", label: text("Accept edits", "接受编辑") },
        { v: "plan", label: text("Plan mode", "计划模式") },
        { v: "auto", label: text("Auto mode", "自动判定") },
        { v: "bypass", label: text("Bypass permissions", "绕过权限") },
      ],
    },
    {
      key: "thinking_effort",
      label: text("Default thinking", "默认思考力度"),
      opts: [
        { v: "", label: text("Not set", "不设") },
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
          "New chats in this project start with these. \"Not set\" leaves it at the built-in default.",
          "该项目的新会话默认用这些设置。“不设”= 用内置默认。",
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
