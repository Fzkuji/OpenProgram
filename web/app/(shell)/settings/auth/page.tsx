"use client";

/**
 * Settings → Auth page.
 *
 * A table-driven view of credential pools for the active profile, with
 * three actions:
 *   • Discover — scan external sources (Codex CLI, Claude Code, env
 *     vars, …) and show what could be adopted, read-only preview.
 *   • Add — paste an API key / OAuth token for a provider.
 *   • Remove — drop a credential from a pool.
 *
 * Real-time AuthEvents stream in via /api/auth/events and trigger a
 * pool refetch when the event implies a pool change (add / remove /
 * refresh). The hook keeps the UI honest without polling.
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/net/api";
import { useTranslation } from "@/lib/i18n";
import { subscribeProviderAuthEvents as subscribeAuthEvents } from "@/lib/net/provider-auth-events";
import type {
  AuthProfile,
  CredentialView,
  DiscoveredCredential,
  PoolView,
} from "@/lib/types";

const POOL_REFETCH_EVENTS = new Set([
  "pool_member_added",
  "pool_member_removed",
  "pool_rotated",
  "refresh_succeeded",
  "refresh_failed",
  "imported_from_external",
  "login_succeeded",
  "needs_reauth",
  "revoked",
]);

export default function AuthSettingsPage() {
  const { text } = useTranslation();
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [activeProfile, setActiveProfile] = useState<string>("default");
  const [pools, setPools] = useState<PoolView[]>([]);
  const [discovered, setDiscovered] = useState<DiscoveredCredential[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addForm, setAddForm] = useState<{
    provider: string;
    type: "api_key" | "oauth";
    apiKey: string;
    accessToken: string;
    refreshToken: string;
  }>({
    provider: "",
    type: "api_key",
    apiKey: "",
    accessToken: "",
    refreshToken: "",
  });

  const reload = useCallback(async (profile: string) => {
    setError(null);
    try {
      const [p, pl] = await Promise.all([
        api.listProviderProfiles(),
        api.listProviderPools(profile),
      ]);
      setProfiles(p.profiles);
      setPools(pl.pools);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload(activeProfile);
  }, [activeProfile, reload]);

  useEffect(() => {
    return subscribeAuthEvents((ev) => {
      if (POOL_REFETCH_EVENTS.has(ev.type) && ev.profile_id === activeProfile) {
        reload(activeProfile);
      }
    });
  }, [activeProfile, reload]);

  const onDiscover = async () => {
    try {
      const r = await api.discoverProviderCredentials();
      setDiscovered(r.discovered);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addForm.provider.trim()) return;
    try {
      if (addForm.type === "api_key") {
        await api.addProviderCredential(addForm.provider.trim(), activeProfile, {
          type: "api_key",
          api_key: addForm.apiKey.trim(),
        });
      } else {
        await api.addProviderCredential(addForm.provider.trim(), activeProfile, {
          type: "oauth",
          access_token: addForm.accessToken.trim(),
          refresh_token: addForm.refreshToken.trim() || undefined,
        });
      }
      setAddForm({
        provider: "",
        type: "api_key",
        apiKey: "",
        accessToken: "",
        refreshToken: "",
      });
      reload(activeProfile);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onRemove = async (cred: CredentialView) => {
    try {
      await api.removeProviderCredential(cred.provider_id, cred.profile_id, cred.credential_id);
      reload(activeProfile);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (loading) return <div className="p-6 text-sm text-muted-foreground">{text("Loading auth...", "加载认证信息中...")}</div>;

  return (
    <div className="mx-auto max-w-5xl p-6 space-y-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">{text("Auth", "认证")}</h1>
        <p className="text-sm text-muted-foreground">
          {text(
            "Credential pools for each provider in the active profile. Secrets are masked on display; the raw value never leaves the server after it has been stored.",
            "当前 profile 下每个 provider 的凭据池。密钥展示时会被遮蔽；保存后原始值不会离开服务器。",
          )}
        </p>
      </header>

      {error && (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium">{text("Profile", "配置档案")}</label>
          <select
            className="rounded border bg-background px-2 py-1 text-sm"
            value={activeProfile}
            onChange={(e) => setActiveProfile(e.target.value)}
          >
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name || p.name}
              </option>
            ))}
          </select>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium">{text("Credential pools", "凭据池")}</h2>
          <button
            className="rounded border px-3 py-1 text-sm hover:bg-muted"
            onClick={onDiscover}
          >
            {text("Discover", "发现")}
          </button>
        </div>
        {pools.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {text(
              "No credentials for this profile yet. Add one below or click Discover to scan external sources.",
              "这个 profile 还没有凭据。可以在下方添加，或点击发现扫描外部来源。",
            )}
          </p>
        ) : (
          <div className="space-y-4">
            {pools.map((pool) => (
              <PoolCard key={`${pool.provider_id}:${pool.profile_id}`} pool={pool} onRemove={onRemove} text={text} />
            ))}
          </div>
        )}
      </section>

      {discovered && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">{text("Discovered credentials", "发现的凭据")}</h2>
          <p className="text-xs text-muted-foreground">
            {text(
              "Found on this machine but not yet adopted. Add them via the form below if you want OpenProgram to use them.",
              "这些凭据存在于本机，但尚未被采用。如需 OpenProgram 使用它们，请通过下方表单添加。",
            )}
          </p>
          <ul className="space-y-2 text-sm">
            {discovered.map((d, i) => (
              <li key={i} className="rounded border px-3 py-2">
                <div className="font-mono text-xs text-muted-foreground">{d.source_id}</div>
                {d.credential ? (
                  <div>
                    {d.credential.provider_id} / {d.credential.profile_id}
                    {" — "}
                    {renderPayloadPreview(d.credential, text)}
                  </div>
                ) : (
                  <div className="text-destructive">{text("Error: ", "错误：")}{d.error}</div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-lg font-medium">{text("Add credential", "添加凭据")}</h2>
        <form onSubmit={onAdd} className="grid grid-cols-1 md:grid-cols-2 gap-3 rounded border p-4">
          <label className="text-sm">
            {text("Provider", "服务商")}
            <input
              className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm"
              placeholder="openai / anthropic / google-gemini-cli / …"
              value={addForm.provider}
              onChange={(e) => setAddForm((f) => ({ ...f, provider: e.target.value }))}
              required
            />
          </label>
          <label className="text-sm">
            {text("Type", "类型")}
            <select
              className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm"
              value={addForm.type}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, type: e.target.value as "api_key" | "oauth" }))
              }
            >
              <option value="api_key">{text("API key", "API key")}</option>
              <option value="oauth">OAuth</option>
            </select>
          </label>
          {addForm.type === "api_key" ? (
            <label className="text-sm md:col-span-2">
              {text("API key", "API key")}
              <input
                type="password"
                className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm font-mono"
                value={addForm.apiKey}
                onChange={(e) => setAddForm((f) => ({ ...f, apiKey: e.target.value }))}
                required
              />
            </label>
          ) : (
            <>
              <label className="text-sm md:col-span-2">
                {text("Access token", "访问 token")}
                <input
                  type="password"
                  className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm font-mono"
                  value={addForm.accessToken}
                  onChange={(e) => setAddForm((f) => ({ ...f, accessToken: e.target.value }))}
                  required
                />
              </label>
              <label className="text-sm md:col-span-2">
                {text("Refresh token (optional)", "刷新 token（可选）")}
                <input
                  type="password"
                  className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm font-mono"
                  value={addForm.refreshToken}
                  onChange={(e) => setAddForm((f) => ({ ...f, refreshToken: e.target.value }))}
                />
              </label>
            </>
          )}
          <button type="submit" className="md:col-span-2 rounded bg-primary px-3 py-2 text-sm text-primary-foreground">
            {text("Add", "添加")}
          </button>
        </form>
      </section>
    </div>
  );
}

function PoolCard({
  pool,
  onRemove,
  text,
}: {
  pool: PoolView;
  onRemove: (cred: CredentialView) => void;
  text: (en: string, zh: string) => string;
}) {
  return (
    <div className="rounded border">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <div className="font-medium">{pool.provider_id}</div>
          <div className="text-xs text-muted-foreground">
            {text("profile", "配置档案")}：{pool.profile_id} · {text("strategy", "策略")}：{pool.strategy}
          </div>
        </div>
        <div className="text-xs text-muted-foreground">{pool.credentials.length} {text("credential(s)", "个凭据")}</div>
      </div>
      <ul>
        {pool.credentials.map((cred) => (
          <li
            key={cred.credential_id}
            className="flex items-center justify-between px-3 py-2 text-sm even:bg-muted/30"
          >
            <div className="flex flex-col gap-0.5">
              <div className="font-mono text-xs">{cred.credential_id}</div>
              <div>{renderPayloadPreview(cred, text)}</div>
              <div className="text-xs text-muted-foreground">
                {text("source", "来源")}：{cred.source}
                {cred.read_only ? ` · ${text("read-only", "只读")}` : ""}
                {" · "}{text("status", "状态")}：
                {cred.status}
              </div>
            </div>
            <button
              className="rounded border px-2 py-1 text-xs hover:bg-destructive/10"
              onClick={() => onRemove(cred)}
              aria-label={`${text("Remove", "移除")} ${cred.credential_id}`}
            >
              {text("Remove", "移除")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderPayloadPreview(cred: CredentialView, text: (en: string, zh: string) => string): string {
  const p = cred.payload;
  if (p.type === "api_key") return `API key ${p.api_key_preview ?? ""}`;
  if (p.type === "oauth")
    return `OAuth ${p.access_token_preview ?? ""}${p.has_refresh_token ? ` (${text("+refresh", "+刷新")})` : ""}`;
  if (p.type === "cli_delegated") return `${text("CLI-delegated", "CLI 委托")} -> ${p.store_path ?? ""}`;
  if (p.type === "device_code") return `${text("Device code", "设备码")} ${p.access_token_preview ?? ""}`;
  if (p.type === "external_process") return `${text("Helper", "辅助进程")} ${(p.command || []).join(" ")}`;
  return cred.kind;
}
