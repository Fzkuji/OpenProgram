"use client";

/**
 * /mcp — MCP server management page.
 *
 * Outer shell is identical to /functions (functions-page.tsx):
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

import { cn } from "@/lib/utils";

import { CatalogDialog } from "./mcp-catalog-dialog";
import {
  DetailView,
  stateBadge,
  type BusyAction,
  type ServerDetail,
  type ServerStatus,
} from "./mcp-detail-view";
import { EditDialog, type EditTarget } from "./mcp-edit-dialog";
import styles from "./mcp-page.module.css";

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
