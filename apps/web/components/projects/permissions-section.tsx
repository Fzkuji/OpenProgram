"use client";

/**
 * 权限规则管理（按项目）。列出某项目的 allow / deny / ask 规则、手动加、
 * 逐条删。规则跟项目走（<project>/.openprogram/settings.json）。
 * 规则语法见 permission_rule.py（ToolName 或 ToolName(pattern)）。
 * 见 permission-model.md §2.2 / §4.6。
 */
import { useEffect, useState, useCallback, useRef } from "react";

import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Behavior = "deny" | "ask" | "allow";
type Rules = Record<Behavior, string[]>;

const EMPTY: Rules = { deny: [], ask: [], allow: [] };

const BEHAVIORS: { key: Behavior; en: string; zh: string; color: string }[] = [
  { key: "deny", en: "Deny", zh: "拒绝", color: "var(--danger, #d72518)" },
  { key: "ask", en: "Ask", zh: "询问", color: "var(--warning, #d78a18)" },
  { key: "allow", en: "Allow", zh: "允许", color: "var(--success, #3a9d5a)" },
];

export function PermissionsSection({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const [notice, setNotice] = useState("");
  const [rules, setRules] = useState<Rules>(EMPTY);
  const [draft, setDraft] = useState<Record<Behavior, string>>({
    deny: "", ask: "", allow: "",
  });

  type Reply = Partial<Rules> & { project_id?: string; status?: string; error?: string };
  const request = useCallback(async (action: string, payload: Record<string, unknown> = {}) => {
    return wsRequest<Reply>(action, { project_id: projectId, ...payload }, "permission_rules",
      (d) => d.project_id === projectId, 10000, { requestId: true });
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    setRules(EMPTY);
    setDraft({ deny: "", ask: "", allow: "" });
    setNotice("");
    const refresh = async () => {
      const d = await request("list_permission_rules");
      if (controller.signal.aborted) return;
      if (!d || d.status === "error") {
        setNotice(d?.error || text("Could not load rules. Reconnect and reopen this project.", "无法加载规则，请恢复连接后重新打开项目。"));
        return;
      }
      setRules({ deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [] });
    };
    function onRules(e: WindowEventMap["op:permission-rules"]) {
      const d = e.detail as Reply;
      if (d?.project_id !== projectId || d.status === "error") return;
      setRules({ deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [] });
    }
    window.addEventListener("op:permission-rules", onRules);
    void refresh();
    return () => { controller.abort(); window.removeEventListener("op:permission-rules", onRules); };
  }, [projectId, request, text]);

  const mutate = async (behavior: Behavior, rule: string, add: boolean) => {
    if (!rule || busy.current) return;
    busy.current = true;
    setPending(true);
    setNotice("");
    try {
      const d = await request(add ? "add_permission_rule" : "remove_permission_rule", { behavior, rule });
      if (currentProject.current !== projectId) return;
      if (!d || d.status === "error") {
        setNotice(d?.error || text("Save not confirmed. Your input is preserved; retry after reconnecting.", "尚未确认保存，输入已保留；恢复连接后可重试。"));
        return;
      }
      setRules({ deny: d.deny ?? [], ask: d.ask ?? [], allow: d.allow ?? [] });
      if (add) setDraft((old) => ({ ...old, [behavior]: old[behavior].trim() === rule ? "" : old[behavior] }));
      setNotice(text("Saved", "已保存"));
    } finally { busy.current = false; setPending(false); }
  };
  const add = (behavior: Behavior) => void mutate(behavior, draft[behavior].trim(), true);
  const remove = (behavior: Behavior, rule: string) => void mutate(behavior, rule, false);

  return (
    <div aria-busy={pending}>
      {notice && <p role="status">{notice}</p>}
      <p style={{ color: "var(--text-muted)", fontSize: "var(--fs-base)", marginTop: 0 }}>
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
            <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-base)", paddingLeft: 16 }}>
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
                  <Button
                    type="button"
                    disabled={pending}
                    onClick={() => remove(key, rule)}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      color: "var(--text-muted)", fontSize: 16,
                    }}
                    aria-label={text("Remove", "删除")}
                  >×</Button>
                </li>
              ))}
            </ul>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <Input
              disabled={pending}
              aria-label={`${text(en, zh)} ${text("rule", "规则")}`}
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
            <Button
              type="button"
              disabled={pending || !draft[key].trim()}
              onClick={() => add(key)}
              style={{
                padding: "6px 14px", borderRadius: 8,
                border: "1px solid var(--border)", background: "var(--bg-tertiary)",
                color: "var(--text-primary)", cursor: "pointer",
              }}
            >{text("Add", "添加")}</Button>
          </div>
        </div>
      ))}
    </div>
  );
}
