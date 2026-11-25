"use client";

// Settings → Third-Party Harnesses → the model Codex and Claude Code use
// outside a chat (GET/PUT /api/harnesses/models, backed by
// openprogram/models/gateway). "Through OpenProgram" sends the harness's
// model calls to the Worker's gateway; "Also in the Terminal" writes that
// into the harness's own config and restores it when turned off.

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SettingsRow } from "../settings-ui";
import usage from "../token-usage/usage.module.css";
import { ProviderIcon } from "../providers/icons/provider-icon";
import local from "./harnesses.module.css";
import { Badge } from "@/components/ui/badge";
import { SimpleSelect, type SimpleSelectGroup, type SimpleSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { api } from "@/lib/net/api";
import { jsonFetch } from "@/lib/net/fetch-client";
import { PICKER_QUERY_KEY } from "@/lib/models/provider-state";
import { useTranslation } from "@/lib/i18n";

type Tier = "opus" | "sonnet" | "haiku";
type HarnessRow = {
  id: "codex" | "claude_code";
  label: string;
  model: string;
  terminal: boolean;
  tiers?: Record<Tier, string>;
  config: { path: string; applied: boolean; drift: string[]; error: string | null };
};

const QUERY_KEY = ["harness-models"] as const;
const ICON: Record<string, string> = { claude_code: "anthropic", codex: "openai" };
const TIERS: { id: Tier; label: string }[] = [
  { id: "opus", label: "Opus" },
  { id: "sonnet", label: "Sonnet" },
  { id: "haiku", label: "Haiku" },
];

export function HarnessModels() {
  const { text } = useTranslation();
  const client = useQueryClient();
  const { data } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => jsonFetch<{ harnesses: HarnessRow[] }>("/api/harnesses/models").then((d) => d.harnesses),
  });
  const { data: picker } = useQuery({ queryKey: PICKER_QUERY_KEY, queryFn: api.listPickerModels });
  const [failed, setFailed] = useState<Record<string, string>>({});

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
    } catch (e) {
      setFailed((f) => ({ ...f, [row.id]: e instanceof Error ? e.message : String(e) }));
      void client.invalidateQueries({ queryKey: QUERY_KEY });
    }
  }

  if (!data) return null;
  return (
    <>
      <h3 className={usage.sectionTitle}>{text("Models", "模型")}</h3>
      <div className={local.agents}>
        {data.map((row) => {
          const own: SimpleSelectOption = {
            value: "",
            label: text(`${row.label}'s Own Sign-In`, `${row.label} 自己的登录`),
          };
          const known = groups.some((g) => g.options.some((o) => o.value === row.model));
          const options: (SimpleSelectOption | SimpleSelectGroup)[] = [own, ...groups];
          if (row.model && !known) options.push({ value: row.model, label: row.model });
          const sameModel: SimpleSelectOption = { value: "", label: text("Same Model", "同一模型") };
          const drift = row.config.applied && (row.config.drift.length > 0 || !!row.config.error);
          return (
            <div key={row.id} className={usage.card}>
              <div className={local.agentHead}>
                <ProviderIcon id={ICON[row.id] ?? row.id} size={20} />
                <span className={local.agentName}>{row.label}</span>
                {failed[row.id] ? (
                  <HoverTip label={failed[row.id]}>
                    <Badge variant="destructive" className={local.agentMeta} tabIndex={0}>{text("Not Saved", "未保存")}</Badge>
                  </HoverTip>
                ) : drift ? (
                  <HoverTip label={row.config.error ?? `${row.config.path}: ${row.config.drift.join(", ")}`}>
                    <Badge variant="warning" className={local.agentMeta} tabIndex={0}>{text("Config Changed Elsewhere", "配置已在别处修改")}</Badge>
                  </HoverTip>
                ) : null}
              </div>
              <div className={local.modelRows}>
                <SettingsRow label={text("Models", "模型")}>
                  <SimpleSelect
                    aria-label={text(`${row.label} models`, `${row.label} 模型`)}
                    value={row.model}
                    onValueChange={(model) => void save(row, { model })}
                    options={options}
                  />
                </SettingsRow>
                {row.id === "claude_code" && row.model && TIERS.map((tier) => (
                  <SettingsRow key={tier.id} label={tier.label}>
                    <SimpleSelect
                      aria-label={tier.label}
                      value={row.tiers?.[tier.id] ?? ""}
                      onValueChange={(value) => void save(row, { tiers: { ...(row.tiers ?? { opus: "", sonnet: "", haiku: "" }), [tier.id]: value } })}
                      options={[sameModel, ...groups]}
                    />
                  </SettingsRow>
                ))}
                <SettingsRow label={text("Also in the Terminal", "也用于终端")}>
                  <Switch
                    checked={row.terminal}
                    disabled={!row.model}
                    aria-label={text("Also in the Terminal", "也用于终端")}
                    onCheckedChange={(terminal) => void save(row, { terminal })}
                  />
                </SettingsRow>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
