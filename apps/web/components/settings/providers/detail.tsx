"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { ProviderStatus, ProviderUsage } from "@/lib/settings/provider-status.generated";
import { settingsSend } from "@/lib/settings/settings-request";
import { cn } from "@/lib/utils";

import { Connectivity } from "./auth/connectivity";
import { CliInfo, SetupHint } from "./auth/setup-hint";
import { KeysPanel } from "./detail/keys-panel";
import { UsagePanel } from "./detail/usage-panel";
import { ProviderIcon } from "./icons/provider-icon";
import { ModelList } from "./model-list";
import { fmtAgo, providerUpdatedAt } from "./status/model";
import { HealthChip } from "./status/parts";
import styles from "./status/status.module.css";
import type { Model, Provider } from "./types";

/** One provider's page (design §5): back to the list, its name, health,
 *  when its status was read and Refresh, the on switch; usage beside the
 *  keys; the models table; Remove provider at the foot. */
export function Detail({
  provider,
  status = null,
  onBack,
  onRefresh,
  onToggle,
  onChanged,
  onDeleted,
}: {
  provider: Provider;
  status?: ProviderStatus | null;
  onBack?: () => void;
  onRefresh?: () => void | Promise<void>;
  onToggle: (enabled: boolean) => void;
  onChanged: () => void;
  onDeleted?: () => void;
}) {
  const { text } = useTranslation();
  const [models, setModels] = useState<Model[]>([]);
  const [modelsState, setModelsState] = useState<"loading" | "ready" | "failed">("loading");
  const [spend, setSpend] = useState<Record<string, number>>({});
  // A subscription's calls are covered by the plan: their list price is not what was paid.
  const [included, setIncluded] = useState(false);
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [manualId, setManualId] = useState("");
  const [manualBusy, setManualBusy] = useState(false);
  const [contextWindow, setContextWindow] = useState("128000");
  const [maxTokens, setMaxTokens] = useState("8192");
  const [manualError, setManualError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const reloadModels = useCallback(async () => {
    if (provider.kind === "cli") { setModels([]); setModelsState("ready"); return; }
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(provider.id)}/models`);
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      setModels(d.models || []);
      setModelsState("ready");
    } catch {
      setModelsState("failed");
    }
  }, [provider.id, provider.kind]);

  useEffect(() => { setModels([]); setModelsState("loading"); void reloadModels(); }, [reloadModels]);

  useEffect(() => {
    let alive = true;
    fetch(`/api/providers/${encodeURIComponent(provider.id)}/usage?days=30&group=model`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: ProviderUsage | null) => {
        if (!alive || !d) return;
        setSpend(Object.fromEntries((d.models ?? []).map((m) => [m.model_id, m.list_price_usd])));
        setIncluded(!!d.totals?.included);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [provider.id]);

  useEffect(() => {
    const onModels = (event: Event) => {
      const changed = (event as CustomEvent<{ provider?: string }>).detail?.provider;
      if (!changed || changed === provider.id) void reloadModels();
    };
    window.addEventListener("op:provider-models-changed", onModels);
    return () => window.removeEventListener("op:provider-models-changed", onModels);
  }, [provider.id, reloadModels]);

  const changed = useCallback(() => { void reloadModels(); onChanged(); }, [reloadModels, onChanged]);

  async function fetchModels() {
    setFetchStatus(text("Fetching…", "获取中…"));
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(provider.id)}/fetch-models`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const d = await r.json();
      if (d.error) { setFetchStatus(text("Failed: ", "失败：") + d.error); return; }
      setFetchStatus(text(`Fetched ${d.fetched}`, `已获取 ${d.fetched} 个`));
      changed();
    } catch (e) {
      setFetchStatus(text("Failed: ", "失败：") + (e as Error).message);
    }
  }

  async function addManualModel() {
    const mid = manualId.trim();
    if (!mid) return;
    setManualBusy(true);
    try {
      const response = await fetch(`/api/providers/${encodeURIComponent(provider.id)}/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: mid, ...(provider.local ? { context_window: Number(contextWindow), max_tokens: Number(maxTokens) } : {}) }),
      });
      const result = await response.json();
      if (!response.ok || result.error) { setManualError(result.error || text("Could not save the model.", "无法保存模型。")); return; }
      setManualError(null);
      setManualId("");
      changed();
    } catch {
      setManualError(text("Could not save the model. Check the connection and try again.", "无法保存模型，请检查连接后重试。"));
    } finally {
      setManualBusy(false);
    }
  }

  /** A custom provider is deleted; any other provider loses its saved keys
   *  and sign-ins and is switched off, so it leaves the connected list. */
  async function removeProvider() {
    if (!(await confirmAction({
      title: text(`Remove ${provider.label}?`, `移除 ${provider.label}？`),
      description: provider.custom
        ? text("The provider and its models are deleted.", "这个 Provider 和它的模型会被删除。")
        : text("Its saved keys and sign-ins are deleted from this computer and it is switched off.", "这台电脑上保存的密钥和登录信息会被删除，并停用它。"),
      confirmLabel: text("Remove", "移除"),
      destructive: true,
    }))) return;
    const failure = text("Could not remove this provider.", "无法移除这个 Provider。");
    const id = encodeURIComponent(provider.id);
    if (provider.custom) {
      if ((await settingsSend(`/api/providers/custom/${id}`, { method: "DELETE", failure })) === null) return;
    } else {
      try {
        const r = await fetch(`/api/providers/${id}/accounts`);
        const d = r.ok ? await r.json() : { accounts: [] };
        for (const a of (d.accounts ?? []) as { id: string }[]) {
          if ((await settingsSend(`/api/providers/${id}/accounts/remove`, { body: { id: a.id }, failure })) === null) return;
        }
      } catch { /* nothing saved to remove */ }
      if ((await settingsSend(`/api/providers/${id}/toggle`, { body: { enabled: false }, failure })) === null) return;
    }
    onDeleted?.();
  }

  async function refresh() {
    if (!onRefresh) return;
    setRefreshing(true);
    try { await onRefresh(); } finally { setRefreshing(false); }
  }

  const now = Date.now() / 1000;
  const updated = status ? providerUpdatedAt(status) : null;
  const hasKeys = !!provider.api_key_env || (provider.login_methods?.length ?? 0) > 0 || !!provider.local;

  return (
    <div className={styles.detail}>
      {onBack ? (
        <button type="button" className={styles.crumb} onClick={onBack}>
          <ArrowLeft size={14} aria-hidden="true" />{text("LLM Providers", "LLM Providers")}
        </button>
      ) : null}
      <div className="flex flex-wrap items-center gap-[12px]">
        <ProviderIcon id={provider.id} size={32} />
        <h2 className={styles.detailTitle}>{provider.label}</h2>
        {status ? <HealthChip health={status.health} /> : null}
        <span className="flex-1" />
        {updated ? (
          <span className={cn(styles.xs, styles.muted)}>{text("Updated ", "更新于 ")}{fmtAgo(updated, now, text)}</span>
        ) : null}
        {onRefresh ? (
          <HoverTip label={text("Read the sign-in, quota and health again", "重新读取登录、额度和状态")}>
            <Button variant="ghost" size="icon-sm" aria-label={text("Refresh", "刷新")} disabled={refreshing} onClick={() => void refresh()}>
              <RefreshCw className={cn(refreshing && styles.spinning)} />
            </Button>
          </HoverTip>
        ) : null}
        <Switch checked={provider.enabled} onCheckedChange={onToggle} aria-label={text("Enable this provider", "启用这个 Provider")} />
      </div>

      {provider.setup_hint ? <SetupHint key={`${provider.id}:setup`} hint={provider.setup_hint} configured={!!provider.configured} /> : null}

      <div className="grid grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] items-start gap-[16px] @max-[760px]:grid-cols-1">
        <UsagePanel key={`${provider.id}:usage`} providerId={provider.id} status={status} />
        {hasKeys ? <KeysPanel key={`${provider.id}:keys`} provider={provider} status={status} onChanged={changed} /> : null}
      </div>

      {provider.local ? <Connectivity key={`${provider.id}:check`} providerId={provider.id} local /> : null}

      {(provider.custom || provider.local) && provider.kind !== "cli" ? (
        <section className={styles.panel} aria-label={text("Add model by id", "手动添加模型")}>
          <div className={styles.panelHead}>{text("Add model by id", "手动添加模型")}</div>
          <div className="flex gap-[10px] items-center">
            <Input placeholder={text("model id (e.g. my-model)", "模型 id（如 my-model）")} aria-label={text("Model id", "模型 id")} value={manualId}
              onChange={(e) => setManualId(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addManualModel(); }} />
            {provider.local ? (
              <>
                <HoverTip label={text("Context tokens", "上下文 token 数")}>
                  <Input type="number" min="1" className="w-[110px]" aria-label={text("Context tokens", "上下文 token 数")} value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} />
                </HoverTip>
                <HoverTip label={text("Output tokens", "最大输出 token 数")}>
                  <Input type="number" min="1" className="w-[100px]" aria-label={text("Output tokens", "最大输出 token 数")} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
                </HoverTip>
              </>
            ) : null}
            <Button onClick={() => void addManualModel()} disabled={!manualId.trim() || manualBusy}>
              {manualBusy ? text("Adding…", "添加中…") : text("Add", "添加")}
            </Button>
          </div>
          {manualError ? <div className={cn(styles.line, styles.bad)} role="alert">{manualError}</div> : null}
        </section>
      ) : null}

      {provider.kind === "cli" ? (
        <CliInfo provider={provider} />
      ) : modelsState === "loading" ? null : modelsState === "failed" ? (
        <div className="flex items-center justify-between gap-[10px]">
          <span className={cn(styles.xs, styles.muted)}>{text("Could not load the model list.", "无法加载模型列表。")}</span>
          <Button variant="elevated" size="sm" onClick={() => void reloadModels()}>{text("Retry", "重试")}</Button>
        </div>
      ) : models.length > 0 ? (
        <ModelList key={`${provider.id}:models`} provider={provider} models={models} spend={spend} included={included || !!status?.spend.included} defaultModel={status?.default_model ?? null} onReload={changed} />
      ) : (
        <section className={styles.panel} aria-label={text("Models", "模型")}>
          <div className={styles.panelHead}>
            <span>{text("Models", "模型")}</span>
            <span className={cn(styles.xs, styles.muted)}>{text("None yet", "还没有")}</span>
            <span className="flex-1" />
            {fetchStatus ? <span className={cn(styles.xs, styles.muted)} role="status">{fetchStatus}</span> : null}
            {provider.supports_fetch ? <Button size="sm" onClick={() => void fetchModels()}>{text("Fetch models", "获取模型")}</Button> : null}
          </div>
        </section>
      )}

      <div className="flex justify-end">
        <Button variant="ghost" size="sm" className="text-[var(--danger-soft)]" onClick={() => void removeProvider()}>
          {text("Remove provider", "移除 Provider")}
        </Button>
      </div>
    </div>
  );
}
