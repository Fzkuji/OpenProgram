"use client";

/**
 * Settings → Auth page.
 *
 * A table-driven view of credential pools for the active account, with
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

import { KeyRound } from "lucide-react";

import { api } from "@/lib/net/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { PlusIcon } from "@/components/animated-icons";
import pageStyles from "@/components/settings/settings-page.module.css";
import styles from "@/components/settings/providers/provider-pane.module.css";
import { useTranslation } from "@/lib/i18n";
import { subscribeProviderAuthEvents as subscribeAuthEvents } from "@/lib/net/auth/provider-auth-events";
import type {
  AuthAccount,
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
  const [accounts, setAccounts] = useState<AuthAccount[]>([]);
  const [activeAccount, setActiveAccount] = useState<string>("default");
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

  const reload = useCallback(async (account: string) => {
    setError(null);
    try {
      const [p, pl] = await Promise.all([
        api.listProviderAccounts(),
        api.listProviderPools(account),
      ]);
      setAccounts(p.accounts);
      setPools(pl.pools);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload(activeAccount);
  }, [activeAccount, reload]);

  useEffect(() => {
    return subscribeAuthEvents((ev) => {
      if (POOL_REFETCH_EVENTS.has(ev.type) && ev.account_id === activeAccount) {
        reload(activeAccount);
      }
    });
  }, [activeAccount, reload]);

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
        await api.addProviderCredential(addForm.provider.trim(), activeAccount, {
          type: "api_key",
          api_key: addForm.apiKey.trim(),
        });
      } else {
        await api.addProviderCredential(addForm.provider.trim(), activeAccount, {
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
      reload(activeAccount);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onRemove = async (cred: CredentialView) => {
    try {
      await api.removeProviderCredential(cred.provider_id, cred.account_id, cred.credential_id);
      reload(activeAccount);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (loading) {
    return (
      <div className={pageStyles.page}>
        <div className={pageStyles.pageHeader}>
          <h2 className={pageStyles.pageTitle}>{text("Auth", "认证")}</h2>
        </div>
        <div className={pageStyles.pageBody}>
          <div className={pageStyles.rowHelp}>{text("Loading auth...", "加载认证信息中...")}</div>
        </div>
      </div>
    );
  }

  // Same shell as the other Settings pages: .page > .pageHeader > .pageBody,
  // a <section> per block with .sectionTitle and a .card of rows.
  return (
    <div className={pageStyles.page}>
      <div className={pageStyles.pageHeader}>
        <h2 className={pageStyles.pageTitle}>{text("Auth", "认证")}</h2>
        <p className={pageStyles.pageMeta}>
          {text(
            "Credential pools for each provider in the active account. Secrets are masked on display; the raw value never leaves the server after it has been stored.",
            "当前账号下每个 provider 的凭据池。密钥展示时会被遮蔽；保存后原始值不会离开服务器。",
          )}
        </p>
      </div>
      <div className={pageStyles.pageBody}>
        {error && <div className={pageStyles.rowStatusError} role="alert">{error}</div>}

        <section>
          <div className={pageStyles.card}>
            <div className={pageStyles.row}>
              <label className={pageStyles.label} htmlFor="auth-account">{text("Account", "账号")}</label>
              <div className={pageStyles.control}>
                <SimpleSelect label={text("Account", "账号")}
                  id="auth-account"
                  value={activeAccount}
                  onValueChange={setActiveAccount}
                  options={accounts.map((p) => ({ value: p.name, label: p.display_name || p.name }))}
                />
              </div>
            </div>
          </div>
        </section>

        <section>
          <div className={`${pageStyles.authSectionHead} flex items-center justify-between gap-[12px]`}>
            <h3 className={pageStyles.sectionTitle}>{text("Credential pools", "凭据池")}</h3>
            <Button variant="elevated" onClick={onDiscover}>{text("Discover", "发现")}</Button>
          </div>
          {pools.length === 0 ? (
            <ManageEmptyState
              compact
              icon={<KeyRound />}
              title={text("No credentials for this account yet", "这个账号还没有凭据")}
              description={text(
                "Add one below or click Discover to scan external sources.",
                "可以在下方添加，或点击发现扫描外部来源。",
              )}
            />
          ) : (
            pools.map((pool) => (
              <PoolCard key={`${pool.provider_id}:${pool.account_id}`} pool={pool} onRemove={onRemove} text={text} />
            ))
          )}
        </section>

        {discovered && (
          <section>
            <h3 className={pageStyles.sectionTitle}>{text("Discovered credentials", "发现的凭据")}</h3>
            <p className={pageStyles.pageMeta}>
              {text(
                "Found on this machine but not yet adopted. Add them via the form below if you want OpenProgram to use them.",
                "这些凭据存在于本机，但尚未被采用。如需 OpenProgram 使用它们，请通过下方表单添加。",
              )}
            </p>
            <div className={pageStyles.card}>
              {discovered.map((d, i) => (
                <div key={i} className={pageStyles.row}>
                  <div className={pageStyles.label}>
                    <div className={pageStyles.authMono}>{d.source_id}</div>
                    {d.credential ? (
                      <p className={pageStyles.rowHelp}>
                        {d.credential.provider_id} / {d.credential.account_id}
                        {" — "}
                        {renderPayloadPreview(d.credential, text)}
                      </p>
                    ) : (
                      <p className={pageStyles.rowStatusError}>{text("Error: ", "错误：")}{d.error}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        <section>
          <h3 className={pageStyles.sectionTitle}>{text("Add credential", "添加凭据")}</h3>
          <form onSubmit={onAdd} className={`${pageStyles.card} ${pageStyles.authForm}`}>
            <label className={pageStyles.authField}>
              {text("Provider", "服务商")}
              <Input
                placeholder="openai / anthropic / google-gemini-cli / …"
                value={addForm.provider}
                onChange={(e) => setAddForm((f) => ({ ...f, provider: e.target.value }))}
                required
              />
            </label>
            <label className={pageStyles.authField}>
              {text("Type", "类型")}
              <SimpleSelect label={text("Type", "类型")}
                className="w-full"
                value={addForm.type}
                onValueChange={(value) => setAddForm((f) => ({ ...f, type: value }))}
                options={[
                  { value: "api_key" as const, label: text("API key", "API key") },
                  { value: "oauth" as const, label: "OAuth" },
                ]}
              />
            </label>
            {addForm.type === "api_key" ? (
              <label className={`${pageStyles.authField} ${pageStyles.authFieldWide}`}>
                {text("API key", "API key")}
                <Input
                  type="password"
                  className={pageStyles.authMono}
                  value={addForm.apiKey}
                  onChange={(e) => setAddForm((f) => ({ ...f, apiKey: e.target.value }))}
                  required
                />
              </label>
            ) : (
              <>
                <label className={`${pageStyles.authField} ${pageStyles.authFieldWide}`}>
                  {text("Access token", "访问 token")}
                  <Input
                    type="password"
                    className={pageStyles.authMono}
                    value={addForm.accessToken}
                    onChange={(e) => setAddForm((f) => ({ ...f, accessToken: e.target.value }))}
                    required
                  />
                </label>
                <label className={`${pageStyles.authField} ${pageStyles.authFieldWide}`}>
                  {text("Refresh token (optional)", "刷新 token（可选）")}
                  <Input
                    type="password"
                    className={pageStyles.authMono}
                    value={addForm.refreshToken}
                    onChange={(e) => setAddForm((f) => ({ ...f, refreshToken: e.target.value }))}
                  />
                </label>
              </>
            )}
            <div className={pageStyles.authFieldWide}>
              <Button type="submit"><PlusIcon size={16} aria-hidden />{text("Add", "添加")}</Button>
            </div>
          </form>
        </section>
      </div>
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
    <div className={pageStyles.card}>
      <div className={pageStyles.row}>
        <div className={pageStyles.label}>
          <div className={styles.authPoolTitle}>{pool.provider_id}</div>
          <p className={pageStyles.rowHelp}>
            {text("account", "账号")}：{pool.account_id} · {text("strategy", "策略")}：{pool.strategy}
          </p>
        </div>
        <div className={pageStyles.control}>
          <span className={pageStyles.rowHelp}>{pool.credentials.length} {text("credential(s)", "个凭据")}</span>
        </div>
      </div>
      {pool.credentials.map((cred) => (
        <div key={cred.credential_id} className={pageStyles.row}>
          <div className={pageStyles.label}>
            <div className={pageStyles.authMono}>{cred.credential_id}</div>
            <div>{renderPayloadPreview(cred, text)}</div>
            <p className={pageStyles.rowHelp}>
              {text("source", "来源")}：{cred.source}
              {cred.read_only ? ` · ${text("read-only", "只读")}` : ""}
              {" · "}{text("status", "状态")}：
              {cred.status}
            </p>
          </div>
          <div className={pageStyles.control}>
            <Button
              variant="destructive"
              onClick={() => onRemove(cred)}
              aria-label={`${text("Remove", "移除")} ${cred.credential_id}`}
            >
              {text("Remove", "移除")}
            </Button>
          </div>
        </div>
      ))}
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
  if (p.type === "credential_process") return `${text("Helper", "辅助进程")} ${(p.command || []).join(" ")}`;
  return cred.kind;
}
