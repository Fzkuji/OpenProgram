"use client";

// Settings → Third-Party Harnesses → the model each outside agent uses
// (GET/PUT /api/harnesses/models, backed by the agent registry in
// openprogram/models/gateway/agents). "Through OpenProgram" sends the
// agent's model calls to the Worker's gateway. For Codex and Claude Code,
// which OpenProgram also runs in its own chats, "Also in the Terminal"
// writes that into the agent's own config; for any other agent the pick is
// its config. An agent with only its own models (Cursor CLI, Devin) picks
// among those, its model field's choices; one that reaches models of its own
// beside OpenProgram's (DeepSeek Harness) offers them above OpenProgram's. A
// multi field (Kimi Code's subagent pool) is a menu of checks, its value one
// choice per line. Going back to the agent's own
// setup restores what was there. An agent with no model to pick (it lists
// the gateway's models itself) is a switch; an app that takes the gateway
// only through its own import link is handed that link
// (POST /api/harnesses/import) to confirm in the app. What the agent says
// after a change (how to start agy on the gateway, a restart to pick it up)
// sits under its rows until the next change, as magpie shows an agent's
// notice after a pick.

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SettingsRow } from "../settings-ui";
import usage from "../token-usage/usage.module.css";
import { ProviderIcon } from "../providers/icons/provider-icon";
import local from "./harnesses.module.css";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { FieldDescription } from "@/components/ui/field";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { SELECT_TRIGGER, SimpleSelect, type SimpleSelectGroup, type SimpleSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { api } from "@/lib/net/api";
import { jsonFetch } from "@/lib/net/fetch-client";
import { PICKER_QUERY_KEY } from "@/lib/models/provider-state";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { ChevronDownIcon } from "lucide-react";

/** A field with `options` is a choice (Codex's "applies to", Kiro Crew's harness), not a model. */
/** `kept`: the user's own setting (agy's fast tools), shown and set while disconnected too. */
/** `multi`: several of its options at once, one per line. `withCatalog`: the model field's options are
 *  the agent's own models offered beside OpenProgram's. */
type AgentField = {
  key: string;
  label: string;
  quiet: boolean;
  follows: string;
  kept?: boolean;
  multi?: boolean;
  withCatalog?: boolean;
  options?: SimpleSelectOption[];
};
type HarnessRow = {
  id: string;
  label: string;
  icon: string;
  /** OpenProgram also runs it in its own chats: the pick is a setting, the terminal a switch. */
  harness: boolean;
  fields: AgentField[];
  /** Its folder is here but nothing runs it any more. */
  cliGone: boolean;
  /** Its app is running on the config from before the change. */
  restart: boolean;
  /** No model to pick: it lists the gateway's models itself, and is only turned on or off. */
  switch?: boolean;
  /** It takes the gateway only through an import link of its own, confirmed in the app. */
  link?: boolean;
  /** Whether the app has the gateway already; null when that can't be read. */
  added?: boolean | null;
  model: string;
  terminal: boolean;
  tiers?: Record<string, string>;
  /** False for an agent with only its own models: `model` is one of its model field's options. */
  gateway?: boolean;
  /** What to know after a change: how to start it, a restart it needs. */
  notice?: string;
  config: { path: string; applied: boolean; drift: string[]; error: string | null };
};

const QUERY_KEY = ["harness-models"] as const;

export function HarnessModels() {
  const { text } = useTranslation();
  const client = useQueryClient();
  const { data } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => jsonFetch<{ harnesses: HarnessRow[] }>("/api/harnesses/models").then((d) => d.harnesses),
  });
  const { data: picker } = useQuery({ queryKey: PICKER_QUERY_KEY, queryFn: api.listPickerModels });
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<Record<string, string>>({});

  const groups = useMemo<SimpleSelectGroup[]>(() => {
    const byProvider = new Map<string, SimpleSelectGroup>();
    for (const m of picker?.models ?? []) {
      const group = byProvider.get(m.provider) ?? {
        label: text(`Through OpenProgram · ${m.providerLabel ?? m.provider}`, `通过 OpenProgram · ${m.providerLabel ?? m.provider}`),
        options: [],
      };
      group.options.push({ value: `${m.provider}/${m.id}`, label: m.name });
      byProvider.set(m.provider, group);
    }
    return [...byProvider.values()];
  }, [picker, text]);

  async function save(row: HarnessRow, patch: Partial<Pick<HarnessRow, "model" | "terminal" | "tiers">>) {
    try {
      const next = await jsonFetch<HarnessRow>("/api/harnesses/models", {
        method: "PUT",
        body: JSON.stringify({ harness: row.id, ...patch }),
      });
      client.setQueryData<HarnessRow[]>(QUERY_KEY, (rows) => rows?.map((r) => (r.id === row.id ? next : r)));
      setFailed(({ [row.id]: _gone, ...rest }) => rest);
      setNotices((n) => ({ ...n, [row.id]: next.notice ?? "" }));
    } catch (e) {
      setFailed((f) => ({ ...f, [row.id]: e instanceof Error ? e.message : String(e) }));
      void client.invalidateQueries({ queryKey: QUERY_KEY });
    }
  }

  async function importLink(row: HarnessRow) {
    try {
      const next = await jsonFetch<HarnessRow>("/api/harnesses/import", {
        method: "POST",
        body: JSON.stringify({ harness: row.id }),
      });
      client.setQueryData<HarnessRow[]>(QUERY_KEY, (rows) => rows?.map((r) => (r.id === row.id ? next : r)));
      setFailed(({ [row.id]: _gone, ...rest }) => rest);
    } catch (e) {
      setFailed((f) => ({ ...f, [row.id]: e instanceof Error ? e.message : String(e) }));
    }
  }

  async function restart(row: HarnessRow) {
    try {
      await jsonFetch("/api/harnesses/restart", { method: "POST", body: JSON.stringify({ harness: row.id }) });
    } finally {
      void client.invalidateQueries({ queryKey: QUERY_KEY });
    }
  }

  if (!data) return null;
  return (
    <>
      <h3 className={usage.sectionTitle}>{text("Models", "模型")}</h3>
      <div className={local.agents}>
        {data.map((row) => {
          // An agent with only its own models (Cursor CLI, Devin) picks among them, not OpenProgram's.
          const ownModels = row.gateway === false;
          const own: SimpleSelectOption = {
            value: "",
            label: ownModels
              ? text(`${row.label}'s Default`, `${row.label} 默认`)
              : text(`${row.label}'s Own Sign-In`, `${row.label} 自己的登录`),
          };
          const modelField = row.fields.find((f) => f.key === "model");
          const beside = modelField?.withCatalog && modelField.options?.length
            ? [{ label: row.label, options: modelField.options }]
            : [];
          const choices: SimpleSelectGroup[] = ownModels
            ? [{ label: row.label, options: modelField?.options ?? [] }]
            : [...beside, ...groups];
          const known = choices.some((g) => g.options.some((o) => o.value === row.model));
          const options: (SimpleSelectOption | SimpleSelectGroup)[] = [own, ...choices];
          if (row.model && !known) options.push({ value: row.model, label: row.model });
          const sameModel: SimpleSelectOption = { value: "", label: text("Same Model", "同一模型") };
          const drift = row.config.applied && (row.config.drift.length > 0 || !!row.config.error);
          // off the gateway only the agent's own settings (kept fields) are left to pick
          const tiers = row.fields.filter((f) => f.quiet && (row.model || f.kept));
          const tierRows = tiers.map((tier) => tier.multi ? (
            <SettingsRow key={tier.key} label={tier.label}>
              <MultiPick
                field={tier}
                value={row.tiers?.[tier.key] ?? ""}
                empty={text("Default", "默认")}
                label={(value) => {
                  const found = groups.flatMap((g) => g.options)
                    .find((o) => o.value === value.replace(/^openprogram\//, ""))?.label;
                  return typeof found === "string" ? found : value;
                }}
                onChange={(value) => void save(row, { tiers: { ...(row.tiers ?? {}), [tier.key]: value } })}
              />
            </SettingsRow>
          ) : (
            <SettingsRow key={tier.key} label={tier.label}>
              <SimpleSelect
                aria-label={tier.label}
                value={row.tiers?.[tier.key] ?? ""}
                onValueChange={(value) => void save(row, { tiers: { ...(row.tiers ?? {}), [tier.key]: value } })}
                options={tier.options ? [{ value: "", label: text("Default", "默认") }, ...tier.options]
                  : [tier.follows ? sameModel : {
                    value: "",
                    label: text(`${row.label}'s Own Setting`, `${row.label} 自己的设置`),
                  }, ...groups]}
              />
            </SettingsRow>
          ));
          return (
            <div key={row.id} className={usage.card}>
              <div className={local.agentHead}>
                <ProviderIcon id={row.icon || row.id} size={20} />
                <span className={local.agentName}>{row.label}</span>
                {failed[row.id] ? (
                  <HoverTip label={failed[row.id]}>
                    <Badge variant="destructive" className={local.agentMeta} tabIndex={0}>{text("Not Saved", "未保存")}</Badge>
                  </HoverTip>
                ) : drift ? (
                  <HoverTip label={row.config.error ?? `${row.config.path}: ${row.config.drift.join(", ")}`}>
                    <Badge variant="warning" className={local.agentMeta} tabIndex={0}>{text("Config Changed Elsewhere", "配置已在别处修改")}</Badge>
                  </HoverTip>
                ) : row.cliGone ? (
                  <Badge variant="secondary" className={local.agentMeta}>{text("Command Not Found", "找不到命令")}</Badge>
                ) : row.link && row.added ? (
                  <Badge variant="secondary" className={local.agentMeta}>{text("Added", "已添加")}</Badge>
                ) : null}
                {row.restart && (
                  <Button variant="ghost" size="sm" onClick={() => void restart(row)}>
                    {text(`Restart ${row.label}`, `重启 ${row.label}`)}
                  </Button>
                )}
              </div>
              {row.link ? (
                <div className={local.modelRows}>
                  <SettingsRow label={text("Through OpenProgram", "通过 OpenProgram")}>
                    <Button variant="elevated" size="sm" onClick={() => void importLink(row)}>
                      {text(`Add in ${row.label}`, `在 ${row.label} 中添加`)}
                    </Button>
                  </SettingsRow>
                </div>
              ) : row.switch ? (
                <div className={local.modelRows}>
                  <SettingsRow label={text("Through OpenProgram", "通过 OpenProgram")}>
                    <Switch
                      checked={row.terminal}
                      aria-label={text("Through OpenProgram", "通过 OpenProgram")}
                      onCheckedChange={(terminal) => void save(row, { terminal })}
                    />
                  </SettingsRow>
                  {row.terminal && tierRows}
                </div>
              ) : (
              <div className={local.modelRows}>
                <SettingsRow label={text("Models", "模型")}>
                  <SimpleSelect
                    aria-label={text(`${row.label} models`, `${row.label} 模型`)}
                    value={row.model}
                    onValueChange={(model) => void save(row, { model })}
                    options={options}
                  />
                </SettingsRow>
                {tierRows}
                {row.harness && (
                  <SettingsRow label={text("Also in the Terminal", "也用于终端")}>
                    <Switch
                      checked={row.terminal}
                      disabled={!row.model}
                      aria-label={text("Also in the Terminal", "也用于终端")}
                      onCheckedChange={(terminal) => void save(row, { terminal })}
                    />
                  </SettingsRow>
                )}
              </div>
              )}
              {notices[row.id] ? <FieldDescription className="break-words">{notices[row.id]}</FieldDescription> : null}
            </div>
          );
        })}
      </div>
    </>
  );
}

/** A field picked several at once (Kimi Code's subagent pool): a menu of
 *  checks that stays open, its value the picks one per line. */
function MultiPick({ field, value, empty, label, onChange }: {
  field: AgentField;
  value: string;
  empty: string;
  label: (value: string) => string;
  onChange: (value: string) => void;
}) {
  const picked = value.split("\n").filter(Boolean);
  const toggle = (choice: string) =>
    onChange((picked.includes(choice) ? picked.filter((c) => c !== choice) : [...picked, choice]).join("\n"));
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={field.label} className={cn(SELECT_TRIGGER, "w-full max-w-48 min-w-0")} data-size="default">
        <span className="truncate">{picked.length ? picked.map(label).join(", ") : empty}</span>
        <ChevronDownIcon className="pointer-events-none size-4 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className={MENU_PANEL}>
        <div className={GROUP_LABEL}>{field.label}</div>
        {(field.options ?? []).map((option) => (
          <DropdownMenuItem
            key={option.value}
            className={cn(itemCls(false), MENU_ITEM_STATES)}
            // several are picked: the menu stays open
            onSelect={(e) => { e.preventDefault(); toggle(option.value); }}
          >
            <MenuRow label={typeof option.label === "string" ? option.label : option.value} checked={picked.includes(option.value)} />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
