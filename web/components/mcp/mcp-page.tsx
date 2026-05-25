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

  // ``reload`` only refreshes the server list; it never touches
  // ``selected``. Selection bookkeeping lives in a separate effect
  // below so a transient empty list (e.g. ``restart_server`` briefly
  // empties ``_clients`` between stop and respawn) can't reset the
  // user's selection — the right pane just shows "Loading…" for a
  // beat and then snaps back when the server reappears.
  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/mcp/servers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setServers((data.servers as ServerStatus[]) || []);
      setLoadErr(null);
    } catch (e) {
      setLoadErr(String(e));
    } finally {
      setLoading(false);
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
    void reload();
    const t = setInterval(() => {
      if (busyRef.current === null) void reload();
    }, 4000);
    return () => clearInterval(t);
  }, [reload]);

  // Selection bookkeeping — only auto-select on initial load
  // (selected is null and we have servers). Don't auto-reset when
  // a server temporarily disappears.
  useEffect(() => {
    if (selected === null && servers.length > 0) {
      setSelected(servers[0].name);
    }
  }, [servers, selected]);

  const fetchDetail = useCallback(async (name: string) => {
    try {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`);
      if (!r.ok) { setDetail(null); return; }
      setDetail((await r.json()) as ServerDetail);
    } catch {
      setDetail(null);
    }
  }, []);

  useEffect(() => {
    if (selected) { setDetail(null); void fetchDetail(selected); }
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

interface EditTarget {
  mode: "add" | "edit";
  name: string;
  transport: "local" | "http" | "sse";
  // local
  command: string;
  env: string;
  // remote
  url: string;
  headers: string;            // "Key: Value" per line
  authKind: "none" | "bearer" | "oauth";
  bearerToken: string;
  oauthClientName: string;
  oauthScope: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthRedirectPort: number;
  // shared
  enabled: boolean;
  timeout_seconds: number;
  alwaysLoad: boolean;
}

function EditDialog({
  target, onClose, onSaved,
}: {
  target: EditTarget;
  onClose: () => void;
  onSaved: (newName?: string) => void;
}) {
  const [state, setState] = useState<EditTarget>(target);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Tagged busy state so Test and Save buttons each show their own
  // working text — "Testing…" vs "Saving…" — instead of both silently
  // disabling on one shared flag.
  const [busy, setBusy] = useState<null | "save" | "test">(null);
  const saving = busy !== null;
  const isAdd = target.mode === "add";

  function parseKV(text: string, sep: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split(/\n+/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf(sep);
      if (eq < 0) continue;
      out[t.slice(0, eq).trim()] = t.slice(eq + sep.length).trim();
    }
    return out;
  }
  const parseEnv = (s: string) => parseKV(s, "=");
  const parseHeaders = (s: string) => parseKV(s, ":");
  function splitCommand(text: string): string[] {
    return text.trim().split(/\s+/).filter(Boolean);
  }

  function buildBody(): { ok: true; body: Record<string, unknown> } | { ok: false; err: string } {
    if (!state.name.trim()) return { ok: false, err: "name is required" };
    const base = {
      name: state.name.trim(),
      type: state.transport,
      enabled: state.enabled,
      timeout_seconds: state.timeout_seconds,
      always_load: state.alwaysLoad,
    };
    if (state.transport === "local") {
      const cmd = splitCommand(state.command);
      if (cmd.length === 0) return { ok: false, err: "command is required" };
      return { ok: true, body: { ...base, command: cmd, env: parseEnv(state.env) } };
    }
    if (!state.url.trim()) return { ok: false, err: "url is required" };
    let auth: Record<string, unknown>;
    if (state.authKind === "bearer") {
      if (!state.bearerToken.trim())
        return { ok: false, err: "bearer token is required" };
      auth = { kind: "bearer", token: state.bearerToken.trim() };
    } else if (state.authKind === "oauth") {
      auth = {
        kind: "oauth",
        client_name: state.oauthClientName.trim() || "OpenProgram",
        redirect_port: state.oauthRedirectPort || 0,
      };
      if (state.oauthScope.trim()) auth.scope = state.oauthScope.trim();
      if (state.oauthClientId.trim()) auth.client_id = state.oauthClientId.trim();
      if (state.oauthClientSecret.trim())
        auth.client_secret = state.oauthClientSecret.trim();
    } else {
      auth = { kind: "none" };
    }
    return {
      ok: true,
      body: {
        ...base,
        url: state.url.trim(),
        headers: parseHeaders(state.headers),
        auth,
      },
    };
  }

  async function save() {
    setErr(null); setNote(null);
    const built = buildBody();
    if (!built.ok) { setErr(built.err); return; }
    const body = built.body;
    setBusy("save");
    try {
      const r = isAdd
        ? await fetch("/api/mcp/servers", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          })
        : await fetch(`/api/mcp/servers/${encodeURIComponent(state.name)}`, {
            method: "PATCH", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setErr(d.detail || `HTTP ${r.status}`);
        return;
      }
      onSaved(state.name.trim());
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function testRun() {
    setErr(null); setNote(null);
    const built = buildBody();
    if (!built.ok) { setErr(`${built.err} (to test)`); return; }
    const body = { ...built.body, name: state.name || "test", enabled: true };
    setBusy("test");
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr(`test failed: ${data.error || data.detail || `HTTP ${r.status}`}`);
        return;
      }
      setNote(`✓ test ok — ${data.tool_count} tool(s): ${data.tools.join(", ")}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={true} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {isAdd ? "Add MCP server" : `Edit ${target.name}`}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-name">Name</Label>
            <Input
              id="mcp-name"
              value={state.name}
              disabled={!isAdd}
              onChange={(e) => setState({ ...state, name: e.target.value })}
              placeholder="drawio"
              className="font-mono"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-transport">Transport</Label>
            <select
              id="mcp-transport"
              value={state.transport}
              onChange={(e) =>
                setState({ ...state, transport: e.target.value as EditTarget["transport"] })}
              className="flex h-10 rounded-md border bg-background px-3 font-mono text-sm
                          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="local">local (stdio subprocess)</option>
              <option value="http">http (Streamable HTTP)</option>
              <option value="sse">sse (legacy Server-Sent Events)</option>
            </select>
          </div>

          {state.transport === "local" ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-cmd">Command</Label>
                <Input
                  id="mcp-cmd"
                  value={state.command}
                  onChange={(e) => setState({ ...state, command: e.target.value })}
                  placeholder="npx -y @drawio/mcp"
                  className="font-mono"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-env">Environment (KEY=VALUE per line)</Label>
                <textarea
                  id="mcp-env"
                  value={state.env}
                  onChange={(e) => setState({ ...state, env: e.target.value })}
                  placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=ghp_..."
                  className="flex min-h-[100px] rounded-md border bg-background px-3 py-2 font-mono text-sm
                              placeholder:text-muted-foreground
                              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
            </>
          ) : (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-url">URL</Label>
                <Input
                  id="mcp-url"
                  value={state.url}
                  onChange={(e) => setState({ ...state, url: e.target.value })}
                  placeholder="https://mcp.example.com/mcp"
                  className="font-mono"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-headers">Headers (Key: Value per line)</Label>
                <textarea
                  id="mcp-headers"
                  value={state.headers}
                  onChange={(e) => setState({ ...state, headers: e.target.value })}
                  placeholder="X-Tenant: acme"
                  className="flex min-h-[60px] rounded-md border bg-background px-3 py-2 font-mono text-sm
                              placeholder:text-muted-foreground
                              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-auth">Authentication</Label>
                <select
                  id="mcp-auth"
                  value={state.authKind}
                  onChange={(e) =>
                    setState({ ...state, authKind: e.target.value as EditTarget["authKind"] })}
                  className="flex h-10 rounded-md border bg-background px-3 font-mono text-sm
                              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="none">none</option>
                  <option value="bearer">bearer token (static)</option>
                  <option value="oauth">OAuth 2.1 (browser flow)</option>
                </select>
              </div>

              {state.authKind === "bearer" && (
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="mcp-bearer">Bearer token</Label>
                  <Input
                    id="mcp-bearer"
                    type="password"
                    value={state.bearerToken}
                    onChange={(e) => setState({ ...state, bearerToken: e.target.value })}
                    placeholder="paste token"
                    className="font-mono"
                  />
                </div>
              )}

              {state.authKind === "oauth" && (
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-name">Client name</Label>
                    <Input
                      id="mcp-oa-name"
                      value={state.oauthClientName}
                      onChange={(e) =>
                        setState({ ...state, oauthClientName: e.target.value })}
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-scope">Scope (optional)</Label>
                    <Input
                      id="mcp-oa-scope"
                      value={state.oauthScope}
                      onChange={(e) => setState({ ...state, oauthScope: e.target.value })}
                      placeholder="read write"
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-cid">Client ID (optional)</Label>
                    <Input
                      id="mcp-oa-cid"
                      value={state.oauthClientId}
                      onChange={(e) =>
                        setState({ ...state, oauthClientId: e.target.value })}
                      placeholder="leave blank for dynamic registration"
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-csec">Client secret (optional)</Label>
                    <Input
                      id="mcp-oa-csec"
                      type="password"
                      value={state.oauthClientSecret}
                      onChange={(e) =>
                        setState({ ...state, oauthClientSecret: e.target.value })}
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-port">Redirect port (0 = auto)</Label>
                    <Input
                      id="mcp-oa-port"
                      type="number"
                      min={0}
                      max={65535}
                      value={state.oauthRedirectPort}
                      onChange={(e) =>
                        setState({ ...state, oauthRedirectPort: Number(e.target.value) || 0 })}
                      className="font-mono"
                    />
                  </div>
                </div>
              )}
            </>
          )}

          <div className="flex items-center gap-4">
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="mcp-timeout">Timeout (s)</Label>
              <Input
                id="mcp-timeout" type="number"
                value={state.timeout_seconds}
                min={1} max={300}
                onChange={(e) =>
                  setState({ ...state, timeout_seconds: Number(e.target.value) || 30 })}
              />
            </div>
            <div className="flex items-center gap-2 pt-7">
              <Switch
                id="mcp-enabled"
                checked={state.enabled}
                onCheckedChange={(c) => setState({ ...state, enabled: c })}
              />
              <Label htmlFor="mcp-enabled" className="cursor-pointer">Enabled</Label>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border p-3"
               style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
            <Switch
              id="mcp-always-load"
              checked={state.alwaysLoad}
              onCheckedChange={(c) => setState({ ...state, alwaysLoad: c })}
            />
            <div className="flex-1">
              <Label htmlFor="mcp-always-load" className="cursor-pointer">
                Always load tool schemas (skip deferred-loading)
              </Label>
              <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
                Off (default): tools appear by name in a system-prompt
                catalog; the model loads schemas on demand via
                <code className="mx-1">tool_search</code>, saving tokens
                when this server has many tools. On: every tool ships
                its full JSON Schema with each LLM request — flip this
                for small focused servers (3-5 tools) the model uses
                every turn.
              </div>
            </div>
          </div>

          {err && (
            <div className="rounded-md border p-2 font-mono text-xs"
                 style={{ borderColor: "var(--accent-red)", color: "var(--accent-red)" }}>
              {err}
            </div>
          )}
          {note && (
            <div className="rounded-md border p-2 font-mono text-xs"
                 style={{ borderColor: "var(--accent-green)", color: "var(--accent-green)" }}>
              {note}
            </div>
          )}
        </div>

        <DialogFooter>
          <button
            className={styles.actionBtn}
            onClick={() => void testRun()}
            disabled={saving}
          >
            {busy === "test" ? "Testing…" : "Test"}
          </button>
          <button className={styles.actionBtn} onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className={cn(styles.actionBtn, styles.actionBtnPrimary)}
            onClick={() => void save()}
            disabled={saving}
          >
            {busy === "save" ? "Saving…" : isAdd ? "Add" : "Save"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
