"use client";

/**
 * Settings → Permissions：管理当前会话的权限规则（allow / deny / ask）。
 * 列出已记住的规则、手动加、逐条删。规则语法见 permission_rule.py
 * （ToolName 或 ToolName(pattern)）。见 permission-model.md §4.6。
 */
import { useEffect, useState, useCallback } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./settings-page.module.css";

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

export function PermissionsSection() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [rules, setRules] = useState<Rules>(EMPTY);
  const [draft, setDraft] = useState<Record<Behavior, string>>({
    deny: "", ask: "", allow: "",
  });

  // 收后端广播的 permission_rules 帧（只认当前会话的）。
  useEffect(() => {
    function onRules(e: Event) {
      const d = (e as CustomEvent).detail?.data ?? (e as CustomEvent).detail;
      if (!d || (sessionId && d.session_id !== sessionId)) return;
      setRules({
        deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [],
      });
    }
    window.addEventListener("op:permission-rules", onRules as EventListener);
    return () => window.removeEventListener("op:permission-rules", onRules as EventListener);
  }, [sessionId]);

  // 进入页面时拉一次当前会话的规则。
  useEffect(() => {
    if (sessionId) wsSend({ action: "list_permission_rules", session_id: sessionId });
  }, [sessionId]);

  const add = useCallback((behavior: Behavior) => {
    const rule = draft[behavior].trim();
    if (!rule || !sessionId) return;
    wsSend({ action: "add_permission_rule", session_id: sessionId, behavior, rule });
    setDraft((d) => ({ ...d, [behavior]: "" }));
  }, [draft, sessionId]);

  const remove = useCallback((behavior: Behavior, rule: string) => {
    if (!sessionId) return;
    wsSend({ action: "remove_permission_rule", session_id: sessionId, behavior, rule });
  }, [sessionId]);

  if (!sessionId) {
    return (
      <div className={styles.section}>
        <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
          {text("Open a chat to manage its permission rules.",
                "打开一个会话后可管理它的权限规则。")}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.section}>
      <h2 className={styles.sectionTitle}>{text("Permission Rules", "权限规则")}</h2>
      <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
        {text(
          "Per-session allow / deny / ask rules. Syntax: ToolName or ToolName(pattern), e.g. bash(git:*).",
          "当前会话的允许 / 拒绝 / 询问规则。语法：工具名 或 工具名(模式)，如 bash(git:*)。",
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
