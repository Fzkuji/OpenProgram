"use client";

/**
 * The apps this project lets the agent operate without asking: the
 * project's ``computer_use(<app>)`` allow rules, one row per app with its
 * own icon and name. Removing a row removes the rule, so the next use of
 * that app asks again. "Allow this conversation" grants end with their
 * conversation and are not listed here.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { SolarIcon } from "@/components/solar-icons";
import { useComputerUseApps } from "@/lib/net/computer-use-apps";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";

import { computerUseRuleApp } from "@/lib/computer-use/computer-use-rule";
import { XIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ManageIconButton } from "@/components/ui/manage-page";
import { SettingsSection } from "@/components/settings/settings-ui";

type Reply = { project_id?: string; status?: string; error?: string; allow?: string[] };

export function ComputerUseAppsSection({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const [rules, setRules] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const busy = useRef(false);

  const request = useCallback((action: string, payload: Record<string, unknown> = {}) =>
    wsRequest<Reply>(action, { project_id: projectId, ...payload }, "permission_rules",
      (d) => d.project_id === projectId, 10000, { requestId: true }), [projectId]);

  useEffect(() => {
    let live = true;
    setRules([]);
    setNotice("");
    const keep = (allow?: string[]) => setRules((allow ?? []).filter((rule) => computerUseRuleApp(rule) !== null));
    void request("list_permission_rules").then((d) => {
      if (live && d && d.status !== "error") keep(d.allow);
    });
    // Every rules reply is broadcast, so a rule added from an approval card shows up here at once.
    function onRules(e: WindowEventMap["op:permission-rules"]) {
      const d = e.detail as Reply;
      if (d?.project_id === projectId && d.status !== "error") keep(d.allow);
    }
    window.addEventListener("op:permission-rules", onRules);
    return () => { live = false; window.removeEventListener("op:permission-rules", onRules); };
  }, [projectId, request]);

  const apps = useComputerUseApps(rules.map((rule) => computerUseRuleApp(rule) ?? ""));

  async function remove(rule: string) {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setNotice("");
    try {
      const d = await request("remove_permission_rule", { behavior: "allow", rule });
      if (!d || d.status === "error") {
        setNotice(d?.error || text("Removal not confirmed; retry after reconnecting.", "尚未确认移除，恢复连接后可重试。"));
        return;
      }
      setRules((d.allow ?? []).filter((item) => computerUseRuleApp(item) !== null));
    } finally { busy.current = false; setPending(false); }
  }

  return (
    <div aria-busy={pending} className="flex flex-col gap-3">
      {notice && <p role="status" className="text-fs-base text-text-muted">{notice}</p>}
      <SettingsSection
        title={text("Computer use", "操控电脑")}
        description={text("The agent can operate these apps in the background without asking. The first use of any other app still asks you.",
          "下面的应用可以直接在后台操作，不再询问。第一次操作其他应用时仍会先问你。")}
      >
        {rules.length === 0 ? (
          <div className="flex items-center gap-3 py-4 text-fs-base text-text-muted">
            <AppGlyph />
            <span>{text("No apps allowed yet. The first time the agent operates an app, it asks for your approval in the conversation.",
              "还没有允许的应用。智能体第一次操作某个应用时，会先在对话里请你批准。")}</span>
          </div>
        ) : rules.map((rule) => {
          const query = computerUseRuleApp(rule) ?? rule;
          const app = apps[query];
          const name = app?.name || query;
          return (
            <div key={rule} className="flex min-w-0 items-center gap-3 py-4">
              {app?.icon ? <img className="size-8 shrink-0" src={app.icon} alt="" aria-hidden="true" /> : <AppGlyph />}
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-fs-base font-medium text-text-primary">{name}</span>
                <span className="truncate text-fs-base text-text-muted">{app?.bundle_id || query}</span>
              </div>
              <Badge variant="outline">{text("Always allowed in this project", "本项目始终允许")}</Badge>
              <ManageIconButton
                label={text(`Remove ${name}`, `移除${name}`)}
                tooltip={text("Remove", "移除")}
                disabled={pending}
                danger
                onClick={() => void remove(rule)}
              >
                <XIcon />
              </ManageIconButton>
            </div>
          );
        })}
      </SettingsSection>
    </div>
  );
}

function AppGlyph() {
  return (
    <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-text-muted" aria-hidden="true">
      <SolarIcon name="monitor" size={20} />
    </span>
  );
}
