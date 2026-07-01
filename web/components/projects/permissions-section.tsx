"use client";

/**
 * 权限规则管理（按项目）。列出某项目的 allow / deny / ask 规则、手动加、
 * 逐条删。规则跟项目走（<project>/.openprogram/settings.json）。
 * 规则语法见 permission_rule.py（ToolName 或 ToolName(pattern)）。
 * 见 permission-model.md §2.2 / §4.6。
 */
import { useEffect, useState, useCallback } from "react";

import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";

type Behavior = "deny" | "ask" | "allow";
type Rules = Record<Behavior, string[]>;

const EMPTY: Rules = { deny: [], ask: [], allow: [] };

function wsSend(payload: unknown): boolean {
  const ws = (window as unknown as { ws?: WebSocket }).ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

const BEHAVIORS: { key: Behavior; en: string; zh: string; color: string }[] = [
  { key: "deny", en: "Deny", zh: "拒绝", color: "var(--danger, #d72518)" },
  { key: "ask", en: "Ask", zh: "询问", color: "var(--warning, #d78a18)" },
  { key: "allow", en: "Allow", zh: "允许", color: "var(--success, #3a9d5a)" },
];

export function PermissionsSection({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const [rules, setRules] = useState<Rules>(EMPTY);
  const [draft, setDraft] = useState<Record<Behavior, string>>({
    deny: "", ask: "", allow: "",
  });

  const refresh = useCallback(async () => {
    if (!projectId) return;
    const d = await wsRequest<{ project_id: string } & Rules>(
      "list_permission_rules", { project_id: projectId }, "permission_rules",
    );
    if (d) setRules({ deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [] });
  }, [projectId]);

  // 收后端广播的 permission_rules 帧（只认本项目）。
  useEffect(() => {
    function onRules(e: Event) {
      const ws = (e as MessageEvent).data;
      try {
        const m = JSON.parse(ws);
        if (m?.type !== "permission_rules") return;
        const d = m.data;
        if (d?.project_id !== projectId) return;
        setRules({ deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [] });
      } catch { /* ignore */ }
    }
    const sock = (window as unknown as { ws?: WebSocket }).ws;
    sock?.addEventListener("message", onRules as EventListener);
    refresh();
    return () => sock?.removeEventListener("message", onRules as EventListener);
  }, [projectId, refresh]);

  const add = useCallback((behavior: Behavior) => {
    const rule = draft[behavior].trim();
    if (!rule || !projectId) return;
    wsSend({ action: "add_permission_rule", project_id: projectId, behavior, rule });
    setDraft((d) => ({ ...d, [behavior]: "" }));
  }, [draft, projectId]);

  const remove = useCallback((behavior: Behavior, rule: string) => {
    if (!projectId) return;
    wsSend({ action: "remove_permission_rule", project_id: projectId, behavior, rule });
  }, [projectId]);

  return (
    <div>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
        {text(
          "Rules travel with the project. Syntax: ToolName or ToolName(pattern), e.g. bash(git:*).",
          "规则跟随项目保存。语法：工具名 或 工具名(模式)，如 bash(git:*)。",
        )}
      </p>

      {BEHAVIORS.map(({ key, en, zh, color }) => (
        <div key={key} style={{ marginTop: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", background: color,
              display: "inline-block",
            }} />
            <strong>{text(en, zh)}</strong>
          </div>

          {rules[key].length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: 13, paddingLeft: 16 }}>
              {text("No rules.", "暂无规则。")}
            </div>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {rules[key].map((rule) => (
                <li key={rule} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "6px 12px", borderRadius: 8,
                  background: "var(--bg-tertiary)", marginBottom: 4,
                }}>
                  <code style={{ fontFamily: "var(--font-mono)" }}>{rule}</code>
                  <button
                    type="button"
                    onClick={() => remove(key, rule)}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      color: "var(--text-muted)", fontSize: 16,
                    }}
                    aria-label={text("Remove", "删除")}
                  >×</button>
                </li>
              ))}
            </ul>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <input
              value={draft[key]}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") add(key); }}
              placeholder={text("e.g. bash(git:*)", "如 bash(git:*)")}
              style={{
                flex: 1, padding: "6px 10px", borderRadius: 8,
                border: "1px solid var(--border)", background: "var(--bg-primary)",
                color: "var(--text-primary)",
              }}
            />
            <button
              type="button"
              onClick={() => add(key)}
              style={{
                padding: "6px 14px", borderRadius: 8,
                border: "1px solid var(--border)", background: "var(--bg-tertiary)",
                color: "var(--text-primary)", cursor: "pointer",
              }}
            >{text("Add", "添加")}</button>
          </div>
        </div>
      ))}
    </div>
  );
}
