"use client";

/**
 * 项目级默认配置——作为该项目下新会话的默认值。存 project settings.json，
 * 空 = 继承（不覆盖）。目前：默认权限档 / 默认工具集 / 默认思考力度。
 */
import { useCallback, useEffect, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { SimpleSelect } from "@/components/ui/select";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-ui";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { getSocket } from "@/lib/runtime-bridge/state";

type Config = {
  permission_mode?: string;
  thinking_effort?: string;
};

function wsSend(payload: unknown): boolean {
  const ws = getSocket();
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
    key: keyof Config; label: string; fallback: string;
    opts: { v: string; label: string }[];
  }[] = [
    {
      key: "permission_mode",
      label: text("Default permission mode", "默认权限模式"),
      // 无 "Not set"——没设就是内置默认档 Bypass。
      fallback: "bypass",
      opts: [
        { v: "bypass", label: "Bypass permissions" },
        { v: "auto", label: "Auto" },
        { v: "ask", label: "Manual" },
        { v: "plan", label: "Plan" },
        ...(cfg.permission_mode === "acceptEdits"
          ? [{ v: "acceptEdits", label: "Accept edits" }]
          : []),
      ],
    },
    {
      key: "thinking_effort",
      label: text("Default thinking", "默认思考力度"),
      fallback: "high",   // 默认 high；运行时若当前模型不支持会自动降级到它支持的档
      opts: [
        { v: "off", label: "off" },
        { v: "minimal", label: "minimal" },
        { v: "low", label: "low" },
        { v: "medium", label: "medium" },
        { v: "high", label: "high" },
        { v: "xhigh", label: "xhigh" },
        { v: "max", label: "max" },
      ],
    },
  ];

  return (
    <SettingsSection
      title={text("Default Settings", "默认设置")}
      description={text("New chats in this project start with these.", "该项目的新会话默认用这些设置。")}
    >
      {rows.map((r) => (
        <SettingsRow key={r.key} label={r.label}>
          <SimpleSelect
            aria-label={r.label}
            value={cfg[r.key] || r.fallback}
            onValueChange={(value) => setField(r.key, value)}
            options={r.opts.map((o) => ({ value: o.v, label: o.label }))}
          />
        </SettingsRow>
      ))}
    </SettingsSection>
  );
}
