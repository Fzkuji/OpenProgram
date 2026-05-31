"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Switch } from "@/components/ui/switch";

import { ProviderIcon } from "../provider-icon";

import { ApiKey } from "./api-key";
import { BaseUrl } from "./base-url";
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
      : provider.api_key_env
        ? `API key env: ${provider.api_key_env}`
        : text("Subscription required", "需要订阅");

  const [models, setModels] = useState<Model[]>([]);
  const [modelSearch, setModelSearch] = useState("");
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

      {provider.api_key_env && (
        <ApiKey
          envVar={provider.api_key_env}
          configured={!!provider.configured}
          onChanged={onChanged}
          onSaved={autoCheckAndFetch}
        />
      )}
      {provider.api_key_env && (
        <BaseUrl provider={provider} onChanged={onChanged} />
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
          <p className={styles.modelCountSummary}>
            {text("No models in the registry for this provider.", "这个 Provider 在注册表中没有模型。")}
          </p>
        </div>
      )}
    </>
  );
}
