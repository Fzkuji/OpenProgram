"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

import { ProviderIcon } from "../provider-icon";

import { ApiKey } from "./api-key";
import { BaseUrl } from "./base-url";
import { PoolControls } from "./pool-controls";
import { ProviderAccounts } from "./provider-accounts";
import { ProviderLogin } from "./provider-login";
import { Connectivity, type ConnectivityHandle } from "./connectivity";
import { ModelList } from "./model-list";
import { CliInfo, SetupHint } from "./setup-hint";
import styles from "../settings-page.module.css";
import type { Model, Provider } from "./types";
import { useTranslation } from "@/lib/i18n";

/** Right-pane detail view for one selected provider. Header + enable
 *  toggle, then a stack of sections: setup hint (optional), API key
 *  (api kind only), base URL, connectivity, and the model list (or
 *  CLI info for CLI providers). */
export function Detail({
  provider,
  onToggle,
  onChanged,
}: {
  provider: Provider;
  onToggle: (enabled: boolean) => void;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  const subtitle =
    provider.kind === "cli"
      ? text(`CLI runtime - binary: ${provider.cli_binary || "?"}`, `CLI 运行时 - binary：${provider.cli_binary || "?"}`)
      : provider.id === "claude-code"
        // Runs on a Claude subscription via the local backend — it has no
        // user-facing API key, so don't surface the ANTHROPIC_API_KEY env var
        // (which it carries only as an internal detail) as if you must set it.
        ? text("Runs on your Claude subscription — no API key", "用你的 Claude 订阅 — 无需 API key")
        : provider.api_key_env
          ? `API key env: ${provider.api_key_env}`
          : text("Subscription required", "需要订阅");

  const [models, setModels] = useState<Model[]>([]);
  const [modelSearch, setModelSearch] = useState("");
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const connectivityRef = useRef<ConnectivityHandle>(null);

  const reloadModels = useCallback(async () => {
    if (provider.kind === "cli") {
      setModels([]);
      return;
    }
    try {
      const r = await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/models`,
      );
      const d = await r.json();
      setModels(d.models || []);
    } catch {
      setModels([]);
    }
  }, [provider.id, provider.kind]);

  useEffect(() => {
    reloadModels();
  }, [reloadModels]);

  // After a NEW key is saved: auto-run the connectivity check (its
  // inline ✓/✗ result shows in the Connectivity row, exactly as if the
  // user clicked "Check") and, on success, fetch the model list and
  // refresh it in place. No toasts — the result lives in the panel.
  const autoCheckAndFetch = useCallback(async () => {
    const ok = await connectivityRef.current?.run();
    if (!ok) return;
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.id)}/fetch-models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch { /* ignore — list just won't refresh */ }
    reloadModels();
    onChanged();
  }, [provider.id, reloadModels, onChanged]);

  // Pull the model list from the provider's live /v1/models (or
  // OpenAI-style /models). Shared by the empty-state button below — a
  // provider with no registry rows (every models.dev community entry
  // ships zero) otherwise has no way to populate its list, because the
  // ModelList's own "Fetch models" button only renders once models
  // exist. This is that same fetch, surfaced before the first one lands.
  const fetchModels = useCallback(async () => {
    setFetchStatus(text("Fetching…", "获取中…"));
    try {
      const r = await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/fetch-models`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      const d = await r.json();
      if (d.error) {
        setFetchStatus(text("Failed: ", "失败：") + d.error);
        setTimeout(() => setFetchStatus(null), 6_000);
        return;
      }
      setFetchStatus(text(`Fetched ${d.fetched}`, `已获取 ${d.fetched} 个`));
      await reloadModels();
      onChanged();
      setTimeout(() => setFetchStatus(null), 4_000);
    } catch (e) {
      setFetchStatus(text("Failed: ", "失败：") + (e as Error).message);
      setTimeout(() => setFetchStatus(null), 6_000);
    }
  }, [provider.id, reloadModels, onChanged, text]);

  return (
    <>
      <div className={styles.detailHeader}>
        <div className={styles.detailIcon}>
          <ProviderIcon id={provider.id} size={40} />
        </div>
        <div className={styles.detailTitleWrap}>
          <div className={styles.detailTitle}>{provider.label}</div>
          <div className={styles.detailSubtitle}>{subtitle}</div>
        </div>
        <Switch
          checked={provider.enabled}
          onCheckedChange={onToggle}
          title={text("Enable this provider", "启用这个 Provider")}
        />
      </div>

      {provider.setup_hint && (
        <SetupHint hint={provider.setup_hint} configured={!!provider.configured} />
      )}

      {/* claude-code has no API key — it runs on a Claude subscription via
          the local proxy (see Claude accounts below). Hide the key + base-url
          inputs that would otherwise show because claude-code shares the
          ANTHROPIC_API_KEY env name with the `anthropic` provider; pasting a
          key here does nothing (its Setup says "no API key to paste here"). */}
      {provider.api_key_env && provider.id !== "claude-code" && (
        <ApiKey
          envVar={provider.api_key_env}
          configured={!!provider.configured}
          onChanged={onChanged}
          onSaved={autoCheckAndFetch}
        />
      )}
      {provider.api_key_env && provider.id !== "claude-code" && (
        <BaseUrl provider={provider} onChanged={onChanged} />
      )}
      {/* Multi-key rotation for plain api-key providers: a pool of keys (on top
          of the single env key above) that rotates on rate limits. Pool keys
          take precedence over the env key when present. */}
      {provider.api_key_env && provider.id !== "claude-code" && (
        <PoolControls providerId={provider.id} />
      )}
      {/* Unified multi-account panel (list / add / activate / rename / remove).
          claude-code (Meridian-backed, code-paste add) and the login-only
          providers (OAuth / device-code / import-from-CLI, no API key field —
          openai-codex, github-copilot, gemini-subscription) manage accounts
          here; the panel hits /api/providers/{id}/accounts/* and picks its add
          sub-flow from the backend's add_mode. See provider-accounts.tsx. */}
      {(provider.id === "claude-code" ||
        ((provider.login_methods?.length ?? 0) > 0 && !provider.api_key_env)) && (
        <ProviderAccounts provider={provider} />
      )}
      {/* Providers with BOTH an API key field AND a native login method
          (e.g. anthropic: import-from-CLI or paste a key) keep the simple
          single-account "Sign in" panel alongside the ApiKey field above —
          their multi-key story is pool rotation, not separate accounts. */}
      {provider.id !== "claude-code" &&
        (provider.login_methods?.length ?? 0) > 0 &&
        !!provider.api_key_env && (
          <ProviderLogin provider={provider} onChanged={onChanged} />
        )}
      {/* Connectivity check applies to every HTTP provider, not just
          api-key ones. OAuth providers (openai-codex, gemini-subscription,
          github-copilot, …) need this control too — without it the
          ChatGPT subscription flow has no in-UI way to verify the OAuth
          token survived restart. Backend already handles the auth
          lookup. */}
      {provider.kind === "api" && (
        <Connectivity ref={connectivityRef} providerId={provider.id} />
      )}

      {provider.kind === "cli" ? (
        <CliInfo provider={provider} />
      ) : models.length > 0 ? (
        <ModelList provider={provider} models={models} search={modelSearch} onSearch={setModelSearch} onReload={reloadModels} />
      ) : (
        <div className={styles.detailSection}>
          <div className={styles.detailSectionTitle}>
            <span className={styles.modelCountSummary}>
              {provider.supports_fetch
                ? text("No models yet — fetch them from the provider.", "还没有模型 — 从 Provider 拉取。")
                : text("No models in the registry for this provider.", "这个 Provider 在注册表中没有模型。")}
            </span>
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
              {fetchStatus && (
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{fetchStatus}</span>
              )}
              {provider.supports_fetch && (
                <Button size="sm" onClick={fetchModels}>
                  {text("Fetch models", "获取模型")}
                </Button>
              )}
            </span>
          </div>
        </div>
      )}
    </>
  );
}
