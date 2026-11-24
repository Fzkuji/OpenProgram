"use client";

import { useMemo, useState } from "react";
import { Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FieldDescription, FieldTitle } from "@/components/ui/field";
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation } from "@/lib/i18n";
import type { DetectedEnvKey, DetectedPayload } from "@/lib/settings/provider-status.generated";

import { ProviderIcon } from "../icons/provider-icon";
import type { Provider } from "../types";
import { choiceOf, type Choice } from "./data";

/** A key found in the Worker's environment: its variable name and masked
 *  tail, and Use, which has the Worker read and store it (the page never
 *  sees the value). Step 1 lists every one; step 2 offers its provider's. */
export function EnvKeyRow({
  row, usingEnv, onUse, label = row.label, icon = row.provider,
}: {
  row: DetectedEnvKey;
  usingEnv: string | null;
  onUse: (row: DetectedEnvKey) => void;
  label?: string;
  icon?: string;
}) {
  const { text } = useTranslation();
  return (
    <div className="flex min-h-(--ui-button-h) min-w-0 items-center gap-2.5 text-fs-sm" data-env={row.env_var}>
      <ProviderIcon id={icon} size={20} />
      <span className="truncate">{label}</span>
      <span className="truncate font-mono text-fs-xs text-text-secondary">{row.env_var}</span>
      <span className="truncate font-mono text-fs-xs text-text-muted">{row.masked}</span>
      <span className="flex-1" />
      <Button size="xs" onClick={() => onUse(row)} disabled={usingEnv !== null}>
        {usingEnv === row.env_var ? text("Checking…", "检查中…") : text("Use", "使用")}
      </Button>
    </div>
  );
}

/** Step 1 of Add provider, and the whole page when nothing is connected
 *  (design §4, owner's change 3): keys already in the environment first,
 *  each with Use; the popular providers as one-click tiles; local servers
 *  that answer; Custom; and a search across the whole catalogue. */
export function PickProvider({
  detected, catalogue, onPick, onUseEnv, onCustom, autoFocus = false, usingEnv = null,
}: {
  /** Null until read (read as Settings opens; nothing shows in its place). */
  detected: DetectedPayload | null;
  catalogue: Provider[];
  onPick: (choice: Choice) => void;
  onUseEnv: (row: DetectedEnvKey) => void;
  onCustom: () => void;
  autoFocus?: boolean;
  /** The variable whose Use is being stored. */
  usingEnv?: string | null;
}) {
  const { text } = useTranslation();
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const count = detected?.catalogue_count || catalogue.length;
  const env = (detected?.env ?? []).filter((row) => !row.connected);
  const local = (detected?.local ?? []).filter((row) => row.running);
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all ? [...catalogue].sort((a, b) => a.label.localeCompare(b.label)) : [];
    return catalogue
      .filter((p) => p.label.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
      .sort((a, b) => Number(b.label.toLowerCase().startsWith(q)) - Number(a.label.toLowerCase().startsWith(q)) || a.label.localeCompare(b.label));
  }, [all, catalogue, query]);
  const byId = useMemo(() => new Map(catalogue.map((p) => [p.id, p] as const)), [catalogue]);

  return (
    <div className="flex flex-col gap-[12px] min-w-0">
      <SearchInput
        value={query}
        onChange={setQuery}
        autoFocus={autoFocus}
        aria-label={text("Search providers", "搜索服务商")}
        placeholder={count ? text(`Search ${count} providers`, `搜索 ${count} 个服务商`) : text("Search providers", "搜索服务商")}
      />
      {query.trim() || all ? (
        <div className="flex flex-col gap-[1px] max-h-[280px] overflow-y-auto" role="list" aria-label={text("All providers", "全部服务商")}>
          {results.length === 0 ? (
            catalogue.length ? <FieldDescription>{text("No provider matches", "没有匹配的服务商")}</FieldDescription> : null
          ) : results.map((p) => (
            <Button key={p.id} variant="ghost" role="listitem" className="justify-start gap-2.5 font-normal text-text-primary" onClick={() => onPick(choiceOf(p, detected))}>
              <ProviderIcon id={p.id} size={20} />
              <span className="truncate">{p.label}</span>
              <span className="flex-1" />
              {p.configured ? <Badge variant="secondary">{text("Connected", "已接入")}</Badge> : null}
            </Button>
          ))}
          {all && !query.trim() ? (
            <Button variant="ghost" size="sm" className="self-start" onClick={() => setAll(false)}>{text("Back to popular", "返回常用")}</Button>
          ) : null}
        </div>
      ) : (
        <>
          {env.length > 0 ? (
            <>
              <FieldTitle>{text("In your environment", "环境变量里已有")}</FieldTitle>
              {env.map((row) => <EnvKeyRow key={row.env_var} row={row} usingEnv={usingEnv} onUse={onUseEnv} />)}
            </>
          ) : null}
          {(detected?.popular.length ?? 0) > 0 ? (
            <>
              <FieldTitle>{text("Popular", "常用")}</FieldTitle>
              <div className="grid grid-cols-4 gap-[6px]">
                {detected!.popular.map((c) => (
                  <Button key={c.id} variant="ghost" className="h-auto min-w-0 flex-col gap-1.5 px-1 pt-3 pb-2.5 text-fs-xs font-normal text-text-primary" data-provider={c.id} onClick={() => onPick({ ...c, auth: c.id === "anthropic" ? "api_key" : c.auth })}>
                    <ProviderIcon id={c.id} size={40} plate="brand" />
                    <span className="max-w-full truncate">{c.label}</span>
                    {c.connected ? <span className="text-fs-2xs text-(--success-soft)">{text("Connected", "已接入")}</span> : null}
                  </Button>
                ))}
              </div>
            </>
          ) : null}
          <FieldTitle>{text("On this machine", "本机")}</FieldTitle>
          <div className="grid grid-cols-4 gap-[6px]">
            {local.map((row) => {
              const p = byId.get(row.id);
              return (
                <Button key={row.id} variant="ghost" className="h-auto min-w-0 flex-col gap-1.5 px-1 pt-3 pb-2.5 text-fs-xs font-normal text-text-primary" data-provider={row.id}
                  onClick={() => onPick({ id: row.id, label: row.label, auth: "local", console_url: null, env_var: null, variants: [] })}>
                  <ProviderIcon id={row.id} size={40} plate="brand" />
                  <span className="max-w-full truncate">{p?.label ?? row.label}</span>
                  {row.enabled ? <span className="text-fs-2xs text-(--success-soft)">{text("Connected", "已接入")}</span> : null}
                </Button>
              );
            })}
            <Button variant="ghost" className="h-auto min-w-0 flex-col gap-1.5 px-1 pt-3 pb-2.5 text-fs-xs font-normal text-text-primary" onClick={onCustom} data-provider="custom">
              <span className="inline-flex size-10 items-center justify-center rounded-lg bg-muted text-text-secondary"><Plus aria-hidden="true" /></span>
              <span>{text("Custom", "自定义")}</span>
            </Button>
          </div>
          <Button variant="ghost" size="sm" className="self-start" onClick={() => setAll(true)}>
            {count ? text(`All providers (${count})`, `全部服务商（${count}）`) : text("All providers", "全部服务商")}
          </Button>
        </>
      )}
    </div>
  );
}
