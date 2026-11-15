/**
 * MCP server detail view — the right pane of /mcp.
 *
 * Hosts the read-only details, the action buttons (restart / enable /
 * disable / delete / re-auth), the catalog-update widget, the tool
 * list, and the formatting helpers (``ConfigChip``, ``ToolRow``,
 * ``ReauthButton``, ``Sep``). Pulled out of mcp-page.tsx so the page
 * file is a clean shell + state + list, ~250 lines instead of ~1300.
 *
 * Types (``ServerStatus``, ``ServerDetail``, ``BusyAction``) and the
 * ``stateBadge`` helper live here too so the page imports them back —
 * single source of truth, no circular import.
 */
"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { PlugZapIcon } from "@/components/animated-icons";

import styles from "./mcp-page.module.css";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";

/**
 * A stored `env` / `header` value as the API returns it: presence and
 * shape, never the value. The backend masks every value rather than
 * only the key-shaped names, because an MCP server's env is free-form
 * and any entry can hold a credential.
 */
export interface SecretPreview {
  has_value: boolean;
  masked: string;
}
/** `{NAME: preview}` — names are visible, values never are. */
export type SecretMap = Record<string, SecretPreview>;

export interface ServerAuthInfo {
  kind: "none" | "bearer" | "oauth";
  authenticated: boolean;
  // bearer — presence + mask only, no stored token
  has_token?: boolean;
  masked_token?: string;
  // oauth — client_id is not a secret; the secret is presence + mask
  client_name?: string;
  client_id?: string;
  scope?: string;
  redirect_port?: number;
  has_client_secret?: boolean;
  masked_client_secret?: string;
}
export interface ServerStatus {
  name: string;
  type: string;               // "local" | "http" | "sse"
  enabled: boolean;
  timeout_seconds: number;
  always_load: boolean;        // ship full schemas on turn 1 (vs defer via tool_search)
  ready: boolean;
  error: string | null;
  error_kind?: null | "transient" | "needs_reauth" | "fatal";
  tool_count: number;
  tools: string[];
  // catalog provenance — present iff the server was installed from /mcp catalog
  source_catalog_url?: string | null;
  source_entry_hash?: string | null;
  // local
  command?: string[];
  env?: SecretMap;
  // remote
  url?: string;
  headers?: SecretMap;
  auth?: ServerAuthInfo;
}
export interface ToolSchema {
  name: string;
  title?: string | null;
  description: string;
  input_schema: unknown;
}
export interface ServerDetail extends ServerStatus {
  tool_schemas?: ToolSchema[];
}

export type BusyAction = null | "enable" | "disable" | "restart" | "delete";

export function stateBadge(s: ServerStatus) {
  if (s.ready) return { label: "ready", dotCls: styles.dotReady };
  if (s.error === "disabled")
    return { label: "disabled", dotCls: styles.dotDisabled };
  if (s.error) return { label: "error", dotCls: styles.dotError };
  return { label: "starting", dotCls: styles.dotStarting };
}

