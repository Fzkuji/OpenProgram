"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { Field, FieldError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { SettingsHeading } from "@/components/settings/settings-ui";
import { useTranslation } from "@/lib/i18n";
import { errorDetailText } from "@/lib/net/error-text";
import { refreshSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import type { ProviderStatus, ProviderUsage } from "@/lib/settings/provider-status.generated";
import { providerModelsUrl, providerUsageUrl } from "@/lib/settings/settings-urls";
import { settingsSend } from "@/lib/settings/settings-request";
import { cn } from "@/lib/utils";

import { BaseUrl } from "./auth/base-url";
import { Connectivity } from "./auth/connectivity";
import { CliInfo, SetupHint } from "./auth/setup-hint";
import { credentialUrl, readCredentials } from "./detail/credential-model";
import { CredentialsSection, type CredentialFix } from "./detail/credentials-section";
import { UsagePanel } from "./detail/usage-panel";
import { ProviderIcon } from "./icons/provider-icon";
import { ModelList } from "./model-list";
import { fmtAgo, providerUpdatedAt } from "./status/model";
import { HealthChip } from "./status/parts";
import styles from "./status/status.module.css";
import type { Model, Provider } from "./types";

/** What reading a provider's models answers. */
interface ModelsReply { models?: Model[] }

/** What asking the provider for its model list answers. */
interface FetchModelsReply { fetched?: number; error?: unknown }

/** One provider's page (design §5): back to the list, its name, health,
 *  when its status was read and Refresh, the on switch; its credentials;
 *  usage; the models table; Remove provider at the foot. */
export function Detail({
  provider,
  status = null,
  fix = null,
  onBack,
  onRefresh,
  onToggle,
  onChanged,
  onDeleted,
}: {
  provider: Provider;
  status?: ProviderStatus | null;
  /** The fix a card sent here (Sign in again, Replace key). */
  fix?: CredentialFix | null;
  onBack?: () => void;
  onRefresh?: () => void | Promise<void>;
  onToggle: (enabled: boolean) => void;
  onChanged: () => void;
  onDeleted?: () => void;
}) {
  const { text } = useTranslation();
  // Kept for the visit: reopening a provider shows its models at once and
  // reads them again behind; a failed read keeps them and retries quietly.
  const modelsUrl = provider.kind === "cli" ? null : providerModelsUrl(provider.id);
  const models = useSettingsData<ModelsReply>(modelsUrl)?.models;
  // The usage panel's own read (30 days by day and model) also gives each
  // model's 30-day spend: one request serves both.
  const usage = useSettingsData<ProviderUsage>(providerUsageUrl(provider.id, 30));
  const spend = useMemo(
    () => Object.fromEntries((usage?.models ?? []).map((m) => [m.model_id, m.list_price_usd])),
    [usage],
  );
  // A subscription's calls are covered by the plan: their list price is not what was paid.
  const included = !!usage?.totals?.included;
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [manualId, setManualId] = useState("");
  const [manualBusy, setManualBusy] = useState(false);
  const [contextWindow, setContextWindow] = useState("128000");
  const [maxTokens, setMaxTokens] = useState("8192");
  const [manualError, setManualError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const reloadModels = useCallback(async () => {
    if (modelsUrl) await refreshSettings(modelsUrl);
  }, [modelsUrl]);

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
      const d = await r.json() as FetchModelsReply;
      if (d.error) { setFetchStatus(text("Failed: ", "失败：") + errorDetailText(d.error, String(r.status))); return; }
      const fetched = Number(d.fetched ?? 0);
      setFetchStatus(text(`Fetched ${fetched}`, `已获取 ${fetched} 个`));
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
      const result = await response.json() as { error?: unknown };
      if (!response.ok || result.error) { setManualError(errorDetailText(result.error, text("Could not save the model.", "无法保存模型。"))); return; }
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
        ? text("The provider and its models are deleted.", "这个服务商和它的模型会被删除。")
        : text("Its saved keys and sign-ins are deleted from this computer and it is switched off.", "这台电脑上保存的密钥和登录信息会被删除，并停用它。"),
      confirmLabel: text("Remove", "移除"),
      destructive: true,
    }))) return;
    const failure = text("Could not remove this provider.", "无法移除这个服务商。");
    const id = encodeURIComponent(provider.id);
    if (provider.custom) {
      if ((await settingsSend(`/api/providers/custom/${id}`, { method: "DELETE", failure })) === null) return;
    } else {
      try {
        const list = await readCredentials(provider.id);
        for (const c of list.credentials.filter((row) => !row.env_var)) {
          if ((await settingsSend(credentialUrl(provider.id, c.id), { method: "DELETE", failure })) === null) return;
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
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild><button type="button" onClick={onBack}>{text("Model services", "模型服务")}</button></BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem><BreadcrumbPage>{provider.label}</BreadcrumbPage></BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
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
        <Switch checked={provider.enabled} onCheckedChange={onToggle} aria-label={text("Enable this provider", "启用这个服务商")} />
      </div>

      {/* How to connect, until the provider has a credential: the list says the rest. */}
      {provider.setup_hint && (provider.local || !provider.configured) ? (
        <SetupHint key={`${provider.id}:setup`} hint={provider.setup_hint} configured={!!provider.configured} local={!!provider.local} />
      ) : null}

      {hasKeys ? (
        <CredentialsSection key={`${provider.id}:credentials`} provider={provider} consoleUrl={status?.console_url ?? null} fix={fix} onChanged={changed} />
      ) : null}

      {provider.api_key_env || provider.local ? (
        <section className="flex flex-col gap-3" aria-label={text("Endpoint", "接口地址")}>
          <SettingsHeading title={text("Endpoint", "接口地址")} />
          <BaseUrl key={`${provider.id}:base-url`} provider={provider} onChanged={changed} />
        </section>
      ) : null}

      <UsagePanel key={`${provider.id}:usage`} providerId={provider.id} status={status} />

      {provider.local ? <Connectivity key={`${provider.id}:check`} providerId={provider.id} local /> : null}

      {(provider.custom || provider.local) && provider.kind !== "cli" ? (
        <section className="flex flex-col gap-3" aria-label={text("Add model by id", "手动添加模型")}>
          <SettingsHeading title={text("Add model by id", "手动添加模型")} />
          <Field orientation="horizontal" data-invalid={manualError ? true : undefined}>
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
          </Field>
          {manualError ? <FieldError role="alert">{manualError}</FieldError> : null}
        </section>
      ) : null}

      {provider.kind === "cli" ? (
        <CliInfo provider={provider} />
      ) : !models ? null : models.length > 0 ? (
        <ModelList key={`${provider.id}:models`} provider={provider} models={models} spend={spend} included={included || !!status?.spend.included} defaultModel={status?.default_model ?? null} onReload={changed} />
      ) : (
        <section className="flex flex-col gap-3" aria-label={text("Models", "模型")}>
          <SettingsHeading title={text("Models", "模型")} />
          <ManageEmptyState compact title={text("No models yet", "还没有模型")} description={fetchStatus ?? undefined}
            action={provider.supports_fetch ? <Button size="sm" onClick={() => void fetchModels()}>{text("Fetch models", "获取模型")}</Button> : undefined} />
        </section>
      )}

      <div className="flex justify-end">
        <Button variant="destructive" size="sm" onClick={() => void removeProvider()}>
          {text("Remove provider", "移除服务商")}
        </Button>
      </div>
    </div>
  );
}
