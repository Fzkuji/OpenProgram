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

import { useTranslation } from "@/lib/i18n";
import { PlugZapIcon, PlusIcon, SearchIcon } from "@/components/animated-icons";
import { SearchInput } from "@/components/ui/search-input";
import { ManageEmptyState, ManagePageHeader, ManageRow, ManageSubnav, managePageStyles as shared } from "@/components/ui/manage-page";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { jsonFetch, type HttpError } from "@/lib/net/fetch-client";
import { useChanged } from "@/lib/net/ws/changes";
import { isAbort, keep, lastKept, readKept, readUntilAnswered } from "@/lib/abilities/kept-read";

import { CatalogPanel } from "./mcp-catalog-panel";
import {
  DetailView,
  stateBadge,
  type BusyAction,
  type ServerDetail,
  type ServerStatus,
} from "./mcp-detail-view";
import { EditDialog, type EditTarget } from "./mcp-edit-dialog";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { MCP_SERVERS_KEY, mcpDetailKey, readMcpDetail, readMcpServers } from "./mcp-reads";

export function McpPage({
  embedded,
  query,
  reloadNonce,
}: {
  embedded?: boolean;
  query?: string;
  reloadNonce?: number;
} = {}) {
  const { t, text } = useTranslation();
  // The kept list draws at once; the page reads it again behind it.
  const [servers, setServers] = useState<ServerStatus[]>(() => lastKept<ServerStatus[]>(MCP_SERVERS_KEY) ?? []);
  const [ready, setReady] = useState(() => lastKept(MCP_SERVERS_KEY) !== undefined);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  type McpTab = "installed" | "discover";
  const [tab, setTab] = useState<McpTab>("installed");
  const [filter, setFilter] = useState("");
  const filterValue = query !== undefined ? query : filter;

  // ``reload`` only refreshes the server list; it never touches
  // ``selected``. Selection bookkeeping lives in a separate effect
  // below so a transient empty list (e.g. ``restart_server`` briefly
  // empties ``_clients`` between stop and respawn) can't reset the
  // user's selection — the dialog just waits a beat and then snaps back
  // when the server reappears. A failed read keeps the list on screen and
  // is tried again quietly.
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const list = await readUntilAnswered(MCP_SERVERS_KEY, readMcpServers, signal);
      if (signal?.aborted) return;
      setServers(list);
      setReady(true);
    } catch (e) {
      if (!isAbort(e)) console.warn("[mcp] server list read failed", e);
    }
  }, []);
  // A local change (an action's reply) is kept too, for the next visit.
  useEffect(() => { if (ready) keep(MCP_SERVERS_KEY, servers); }, [ready, servers]);

  useEffect(() => {
    if (!reloadNonce) return;
    void reload(lifetime.current?.signal);
  }, [reloadNonce, reload]);

  // ``busy`` shadowed in a ref so the reload effect doesn't re-mount
  // when an action starts. The previous version listed ``busy`` in
  // useEffect's deps — every busy-state flip restarted the effect,
  // which fired an immediate ``void reload()`` and slammed straight
  // into the backend's stop→respawn window, blanking the server list.
  const busyRef = useRef(busy);
  busyRef.current = busy;

  // One AbortController for the page's lifetime; aborting it on cleanup
  // cancels the initial reload and any change-driven one, so we don't
  // ``setServers`` on an unmounted component.
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    lifetime.current = ac;
    void reload(ac.signal);
    return () => {
      ac.abort();
      lifetime.current = null;
    };
  }, [reload]);

  // The Worker says when a server connects, drops, fails or is added or
  // removed. A change during an action waits for it to end: the action
  // upserts its own result, and a list read inside a restart's stop→spawn
  // window would come back empty.
  const changedWhileBusy = useRef(false);
  useChanged("mcp", () => {
    const signal = lifetime.current?.signal;
    if (!signal) return;
    if (busyRef.current !== null) { changedWhileBusy.current = true; return; }
    return reload(signal);
  });
  useEffect(() => {
    if (busy !== null || !changedWhileBusy.current) return;
    changedWhileBusy.current = false;
    const signal = lifetime.current?.signal;
    if (signal) void reload(signal);
  }, [busy, reload]);

  // A server's detail: the kept one at once, then read again; a failed
  // read keeps what is shown and is tried again quietly.
  const fetchDetail = useCallback(
    async (name: string, signal?: AbortSignal) => {
      try {
        const json = await readUntilAnswered(mcpDetailKey(name), () => readMcpDetail(name), signal);
        if (signal?.aborted) return;
        setDetail(json);
        setActionErr(null);
      } catch (e) {
        if (!isAbort(e)) console.warn(`[mcp] server detail read failed: ${name}`, e);
      }
    },
    [],
  );

  // After an action: one read, its failure only warns (the action's own
  // reply is already on screen).
  async function refreshDetail(name: string) {
    try {
      setDetail(await readKept(mcpDetailKey(name), () => readMcpDetail(name)));
    } catch (e) {
      console.warn(`[mcp] server detail read failed: ${name}`, e);
    }
  }

  useEffect(() => {
    if (!selected) return;
    const ac = new AbortController();
    setDetail(lastKept<ServerDetail>(mcpDetailKey(selected)) ?? null);
    void fetchDetail(selected, ac.signal);
    return () => ac.abort();
  }, [selected, fetchDetail]);

  async function runAction(action: Exclude<BusyAction, null>, fn: () => Promise<void>) {
    setBusy(action);
    setActionErr(null);
    try {
      await fn();
    } catch (e) {
      // A restart that failed answers with a code, not a sentence.
      setActionErr((e as Partial<HttpError> | null)?.code === "mcp_runtime_restart_failed"
        ? text("The server could not be restarted.", "这个服务无法重启。")
        : e instanceof Error ? e.message : String(e));
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
      await refreshDetail(name);
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", async () => {
      const server = await jsonFetch<ServerStatus>(`/api/mcp/servers/${encodeURIComponent(name)}/enable`,
        { method: "POST" });
      upsertServer(server);
      await refreshDetail(name);
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", async () => {
      const server = await jsonFetch<ServerStatus>(`/api/mcp/servers/${encodeURIComponent(name)}/disable`,
        { method: "POST" });
      upsertServer(server);
      await refreshDetail(name);
    });
  }
  async function doDelete(name: string) {
    if (!(await confirmAction({
      title: text(`Remove MCP server "${name}"?`, `移除 MCP 服务器“${name}”？`),
      description: text("Config will be deleted.", "配置会被删除。"),
      confirmLabel: text("Remove", "移除"),
      destructive: true,
    }))) return;
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

  const bodyAndDialogs = (
    <>
        <ManageSubnav
          tabs={[
            { id: "installed", label: text("Installed", "已安装"), count: ready ? servers.length : undefined },
            { id: "discover", label: text("Discover", "发现") },
          ]}
          activeTab={tab}
          onTabChange={(id) => setTab(id as McpTab)}
          summary={ready ? text(
            `${readyCount} available · ${issueCount} ${issueCount === 1 ? "issue" : "issues"}`,
            `可用 ${readyCount} 个 · ${issueCount} 个问题`,
          ) : undefined}
          action={{
            label: text("Add MCP server", "添加 MCP 服务器"),
            onClick: openAdd,
            icon: PlusIcon,
            primary: true,
          }}
          ariaLabel={text("MCP sections", "MCP 分区")}
          panelId="mcp-panel"
        />
        <div
          id="mcp-panel"
          role="tabpanel"
          aria-labelledby={`mcp-panel-tab-${tab}`}
          className={shared.panel}
        >
        {actionErr && <div className={shared.errorBar} role="alert">{actionErr}</div>}
        {tab === "discover" && (
          <div className={shared.body}>
            <CatalogPanel
              existingNames={new Set(servers.map((server) => server.name))}
              query={filterValue}
              onInstalled={async (name) => {
                await reload(lifetime.current?.signal);
                setSelected(name);
              }}
            />
          </div>
        )}
        {tab === "installed" && (
        <div className={shared.body}>
            {query === undefined && (
            <div className="mb-3">
              <SearchInput
                value={filter}
                onChange={setFilter}
                placeholder={text("Search servers...", "搜索服务器...")}
              />
            </div>
            )}
            <div className="space-y-1">
            {/* Nothing until the first list arrives: an empty state then would be untrue. */}
            {!ready ? null : shownServers.length === 0 ? (
              filterValue.trim()
                ? <ManageEmptyState compact icon={<SearchIcon size={20} />} title={text("No matching MCP servers", "没有匹配的 MCP 服务器")} description={text("Try a different search.", "换个关键词试试。")} />
                : <ManageEmptyState
                    compact
                    icon={<PlugZapIcon size={20} />}
                    title={text("No MCP servers yet", "还没有 MCP 服务器")}
                    description={text("Add a server, or install one from Discover, to make its tools available.", "添加服务器，或在“发现”中安装，即可使用其工具。")}
                  />
            ) : (
              shownServers.map((s) => {
                const state = stateBadge(s);
                const stateLabel = text(state.label, {
                  ready: "就绪",
                  disabled: "已禁用",
                  error: "错误",
                  starting: "启动中",
                }[state.label] || state.label);
                const description = s.type === "local" ? (s.command || []).join(" ") : s.url;
                return (
                  <ManageRow
                    key={s.name}
                    icon={<PlugZapIcon size={16} />}
                    name={s.name}
                    description={description || s.error || text("No endpoint details", "没有端点信息")}
                    meta={<>
                      <span className={shared.badge}>{s.type}</span>
                      <span className={`${shared.badge} ${s.ready ? shared.badgeGreen : s.error && s.error !== "disabled" ? shared.badgeRed : ""}`}>{stateLabel}</span>
                    </>}
                    count={text(`${s.tool_count} ${s.tool_count === 1 ? "tool" : "tools"}`, `${s.tool_count} 个工具`)}
                    onClick={() => setSelected(s.name)}
                    actions={<Switch
                      checked={s.enabled}
                      disabled={busy !== null}
                      onCheckedChange={(enabled) => { void (enabled ? doEnable(s.name) : doDisable(s.name)); }}
                      aria-label={s.enabled ? text(`Disable ${s.name}`, `禁用 ${s.name}`) : text(`Enable ${s.name}`, `启用 ${s.name}`)}
                    />}
                  />
                );
              })
            )}
            </div>
        </div>
        )}
        </div>

      <Dialog open={selectedServer !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className="max-h-[86vh] overflow-y-auto sm:max-w-[920px]">
          <DialogHeader><DialogTitle className="sr-only">{selectedServer?.name || text("MCP server details", "MCP 服务器详情")}</DialogTitle></DialogHeader>
          {selectedServer && <DetailView
            server={selectedServer}
            detail={detail}
            busy={busy}
            onRestart={() => void doRestart(selectedServer.name)}
            onEnable={() => void doEnable(selectedServer.name)}
            onDisable={() => void doDisable(selectedServer.name)}
            onDelete={() => void doDelete(selectedServer.name)}
            onEdit={() => { const target = selectedServer; setSelected(null); openEdit(target); }}
          />}
        </DialogContent>
      </Dialog>

      {editing !== null && (
        <EditDialog
          target={editing}
          onClose={() => setEditing(null)}
          onSaved={async (newName) => {
            setEditing(null);
            await reload(lifetime.current?.signal);
            if (newName) setSelected(newName);
          }}
        />
      )}

    </>
  );

  if (embedded) return bodyAndDialogs;

  return (
    <div className="main">
      <div className={shared.view}>
        <ManagePageHeader
          title={t("nav.mcp")}
          actions={[
            { label: t("sidebar.refresh"), onClick: () => { void reload(lifetime.current?.signal); } },
          ]}
        />
        {bodyAndDialogs}
      </div>
    </div>
  );
}