export function DetailView({
  server, detail, busy,
  onRestart, onEnable, onDisable, onDelete, onEdit,
}: {
  server: ServerStatus;
  detail: ServerDetail | null;
  busy: BusyAction;
  onRestart: () => void;
  onEnable: () => void;
  onDisable: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const { t, text } = useTranslation();
  const st = stateBadge(server);
  const statusText = text(st.label, {
    ready: "就绪",
    disabled: "已禁用",
    error: "错误",
    starting: "启动中",
  }[st.label] || st.label);
  const isBusy = busy !== null;
  // Background-check whether the catalog has a newer version of this
  // server. Only runs when the server was actually installed from a
  // catalog — hand-added servers have no source URL so nothing to diff.
  const [updateAvailable, setUpdateAvailable] = useState<boolean | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  useEffect(() => {
    if (!server.source_catalog_url) { setUpdateAvailable(null); return; }
    const ac = new AbortController();
    (async () => {
      try {
        const r = await fetch(
          `/api/mcp/catalog/diff?url=${encodeURIComponent(server.source_catalog_url!)}`,
          { signal: ac.signal },
        );
        if (!r.ok) return;
        const d = await r.json();
        if (ac.signal.aborted) return;
        setUpdateAvailable(server.name in (d.outdated || {}));
      } catch { /* ignore aborts / network blips */ }
    })();
    return () => ac.abort();
  }, [server.name, server.source_catalog_url, server.source_entry_hash]);

  async function applyUpdate() {
    if (!server.source_catalog_url || updateBusy) return;
    setUpdateBusy(true);
    try {
      const r = await fetch(
        `/api/mcp/servers/${encodeURIComponent(server.name)}/update_from_catalog`,
        { method: "POST" },
      );
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showToast(text(`Update failed: ${d.detail || `HTTP ${r.status}`}`, `更新失败：${d.detail || `HTTP ${r.status}`}`), { tone: "error" });
        return;
      }
      setUpdateAvailable(false);
      onRestart();   // refresh the row + detail panel
    } finally {
      setUpdateBusy(false);
    }
  }
  return (
    <div className="flex w-full flex-col gap-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-fs-title"><PlugZapIcon size={20} /></span>
        <span className="text-fs-md font-semibold text-accent-blue">
          {server.name}
        </span>
        <Badge
          variant="outline"
          className="border-border uppercase text-text-muted"
        >
          <span className={cn(styles.serverDot, st.dotCls, "mr-1.5")} />
          {statusText}
        </Badge>

        <div className="ml-auto flex gap-2">
          {server.enabled ? (
            <Button variant="elevated" onClick={onDisable} disabled={isBusy}>
              {busy === "disable" ? text("Disabling...", "禁用中...") : text("Disable", "禁用")}
            </Button>
          ) : (
            <Button variant="elevated" onClick={onEnable} disabled={isBusy}>
              {busy === "enable" ? text("Enabling...", "启用中...") : text("Enable", "启用")}
            </Button>
          )}
          <Button variant="elevated"
            onClick={onRestart}
            disabled={isBusy || !server.enabled}
          >
            {busy === "restart" ? text("Restarting...", "重启中...") : text("Restart", "重启")}
          </Button>
          <Button variant="elevated" onClick={onEdit} disabled={isBusy}>
            {text("Edit", "编辑")}
          </Button>
          <Button variant="destructive"
            onClick={onDelete}
            disabled={isBusy}
          >
            {busy === "delete" ? text("Deleting...", "删除中...") : t("sidebar.delete")}
          </Button>
        </div>
      </div>

      {updateAvailable && (
        <div className="flex items-center gap-3 rounded-md border border-accent-blue bg-bg-secondary p-3">
          <span className="text-fs-2xs font-semibold text-accent-blue">
            ⬆ {text("Update available", "有可用更新")}
          </span>
          <span className="flex-1 text-fs-2xs text-text-muted">
            {text("The catalog at ", "目录 ")}
            <code>{server.source_catalog_url}</code>
            {text(" has a newer version of this server.", " 中有这个服务器的新版本。")}
          </span>
          <Button
            onClick={() => void applyUpdate()}
            disabled={updateBusy}
          >
            {updateBusy ? text("Updating...", "更新中...") : text("Update", "更新")}
          </Button>
        </div>
      )}

      {server.error && server.error !== "disabled" && (
        <div className="flex flex-col gap-2 rounded-md border border-accent-red p-3">
          <div className="flex items-center gap-2 text-fs-2xs font-semibold text-accent-red">
            {server.error_kind === "needs_reauth" && text("Re-authentication required", "需要重新认证")}
            {server.error_kind === "transient" && text("Reconnecting...", "正在重连...")}
            {server.error_kind === "fatal" && text("Connection failed", "连接失败")}
            {!server.error_kind && text("Error", "错误")}
          </div>
          <div className="text-fs-2xs text-accent-red">
            {server.error}
          </div>
          {server.error_kind === "needs_reauth" && (
            <ReauthButton name={server.name} onDone={onRestart} />
          )}
        </div>
      )}

      {/* Config */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-bg-secondary px-3 py-2 text-fs-2xs">
        <ConfigChip k={text("Transport", "传输")} v={server.type} />
        <Sep />
        {server.type === "local" ? (
          <ConfigChip k={text("Command", "命令")} v={<code>{(server.command || []).join(" ")}</code>} />
        ) : (
          <>
            <ConfigChip k="URL" v={<code>{server.url}</code>} />
            <Sep />
            <ConfigChip
              k={text("Auth", "认证")}
              v={
                server.auth ? (
                  <span>
                    {server.auth.kind}
                    {/* Stored credential shown as a mask so the user can
                        tell one is set — never as the value itself. */}
                    {server.auth.has_token && server.auth.masked_token && (
                      <span className="ml-1.5 text-text-muted">
                        {server.auth.masked_token}
                      </span>
                    )}
                    {server.auth.has_client_secret
                      && server.auth.masked_client_secret && (
                      <span className="ml-1.5 text-text-muted">
                        {server.auth.masked_client_secret}
                      </span>
                    )}
                    {server.auth.kind !== "none" && (
                      <span className={cn("ml-1.5", server.auth.authenticated ? "text-accent-green" : "text-accent-red")}>
                        {server.auth.authenticated ? "✓" : text("not authed", "未认证")}
                      </span>
                    )}
                  </span>
                ) : (
                  text("none", "无")
                )
              }
            />
          </>
        )}
        <Sep />
        <ConfigChip k={text("Timeout", "超时")} v={`${server.timeout_seconds}s`} />
        <Sep />
        <ConfigChip
          k={text("Schemas", "Schema")}
          v={server.always_load ? text("always loaded", "始终加载") : text("deferred (tool_search)", "延迟加载（tool_search）")}
        />
        <Sep />
        <ConfigChip k={text("Prefix", "前缀")} v={<code>{server.name}__</code>} />
      </div>

      {Object.keys(server.env || {}).length > 0 && (
        <SecretMapPanel
          title={text("Environment", "环境变量")}
          entries={server.env || {}}
          separator="="
        />
      )}

      {Object.keys(server.headers || {}).length > 0 && (
        <SecretMapPanel
          title={text("Headers", "请求头")}
          entries={server.headers || {}}
          separator=": "
        />
      )}

      {/* Tools */}
      <div className="mt-2 flex items-center gap-2">
        <SectionLabel>{text("Tools", "工具")}</SectionLabel>
        <span className="rounded-full bg-bg-tertiary px-1.5 text-fs-2xs text-text-muted">
          {server.tool_count}
        </span>
        {!server.enabled && (
          <span className="text-fs-2xs text-text-muted">
            {text("(server disabled; enable to load tool list)", "（服务器已禁用；启用后加载工具列表）")}
          </span>
        )}
      </div>

      {!detail ? (
        <div className="text-fs-xs text-text-muted">
          {text("Loading tools...", "正在加载工具...")}
        </div>
      ) : (detail.tool_schemas || []).length === 0 ? (
        <div className="text-fs-xs text-text-muted">
          {server.error === "disabled"
            ? text("Tool list will appear here after you enable the server.", "启用服务器后，工具列表会显示在这里。")
            : server.error
              ? text("Tool list unavailable. See error above.", "工具列表不可用。请查看上方错误。")
              : !server.ready
                ? text("Server starting. Tools will load shortly.", "服务器正在启动，工具稍后会加载。")
                : text("This server exposes no tools.", "这个服务器没有提供工具。")}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {(detail.tool_schemas || []).map((t) => (
            <ToolRow key={t.name} server={server.name} tool={t} />
          ))}
        </div>
      )}
    </div>
  );
}

function ReauthButton({ name, onDone }: { name: string; onDone: () => void }) {
  const { text } = useTranslation();
  const [busy, setBusy] = useState(false);
  async function go() {
    if (!(await confirmAction({
      title: text(`Re-authenticate "${name}"?`, `重新认证“${name}”？`),
      description: text(
        "This wipes stored tokens and re-opens the browser to complete a fresh OAuth flow.",
        "这会清除已存 token，并重新打开浏览器完成新的 OAuth 流程。",
      ),
      confirmLabel: text("Re-authenticate", "重新认证"),
    }))) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/auth/reauth`,
        { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showToast(text(`Re-auth failed: ${d.detail || `HTTP ${r.status}`}`, `重新认证失败：${d.detail || `HTTP ${r.status}`}`), { tone: "error" });
        return;
      }
      onDone();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Button
      className="self-start"
      onClick={() => void go()}
      disabled={busy}
    >
      {busy ? text("Opening browser...", "正在打开浏览器...") : text("Re-authenticate", "重新认证")}
    </Button>
  );
}


/**
 * Renders an `env` / `headers` map. Names are shown in full; values
 * only ever as the mask the API returned — this view has no access to
 * the stored value and no control that would fetch one.
 */
function SecretMapPanel({
  title, entries, separator,
}: {
  title: string;
  entries: SecretMap;
  separator: string;
}) {
  const { text } = useTranslation();
  return (
    <div className="rounded-md border border-border bg-bg-secondary p-3 font-mono text-fs-2xs">
      <SectionLabel className="mb-1 block">{title}</SectionLabel>
      {Object.entries(entries).map(([k, v]) => (
        <div key={k}>
          {k}{separator}
          <span className="text-text-muted">
            {v.has_value ? v.masked : text("(empty)", "（空）")}
          </span>
        </div>
      ))}
    </div>
  );
}

/** The small uppercase caption used for config keys and panel titles. */
function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("text-fs-2xs font-semibold uppercase tracking-wider text-text-muted", className)}>
      {children}
    </span>
  );
}

function Sep() {
  return (
    <span aria-hidden className="text-text-muted opacity-50">·</span>
  );
}

function ConfigChip({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <SectionLabel>{k}</SectionLabel>
      <span>{v}</span>
    </span>
  );
}

function ToolRow({ server, tool }: { server: string; tool: ToolSchema }) {
  return (
    <div className="rounded-md border border-border bg-bg-secondary px-3 py-2 transition-colors">
      <div className="truncate text-fs-xs font-medium text-accent-blue">
        {server}__{tool.name}
      </div>
      <div className="mt-0.5 truncate text-fs-2xs text-text-muted">
        {tool.description || tool.title || "—"}
      </div>
    </div>
  );
}
