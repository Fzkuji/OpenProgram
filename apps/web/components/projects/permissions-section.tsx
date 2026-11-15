"use client";

/**
 * 权限规则管理（按项目）。列出某项目的 allow / deny / ask 规则、手动加、
 * 逐条删。规则跟项目走（<project>/.openprogram/settings.json）。
 * 规则语法见 openprogram/permissions/rule.py（ToolName 或 ToolName(pattern)）。
 * 见 permission-model.md §2.2 / §4.6。
 */
import { useEffect, useState, useCallback, useRef } from "react";

import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { XIcon } from "lucide-react";
import { ManageIconButton } from "@/components/ui/manage-page";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-ui";
import { cn } from "@/lib/utils";
import { computerUseRuleApp } from "@/lib/computer-use/computer-use-rule";

type Behavior = "deny" | "ask" | "allow";
type Rules = Record<Behavior, string[]>;

const EMPTY: Rules = { deny: [], ask: [], allow: [] };

const BEHAVIORS: { key: Behavior; en: string; zh: string; dot: string }[] = [
  { key: "deny", en: "Deny", zh: "拒绝", dot: "bg-[var(--permission-deny)]" },
  { key: "ask", en: "Ask", zh: "询问", dot: "bg-[var(--permission-ask)]" },
  { key: "allow", en: "Allow", zh: "允许", dot: "bg-[var(--permission-allow)]" },
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
  // Apps allowed for computer use have their own section with icons and names.
  const shown = (behavior: Behavior) => behavior === "allow"
    ? rules.allow.filter((rule) => computerUseRuleApp(rule) === null) : rules[behavior];

  return (
    <div aria-busy={pending} className="flex flex-col gap-3">
      {notice && <p role="status" className="text-fs-base text-text-muted">{notice}</p>}
      <SettingsSection
        title={text("Permission Rules", "权限规则")}
        description={text(
          "Rules travel with the project. Syntax: ToolName or ToolName(pattern), e.g. bash(git:*).",
          "规则跟随项目保存。语法：工具名 或 工具名(模式)，如 bash(git:*)。",
        )}
      >
        {BEHAVIORS.map(({ key, en, zh, dot }) => (
          <SettingsRow
            key={key}
            stacked
            label={<span className="flex items-center gap-2"><span className={cn("size-2 rounded-full", dot)} aria-hidden="true" />{text(en, zh)}</span>}
          >
            {shown(key).length === 0 ? (
              <p className="text-fs-base text-text-muted">{text("No rules.", "暂无规则。")}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {shown(key).map((rule) => (
                  <li key={rule} className="flex min-w-0 items-center justify-between gap-2 rounded-md border px-3 py-1">
                    <code className="min-w-0 truncate font-mono text-fs-base">{rule}</code>
                    <ManageIconButton
                      label={text(`Remove ${rule}`, `删除 ${rule}`)}
                      tooltip={text("Remove", "删除")}
                      disabled={pending}
                      danger
                      onClick={() => remove(key, rule)}
                    >
                      <XIcon />
                    </ManageIconButton>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <Input
                disabled={pending}
                aria-label={`${text(en, zh)} ${text("rule", "规则")}`}
                value={draft[key]}
                onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") add(key); }}
                placeholder={text("e.g. bash(git:*)", "如 bash(git:*)")}
              />
              <Button variant="elevated" disabled={pending || !draft[key].trim()} onClick={() => add(key)}>
                {text("Add", "添加")}
              </Button>
            </div>
          </SettingsRow>
        ))}
      </SettingsSection>
    </div>
  );
}
