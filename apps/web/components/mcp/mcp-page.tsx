"use client";

/**
 * /mcp — MCP server management page.
 *
 * Outer chrome (64px header with title + tab pill + action buttons,
 * the split body grid, the empty state) comes from
 * components/ui/manage-page, shared verbatim with /skills and
 * /plugins so the three management pages read as one system.
 * What stays local to this module is the master-detail server rail:
 * connection-state dot, tool count, and the right-hand DetailView.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { PlugZapIcon } from "@/components/animated-icons";
import { SearchInput } from "@/components/ui/search-input";
import { ManagePageHeader, ManageSubnav, managePageStyles as shared } from "@/components/ui/manage-page";
import { jsonFetch } from "@/lib/net/fetch-client";

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

export function McpPage({
  embedded,
  query,
  catalogOpen: catalogOpenProp,
  onCatalogOpen,
  onCatalogClose,
  reloadNonce,
  addNonce,
}: {
  embedded?: boolean;
  query?: string;
  catalogOpen?: boolean;
  onCatalogOpen?: () => void;
  onCatalogClose?: () => void;
  reloadNonce?: number;
  addNonce?: number;
} = {}) {
  const { t, text } = useTranslation();
  const [servers, setServers] = useState<ServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [localCatalogOpen, setLocalCatalogOpen] = useState(false);
  const catalogOpen = catalogOpenProp ?? localCatalogOpen;
  const openCatalog = onCatalogOpen ?? (() => setLocalCatalogOpen(true));
  const closeCatalog = onCatalogClose ?? (() => setLocalCatalogOpen(false));
  const [filter, setFilter] = useState("");
  const filterValue = query !== undefined ? query : filter;

  // ``reload`` only refreshes the server list; it never touches
  // ``selected``. Selection bookkeeping lives in a separate effect
  // below so a transient empty list (e.g. ``restart_server`` briefly
  // empties ``_clients`` between stop and respawn) can't reset the
  // user's selection — the right pane just shows "Loading…" for a
  // beat and then snaps back when the server reappears.
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await jsonFetch<{ servers: ServerStatus[] }>("/api/mcp/servers", { signal });
      if (signal?.aborted) return;
      setServers((data.servers as ServerStatus[]) || []);
      setLoadErr(null);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!reloadNonce) return;
    void reload();
  }, [reloadNonce, reload]);

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
        const json = await jsonFetch<ServerDetail>(
          `/api/mcp/servers/${encodeURIComponent(name)}`,
          { signal },
        );
        if (signal?.aborted) return;
        setDetail(json);
        setActionErr(null);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setDetail(null);
        setActionErr(e instanceof Error ? e.message : String(e));
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
    setActionErr(null);
    try {
      await fn();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
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
      const server = await jsonFetch<ServerStatus>(`/api/mcp/servers/${encodeURIComponent(name)}/restart`,
        { method: "POST" });
      upsertServer(server);
      await fetchDetail(name);
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", async () => {
      const server = await jsonFetch<ServerStatus>(`/api/mcp/servers/${encodeURIComponent(name)}/enable`,
        { method: "POST" });
      upsertServer(server);
      await fetchDetail(name);
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", async () => {
      const server = await jsonFetch<ServerStatus>(`/api/mcp/servers/${encodeURIComponent(name)}/disable`,
        { method: "POST" });
      upsertServer(server);
      await fetchDetail(name);
    });
  }
  async function doDelete(name: string) {
    if (!confirm(text(
      `Remove MCP server "${name}"? Config will be deleted.`,
      `移除 MCP 服务器“${name}”？配置会被删除。`,
    ))) return;
    await runAction("delete", async () => {
      await jsonFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      setServers((prev) => prev.filter((p) => p.name !== name));
      if (selected === name) setSelected(null);
    });
  }

  function openEdit(s: ServerStatus) {
    setEditing({
      mode: "edit", name: s.name,
      transport: (s.type as EditTarget["transport"]) || "local",
      command: (s.command || []).join(" "),
      // Secret-bearing maps start empty: the API returns masks, not
      // values, and posting a mask back would store the mask. An
      // untouched field is omitted from the PATCH, which the backend
      // reads as "preserve". The stored names are passed separately so
      // the dialog can list what is already set.
      env: "",
      storedEnvNames: Object.keys(s.env || {}),
      url: s.url || "",
      headers: "",
      storedHeaderNames: Object.keys(s.headers || {}),
      authKind: s.auth?.kind || "none",
      bearerToken: "",
      hasStoredBearerToken: !!s.auth?.has_token,
      oauthClientName: s.auth?.client_name || "OpenProgram",
      oauthScope: s.auth?.scope || "",
      oauthClientId: s.auth?.client_id || "",
      oauthClientSecret: "",
      hasStoredClientSecret: !!s.auth?.has_client_secret,
      oauthRedirectPort: s.auth?.redirect_port || 0,
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
      storedEnvNames: [],
      url: "",
      headers: "",
      storedHeaderNames: [],
      authKind: "none",
      bearerToken: "",
      hasStoredBearerToken: false,
      oauthClientName: "OpenProgram",
      oauthScope: "",
      oauthClientId: "",
      oauthClientSecret: "",
      hasStoredClientSecret: false,
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

  useEffect(() => {
    if (!addNonce) return;
    openAdd();
  }, [addNonce]);

  const selectedServer = servers.find((s) => s.name === selected) || null;
  const readyCount = servers.filter((server) => server.ready).length;
  const issueCount = servers.filter((server) => server.enabled && !!server.error && server.error !== "disabled").length;

  const shownServers = useMemo(() => {
    const q = filterValue.trim().toLowerCase();
    if (!q) return servers;
    return servers.filter((s) => {
      if (s.name.toLowerCase().includes(q)) return true;
      if ((s.url || "").toLowerCase().includes(q)) return true;
      if ((s.command || []).join(" ").toLowerCase().includes(q)) return true;
      if ((s.error || "").toLowerCase().includes(q)) return true;
      if ((s.tools || []).some((tool) => tool.toLowerCase().includes(q))) return true;
      return false;
    });
  }, [servers, filterValue]);

  const splitAndDialogs = (
    <>
        {embedded && (
          <ManageSubnav
            tabs={[
              { id: "installed", label: text("Installed", "已安装"), count: servers.length },
              { id: "discover", label: text("Discover", "发现") },
            ]}
            activeTab="installed"
            onTabChange={(id) => { if (id === "discover") openCatalog(); }}
            summary={text(
              `${servers.length} installed · ${readyCount} available · ${issueCount} issues`,
              `已安装 ${servers.length} 个 · 可用 ${readyCount} 个 · ${issueCount} 个问题`,
            )}
          />
        )}
        {actionErr && <div className={shared.errorBar} role="alert">{actionErr}</div>}
        <div className={shared.splitBody}>
          <div className={styles.serversNav}>
            {query === undefined && (
            <div className={styles.navSearch}>
              <SearchInput
                value={filter}
                onChange={setFilter}
                placeholder={text("Search servers...", "搜索服务器...")}
              />
            </div>
            )}
            {loading && servers.length === 0 ? (
              <div className={styles.serverItem} style={{ cursor: "default" }}>
                <span className={styles.serverName} style={{ color: "var(--text-muted)" }}>
                  {text("Loading...", "加载中...")}
                </span>
              </div>
            ) : shownServers.length === 0 ? (
              <div className={styles.serverItem} style={{ cursor: "default" }}>
                <span className={styles.serverName} style={{ color: "var(--text-muted)" }}>
                  {filterValue.trim()
                    ? text("No matches", "没有匹配结果")
                    : text("No servers", "没有服务器")}
                </span>
              </div>
            ) : (
              shownServers.map((s) => {
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
              <span className={styles.serverName}>+ {text("Add server", "添加服务器")}</span>
            </button>
          </div>

          <div className={styles.content}>
            {loadErr && <div className={shared.errorBar} role="alert">{loadErr}</div>}
            {selectedServer === null ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>
                  <PlugZapIcon size={40} />
                </div>
                <div className={styles.emptyText}>
                  {text("Select a server on the left to view tools and settings.", "选择左侧服务器查看工具和设置。")}
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
          onClose={() => closeCatalog()}
          onInstalled={async (name) => {
            closeCatalog();
            await reload();
            setSelected(name);
          }}
        />
      )}
    </>
  );

  if (embedded) return splitAndDialogs;

  return (
    <div className="main">
      <div className={shared.view}>
        <ManagePageHeader
          title={t("nav.mcp")}
          tabs={[
            {
              id: "servers",
              label: text("Servers", "服务器"),
              count: servers.length,
            },
          ]}
          activeTab="servers"
          actions={[
            { label: t("sidebar.refresh"), onClick: () => { void reload(); } },
            { label: text("Discover MCP servers", "发现 MCP 服务器"), onClick: openCatalog },
            { label: text("Add server", "添加服务器"), onClick: openAdd, primary: true },
          ]}
        />
        {splitAndDialogs}
      </div>
    </div>
  );
}
