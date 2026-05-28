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

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";

import styles from "./mcp-page.module.css";

export interface ServerAuthInfo {
  kind: "none" | "bearer" | "oauth";
  authenticated: boolean;
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
  env?: Record<string, string>;
  // remote
  url?: string;
  headers?: Record<string, string>;
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
        alert(text(`Update failed: ${d.detail || `HTTP ${r.status}`}`, `更新失败：${d.detail || `HTTP ${r.status}`}`));
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
        <span className="text-xl">🔌</span>
        <span style={{ color: "var(--accent-blue)" }}
              className="font-mono text-lg font-semibold">
          {server.name}
        </span>
        <Badge
          variant="outline"
          className="uppercase"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          <span className={cn(styles.serverDot, st.dotCls)}
                style={{ marginRight: 6 }} />
          {statusText}
        </Badge>

        <div className="ml-auto flex gap-2">
          {server.enabled ? (
            <button className={styles.actionBtn} onClick={onDisable} disabled={isBusy}>
              {busy === "disable" ? text("Disabling...", "禁用中...") : text("Disable", "禁用")}
            </button>
          ) : (
            <button className={styles.actionBtn} onClick={onEnable} disabled={isBusy}>
              {busy === "enable" ? text("Enabling...", "启用中...") : text("Enable", "启用")}
            </button>
          )}
          <button
            className={styles.actionBtn}
            onClick={onRestart}
            disabled={isBusy || !server.enabled}
          >
            {busy === "restart" ? text("Restarting...", "重启中...") : text("Restart", "重启")}
          </button>
          <button className={styles.actionBtn} onClick={onEdit} disabled={isBusy}>
            {text("Edit", "编辑")}
          </button>
          <button
            className={cn(styles.actionBtn, styles.actionBtnDanger)}
            onClick={onDelete}
            disabled={isBusy}
          >
            {busy === "delete" ? text("Deleting...", "删除中...") : t("sidebar.delete")}
          </button>
        </div>
      </div>

      {updateAvailable && (
        <div className="flex items-center gap-3 rounded-md border p-3"
             style={{ borderColor: "var(--accent-blue)",
                      background: "var(--bg-secondary)" }}>
          <span className="text-xs font-semibold"
                style={{ color: "var(--accent-blue)" }}>
            ⬆ {text("Update available", "有可用更新")}
          </span>
          <span className="flex-1 text-xs"
                style={{ color: "var(--text-muted)" }}>
            {text("The catalog at ", "目录 ")}
            <code>{server.source_catalog_url}</code>
            {text(" has a newer version of this server.", " 中有这个服务器的新版本。")}
          </span>
          <button
            className={cn(styles.actionBtn, styles.actionBtnPrimary)}
            onClick={() => void applyUpdate()}
            disabled={updateBusy}
          >
            {updateBusy ? text("Updating...", "更新中...") : text("Update", "更新")}
          </button>
        </div>
      )}

      {server.error && server.error !== "disabled" && (
        <div className="flex flex-col gap-2 rounded-md border p-3"
             style={{ borderColor: "var(--accent-red)" }}>
          <div className="flex items-center gap-2 text-xs font-semibold"
               style={{ color: "var(--accent-red)" }}>
            {server.error_kind === "needs_reauth" && text("Re-authentication required", "需要重新认证")}
            {server.error_kind === "transient" && text("Reconnecting...", "正在重连...")}
            {server.error_kind === "fatal" && text("Connection failed", "连接失败")}
            {!server.error_kind && text("Error", "错误")}
          </div>
          <div className="font-mono text-xs" style={{ color: "var(--accent-red)" }}>
            {server.error}
          </div>
          {server.error_kind === "needs_reauth" && (
            <ReauthButton name={server.name} onDone={onRestart} />
          )}
        </div>
      )}

      {/* Config */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-2 text-xs"
           style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
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
                    {server.auth.kind !== "none" && (
                      <span
                        style={{
                          color: server.auth.authenticated
                            ? "var(--accent-green)"
                            : "var(--accent-red)",
                          marginLeft: 6,
                        }}
                      >
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
        <div className="rounded-md border p-3 font-mono text-xs"
             style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider"
               style={{ color: "var(--text-muted)" }}>
            {text("Environment", "环境变量")}
          </div>
          {Object.entries(server.env || {}).map(([k, v]) => (
            <div key={k}>{k}=<span style={{ color: "var(--text-primary)" }}>{v}</span></div>
          ))}
        </div>
      )}

      {/* Tools */}
      <div className="mt-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: "var(--text-muted)" }}>
          {text("Tools", "工具")}
        </span>
        <span className="rounded-full px-1.5 text-[10px]"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)" }}>
          {server.tool_count}
        </span>
        {!server.enabled && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {text("(server disabled; enable to load tool list)", "（服务器已禁用；启用后加载工具列表）")}
          </span>
        )}
      </div>

      {!detail ? (
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
          {text("Loading tools...", "正在加载工具...")}
        </div>
      ) : (detail.tool_schemas || []).length === 0 ? (
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
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
    if (!confirm(
      text(
        `Re-authenticate "${name}"? This wipes stored tokens and re-opens the browser to complete a fresh OAuth flow.`,
        `重新认证“${name}”？这会清除已存 token，并重新打开浏览器完成新的 OAuth 流程。`,
      ),
    )) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/auth/reauth`,
        { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        alert(text(`Re-auth failed: ${d.detail || `HTTP ${r.status}`}`, `重新认证失败：${d.detail || `HTTP ${r.status}`}`));
        return;
      }
      onDone();
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      className={cn(styles.actionBtn, styles.actionBtnPrimary)}
      style={{ alignSelf: "flex-start" }}
      onClick={() => void go()}
      disabled={busy}
    >
      {busy ? text("Opening browser...", "正在打开浏览器...") : text("Re-authenticate", "重新认证")}
    </button>
  );
}


function Sep() {
  return (
    <span aria-hidden style={{ color: "var(--text-muted)", opacity: 0.5 }}>·</span>
  );
}

function ConfigChip({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: "var(--text-muted)" }}>
        {k}
      </span>
      <span className="font-mono">{v}</span>
    </span>
  );
}

function ToolRow({ server, tool }: { server: string; tool: ToolSchema }) {
  return (
    <div className="rounded-md border px-3 py-2 transition-colors"
         style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
      <div className="truncate font-mono text-sm font-medium"
           style={{ color: "var(--accent-blue)" }}>
        {server}__{tool.name}
      </div>
      <div className="mt-0.5 truncate text-xs" style={{ color: "var(--text-muted)" }}>
        {tool.description || tool.title || "—"}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit / Add dialog
// ---------------------------------------------------------------------------
