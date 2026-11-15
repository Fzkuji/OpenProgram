"use client";

import { useMemo, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation } from "@/lib/i18n";
import type { DetectedEnvKey, DetectedPayload } from "@/lib/settings/provider-status.generated";

import { ProviderIcon } from "../icons/provider-icon";
import type { Provider } from "../types";
import styles from "../status/status.module.css";
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
    <div className={styles.envRow} data-env={row.env_var}>
      <ProviderIcon id={icon} size={20} />
      <span>{label}</span>
      <span className={styles.mono}>{row.env_var}</span>
      <span className={`${styles.mono} ${styles.muted}`}>{row.masked}</span>
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
  detected, detectedFailed = false, onRetry, catalogue, onPick, onUseEnv, onCustom, autoFocus = false, usingEnv = null,
}: {
  detected: DetectedPayload | null;
  /** The popular providers could not be read; Retry reads them again. */
  detectedFailed?: boolean;
  onRetry?: () => void;
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
            <div className={`${styles.xs} ${styles.muted}`}>{text("No provider matches", "没有匹配的服务商")}</div>
          ) : results.map((p) => (
            <button key={p.id} type="button" role="listitem" className={styles.resultRow} onClick={() => onPick(choiceOf(p, detected))}>
              <ProviderIcon id={p.id} size={20} />
              <span>{p.label}</span>
              <span className="flex-1" />
              {p.configured ? <span className={`${styles.xs} ${styles.muted}`}>{text("Connected", "已接入")}</span> : null}
            </button>
          ))}
          {all && !query.trim() ? (
            <Button variant="ghost" size="sm" className="self-start" onClick={() => setAll(false)}>{text("Back to popular", "返回常用")}</Button>
          ) : null}
        </div>
      ) : (
        <>
          {env.length > 0 ? (
            <>
              <div className={styles.group}>{text("In your environment", "环境变量里已有")}</div>
              {env.map((row) => <EnvKeyRow key={row.env_var} row={row} usingEnv={usingEnv} onUse={onUseEnv} />)}
            </>
          ) : null}
          {detected === null ? (
            detectedFailed ? (
              <div className="flex items-center gap-[8px]" role="alert">
                <span className={`${styles.xs} ${styles.muted}`}>{text("Couldn't read the popular providers", "无法读取常用服务商")}</span>
                {onRetry ? <Button variant="ghost" size="xs" onClick={onRetry}>{text("Retry", "重试")}</Button> : null}
              </div>
            ) : (
              <div className="flex justify-center py-[16px]" role="status" aria-label={text("Loading providers", "正在读取服务商")}><Spinner /></div>
            )
          ) : null}
          {(detected?.popular.length ?? 0) > 0 ? (
            <>
              <div className={styles.group}>{text("Popular", "常用")}</div>
              <div className="grid grid-cols-4 gap-[6px]">
                {detected!.popular.map((c) => (
                  <button key={c.id} type="button" className={styles.tile} data-provider={c.id} onClick={() => onPick({ ...c, auth: c.id === "anthropic" ? "api_key" : c.auth })}>
                    <ProviderIcon id={c.id} size={40} plate="brand" />
                    <span>{c.label}</span>
                    {c.connected ? <span className={styles.tileConnected}>{text("Connected", "已接入")}</span> : null}
                  </button>
                ))}
              </div>
            </>
          ) : null}
          <div className={styles.group}>{text("On this machine", "本机")}</div>
          <div className="grid grid-cols-4 gap-[6px]">
            {local.map((row) => {
              const p = byId.get(row.id);
              return (
                <button key={row.id} type="button" className={styles.tile} data-provider={row.id}
                  onClick={() => onPick({ id: row.id, label: row.label, auth: "local", console_url: null, env_var: null, variants: [] })}>
                  <ProviderIcon id={row.id} size={40} plate="brand" />
                  <span>{p?.label ?? row.label}</span>
                  {row.enabled ? <span className={styles.tileConnected}>{text("Connected", "已接入")}</span> : null}
                </button>
              );
            })}
            <button type="button" className={styles.tile} onClick={onCustom} data-provider="custom">
              <span className={styles.plus}><Plus size={16} aria-hidden="true" /></span>
              <span>{text("Custom", "自定义")}</span>
            </button>
          </div>
          <Button variant="ghost" size="sm" className="self-start" onClick={() => setAll(true)}>
            {count ? text(`All providers (${count})`, `全部服务商（${count}）`) : text("All providers", "全部服务商")}
          </Button>
        </>
      )}
    </div>
  );
}
