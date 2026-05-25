"use client";

/**
 * /mcp — MCP server management page.
 *
 * Outer shell is identical to /functions (programs-page.tsx):
 *   <div className="main">
 *     <div className={styles.view}>
 *       <div className={styles.topbar}>...</div>
 *       <div className={styles.body}>
 *         <div className={styles.serversNav}>...</div>
 *         <div className={styles.content}>...</div>
 *       </div>
 *     </div>
 *   </div>
 *
 * Same heights, same borders, same nav-row visuals as the
 * folders nav. Everything inside the right pane uses the shared
 * CSS variables (--border, --text-bright, --accent-blue, ...)
 * instead of bespoke colours.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import { CatalogDialog } from "./mcp-catalog-dialog";
import { EditDialog, type EditTarget } from "./mcp-edit-dialog";
import styles from "./mcp-page.module.css";

interface ServerAuthInfo {
  kind: "none" | "bearer" | "oauth";
  authenticated: boolean;
}
interface ServerStatus {
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
interface ToolSchema {
  name: string;
  title?: string | null;
  description: string;
  input_schema: unknown;
}
interface ServerDetail extends ServerStatus {
  tool_schemas?: ToolSchema[];
}

type BusyAction = null | "enable" | "disable" | "restart" | "delete";

function stateBadge(s: ServerStatus) {
  if (s.ready) return { label: "ready", dotCls: styles.dotReady };
  if (s.error === "disabled")
    return { label: "disabled", dotCls: styles.dotDisabled };
  if (s.error) return { label: "error", dotCls: styles.dotError };
  return { label: "starting", dotCls: styles.dotStarting };
}

export function McpPage() {
  const [servers, setServers] = useState<ServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);

  // ``reload`` only refreshes the server list; it never touches
  // ``selected``. Selection bookkeeping lives in a separate effect
  // below so a transient empty list (e.g. ``restart_server`` briefly
  // empties ``_clients`` between stop and respawn) can't reset the
  // user's selection — the right pane just shows "Loading…" for a
  // beat and then snaps back when the server reappears.
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await fetch("/api/mcp/servers", { signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (signal?.aborted) return;
      setServers((data.servers as ServerStatus[]) || []);
      setLoadErr(null);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setLoadErr(String(e));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  // ``busy`` shadowed in a ref so the polling effect doesn't re-mount
  // when an action starts. The previous version listed ``busy`` in
  // useEffect's deps — every busy-state flip restarted the effect,
  // which fired an immediate ``void reload()`` and slammed straight
  // into the backend's stop→respawn window, blanking the server list.
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    // One AbortController for the lifetime of this effect; aborting
    // it on cleanup cancels both the initial reload and any pending
    // interval-driven reload, so we don't ``setServers`` on an
    // unmounted component.
    const ac = new AbortController();
    void reload(ac.signal);
    const t = setInterval(() => {
      if (busyRef.current === null) void reload(ac.signal);
    }, 4000);
    return () => {
      ac.abort();
      clearInterval(t);
    };
  }, [reload]);

  // Selection bookkeeping — only auto-select on initial load
  // (selected is null and we have servers). Don't auto-reset when
  // a server temporarily disappears.
  useEffect(() => {
    if (selected === null && servers.length > 0) {
      setSelected(servers[0].name);
    }
  }, [servers, selected]);

  const fetchDetail = useCallback(
    async (name: string, signal?: AbortSignal) => {
      try {
        const r = await fetch(
          `/api/mcp/servers/${encodeURIComponent(name)}`,
          { signal },
        );
        if (!r.ok) { setDetail(null); return; }
        const json = (await r.json()) as ServerDetail;
        if (signal?.aborted) return;
        setDetail(json);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setDetail(null);
      }
    },
    [],
  );

  useEffect(() => {
    if (!selected) return;
    const ac = new AbortController();
    setDetail(null);
    void fetchDetail(selected, ac.signal);
    return () => ac.abort();
  }, [selected, fetchDetail]);

  async function runAction(action: Exclude<BusyAction, null>, fn: () => Promise<void>) {
    setBusy(action);
    try { await fn(); } finally { setBusy(null); }
  }

  // Merge a server's fresh status (returned from a POST/PATCH) into
  // local state without nuking the list — restart_server() empties
  // ``_clients`` briefly between stop and respawn, so a blanket
  // ``await reload()`` here would replace the whole list with [] for
  // a beat, dropping the selection.
  function upsertServer(s: ServerStatus) {
    setServers((prev) => {
      const i = prev.findIndex((p) => p.name === s.name);
      if (i < 0) return [...prev, s];
      const next = prev.slice();
      next[i] = s;
      return next;
    });
  }

  async function doRestart(name: string) {
    await runAction("restart", async () => {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/restart`,
        { method: "POST" });
      if (r.ok) {
        upsertServer(await r.json());
        await fetchDetail(name);
      }
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", async () => {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/enable`,
        { method: "POST" });
      if (r.ok) {
        upsertServer(await r.json());
        await fetchDetail(name);
      }
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", async () => {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/disable`,
        { method: "POST" });
      if (r.ok) {
        upsertServer(await r.json());
        await fetchDetail(name);
      }
    });
  }
  async function doDelete(name: string) {
    if (!confirm(`Remove MCP server "${name}"? Config will be deleted.`)) return;
    await runAction("delete", async () => {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`,
        { method: "DELETE" });
      if (r.ok) {
        setServers((prev) => prev.filter((p) => p.name !== name));
        if (selected === name) setSelected(null);
      }
    });
  }

  function openEdit(s: ServerStatus) {
    setEditing({
      mode: "edit", name: s.name,
      transport: (s.type as EditTarget["transport"]) || "local",
      command: (s.command || []).join(" "),
      env: Object.entries(s.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"),
      url: s.url || "",
      headers: Object.entries(s.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n"),
      authKind: s.auth?.kind || "none",
      bearerToken: "",
      oauthClientName: "OpenProgram",
      oauthScope: "",
      oauthClientId: "",
      oauthClientSecret: "",
      oauthRedirectPort: 0,
      enabled: s.enabled,
      timeout_seconds: s.timeout_seconds,
      alwaysLoad: !!s.always_load,
    });
  }
  function openAdd() {
    setEditing({
      mode: "add", name: "",
      transport: "local",
      command: "npx -y @modelcontextprotocol/server-...",
      env: "",
      url: "",
      headers: "",
      authKind: "none",
      bearerToken: "",
      oauthClientName: "OpenProgram",
      oauthScope: "",
      oauthClientId: "",
      oauthClientSecret: "",
      oauthRedirectPort: 0,
      enabled: true,
      timeout_seconds: 30,
      // Default deferred — matches claude-code's policy that all MCP
      // tools go through ToolSearch unless explicitly opted in. Users
      // flip this on for a small focused server whose tools the model
      // uses every turn (e.g. drawio) so its 3-5 schemas are immediate.
      alwaysLoad: false,
    });
  }

  const selectedServer = servers.find((s) => s.name === selected) || null;

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>MCP Servers</span>
          <div className={styles.toolbar}>
            <button className={styles.actionBtn} onClick={() => void reload()}>
              Refresh
            </button>
            <button
              className={styles.actionBtn}
              onClick={() => setCatalogOpen(true)}
            >
              Browse catalog
            </button>
            <button
              className={cn(styles.actionBtn, styles.actionBtnPrimary)}
              onClick={openAdd}
            >
              + Add server
            </button>
          </div>
        </div>

        <div className={styles.body}>
          <div className={styles.serversNav}>
            {loading && servers.length === 0 ? (
              <div className={styles.serverItem} style={{ cursor: "default" }}>
                <span className={styles.serverName} style={{ color: "var(--text-muted)" }}>
                  Loading…
                </span>
              </div>
            ) : servers.length === 0 ? (
              <div className={styles.serverItem} style={{ cursor: "default" }}>
                <span className={styles.serverName} style={{ color: "var(--text-muted)" }}>
                  No servers
                </span>
              </div>
            ) : (
              servers.map((s) => {
                const { dotCls } = stateBadge(s);
                return (
                  <div
                    key={s.name}
                    className={cn(
                      styles.serverItem,
                      selected === s.name && styles.active,
                    )}
                    onClick={() => setSelected(s.name)}
                  >
                    <span className={cn(styles.serverDot, dotCls)} />
                    <span className={styles.serverName}>{s.name}</span>
                    <span className={styles.serverCount}>{s.tool_count}</span>
                  </div>
                );
              })
            )}
            <div className={styles.navSep} />
            <button className={cn(styles.serverItem, styles.navAddItem)} onClick={openAdd}>
              <span className={styles.serverName}>+ Add server</span>
            </button>
          </div>

          <div className={styles.content}>
            {loadErr && (
              <div className="mb-4 rounded-md border p-3 font-mono text-xs"
                   style={{ borderColor: "var(--accent-red)", color: "var(--accent-red)" }}>
                {loadErr}
              </div>
            )}
            {selectedServer === null ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>🔌</div>
                <div className={styles.emptyText}>
                  Select a server on the left to view tools and settings.
                </div>
              </div>
            ) : (
              <DetailView
                server={selectedServer}
                detail={detail}
                busy={busy}
                onRestart={() => void doRestart(selectedServer.name)}
                onEnable={() => void doEnable(selectedServer.name)}
                onDisable={() => void doDisable(selectedServer.name)}
                onDelete={() => void doDelete(selectedServer.name)}
                onEdit={() => openEdit(selectedServer)}
              />
            )}
          </div>
        </div>
      </div>

      {editing !== null && (
        <EditDialog
          target={editing}
          onClose={() => setEditing(null)}
          onSaved={async (newName) => {
            setEditing(null);
            await reload();
            if (newName) setSelected(newName);
          }}
        />
      )}

      {catalogOpen && (
        <CatalogDialog
          existingNames={new Set(servers.map((s) => s.name))}
          onClose={() => setCatalogOpen(false)}
          onInstalled={async (name) => {
            setCatalogOpen(false);
            await reload();
            setSelected(name);
          }}
        />
      )}
    </div>
  );
}

function DetailView({
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
  const st = stateBadge(server);
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
        alert(`Update failed: ${d.detail || `HTTP ${r.status}`}`);
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
          {st.label}
        </Badge>

        <div className="ml-auto flex gap-2">
          {server.enabled ? (
            <button className={styles.actionBtn} onClick={onDisable} disabled={isBusy}>
              {busy === "disable" ? "Disabling…" : "Disable"}
            </button>
          ) : (
            <button className={styles.actionBtn} onClick={onEnable} disabled={isBusy}>
              {busy === "enable" ? "Enabling…" : "Enable"}
            </button>
          )}
          <button
            className={styles.actionBtn}
            onClick={onRestart}
            disabled={isBusy || !server.enabled}
          >
            {busy === "restart" ? "Restarting…" : "Restart"}
          </button>
          <button className={styles.actionBtn} onClick={onEdit} disabled={isBusy}>
            Edit
          </button>
          <button
            className={cn(styles.actionBtn, styles.actionBtnDanger)}
            onClick={onDelete}
            disabled={isBusy}
          >
            {busy === "delete" ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>

      {updateAvailable && (
        <div className="flex items-center gap-3 rounded-md border p-3"
             style={{ borderColor: "var(--accent-blue)",
                      background: "var(--bg-secondary)" }}>
          <span className="text-xs font-semibold"
                style={{ color: "var(--accent-blue)" }}>
            ⬆ Update available
          </span>
          <span className="flex-1 text-xs"
                style={{ color: "var(--text-muted)" }}>
            The catalog at <code>{server.source_catalog_url}</code> has a
            newer version of this server.
          </span>
          <button
            className={cn(styles.actionBtn, styles.actionBtnPrimary)}
            onClick={() => void applyUpdate()}
            disabled={updateBusy}
          >
            {updateBusy ? "Updating…" : "Update"}
          </button>
        </div>
      )}

      {server.error && server.error !== "disabled" && (
        <div className="flex flex-col gap-2 rounded-md border p-3"
             style={{ borderColor: "var(--accent-red)" }}>
          <div className="flex items-center gap-2 text-xs font-semibold"
               style={{ color: "var(--accent-red)" }}>
            {server.error_kind === "needs_reauth" && "Re-authentication required"}
            {server.error_kind === "transient" && "Reconnecting…"}
            {server.error_kind === "fatal" && "Connection failed"}
            {!server.error_kind && "Error"}
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
        <ConfigChip k="Transport" v={server.type} />
        <Sep />
        {server.type === "local" ? (
          <ConfigChip k="Command" v={<code>{(server.command || []).join(" ")}</code>} />
        ) : (
          <>
            <ConfigChip k="URL" v={<code>{server.url}</code>} />
            <Sep />
            <ConfigChip
              k="Auth"
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
                        {server.auth.authenticated ? "✓" : "× not authed"}
                      </span>
                    )}
                  </span>
                ) : (
                  "none"
                )
              }
            />
          </>
        )}
        <Sep />
        <ConfigChip k="Timeout" v={`${server.timeout_seconds}s`} />
        <Sep />
        <ConfigChip
          k="Schemas"
          v={server.always_load ? "always loaded" : "deferred (tool_search)"}
        />
        <Sep />
        <ConfigChip k="Prefix" v={<code>{server.name}__</code>} />
      </div>

      {Object.keys(server.env || {}).length > 0 && (
        <div className="rounded-md border p-3 font-mono text-xs"
             style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider"
               style={{ color: "var(--text-muted)" }}>
            Environment
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
          Tools
        </span>
        <span className="rounded-full px-1.5 text-[10px]"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)" }}>
          {server.tool_count}
        </span>
        {!server.enabled && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            (server disabled — enable to load tool list)
          </span>
        )}
      </div>

      {!detail ? (
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
          Loading tools…
        </div>
      ) : (detail.tool_schemas || []).length === 0 ? (
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
          {server.error === "disabled"
            ? "Tool list will appear here after you enable the server."
            : server.error
              ? "Tool list unavailable — see error above."
              : !server.ready
                ? "Server starting — tools will load shortly."
                : "This server exposes no tools."}
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
  const [busy, setBusy] = useState(false);
  async function go() {
    if (!confirm(
      `Re-authenticate "${name}"? This wipes stored tokens and re-opens the browser to complete a fresh OAuth flow.`,
    )) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/auth/reauth`,
        { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        alert(`Re-auth failed: ${d.detail || `HTTP ${r.status}`}`);
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
      {busy ? "Opening browser…" : "Re-authenticate"}
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

