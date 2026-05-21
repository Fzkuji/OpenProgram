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

import { useCallback, useEffect, useState } from "react";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import styles from "./mcp-page.module.css";

interface ServerStatus {
  name: string;
  type: string;
  command: string[];
  env: Record<string, string>;
  enabled: boolean;
  timeout_seconds: number;
  ready: boolean;
  error: string | null;
  tool_count: number;
  tools: string[];
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

  useEffect(() => {
    void reload();
    // Pause polling while an action is in flight so we don't race the
    // backend's stop→spawn window mid-restart.
    const t = setInterval(() => {
      if (busy === null) void reload();
    }, 4000);
    return () => clearInterval(t);
  }, [reload, busy]);

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

  async function doRestart(name: string) {
    await runAction("restart", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/restart`, { method: "POST" });
      await reload(); await fetchDetail(name);
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/enable`, { method: "POST" });
      await reload(); await fetchDetail(name);
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/disable`, { method: "POST" });
      await reload(); await fetchDetail(name);
    });
  }
  async function doDelete(name: string) {
    if (!confirm(`Remove MCP server "${name}"? Config will be deleted.`)) return;
    await runAction("delete", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      await reload();
      if (selected === name) setSelected(null);
    });
  }

  function openEdit(s: ServerStatus) {
    setEditing({
      mode: "edit", name: s.name,
      command: s.command.join(" "),
      env: Object.entries(s.env).map(([k, v]) => `${k}=${v}`).join("\n"),
      enabled: s.enabled,
      timeout_seconds: s.timeout_seconds,
    });
  }
  function openAdd() {
    setEditing({
      mode: "add", name: "",
      command: "npx -y @modelcontextprotocol/server-...",
      env: "",
      enabled: true,
      timeout_seconds: 30,
    });
  }

  const selectedServer = servers.find((s) => s.name === selected) || null;

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>MCP</span>
          <div className={styles.toolbar}>
            <Button variant="secondary" size="sm" onClick={() => void reload()}>
              Refresh
            </Button>
            <Button size="sm" onClick={openAdd}>+ Add server</Button>
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
            <Button variant="secondary" size="sm" onClick={onDisable} disabled={isBusy}>
              {busy === "disable" ? "Disabling…" : "Disable"}
            </Button>
          ) : (
            <Button variant="secondary" size="sm" onClick={onEnable} disabled={isBusy}>
              {busy === "enable" ? "Enabling…" : "Enable"}
            </Button>
          )}
          <Button variant="secondary" size="sm"
                  onClick={onRestart} disabled={isBusy || !server.enabled}>
            {busy === "restart" ? "Restarting…" : "Restart"}
          </Button>
          <Button variant="secondary" size="sm" onClick={onEdit} disabled={isBusy}>
            Edit
          </Button>
          <Button variant="destructive" size="sm" onClick={onDelete} disabled={isBusy}>
            {busy === "delete" ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>

      {server.error && server.error !== "disabled" && (
        <div className="rounded-md border p-3 font-mono text-xs"
             style={{ borderColor: "var(--accent-red)", color: "var(--accent-red)" }}>
          {server.error}
        </div>
      )}

      {/* Config */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-2 text-xs"
           style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
        <ConfigChip k="Transport" v={server.type} />
        <Sep />
        <ConfigChip k="Command" v={<code>{server.command.join(" ")}</code>} />
        <Sep />
        <ConfigChip k="Timeout" v={`${server.timeout_seconds}s`} />
        <Sep />
        <ConfigChip k="Prefix" v={<code>{server.name}__</code>} />
      </div>

      {Object.keys(server.env).length > 0 && (
        <div className="rounded-md border p-3 font-mono text-xs"
             style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider"
               style={{ color: "var(--text-muted)" }}>
            Environment
          </div>
          {Object.entries(server.env).map(([k, v]) => (
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
  command: string;
  env: string;
  enabled: boolean;
  timeout_seconds: number;
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
  const [saving, setSaving] = useState(false);
  const isAdd = target.mode === "add";

  function parseEnv(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split(/\n+/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq < 0) continue;
      out[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
    }
    return out;
  }
  function splitCommand(text: string): string[] {
    return text.trim().split(/\s+/).filter(Boolean);
  }

  async function save() {
    setErr(null); setNote(null);
    if (!state.name.trim()) { setErr("name is required"); return; }
    const cmd = splitCommand(state.command);
    if (cmd.length === 0) { setErr("command is required"); return; }
    const body = {
      name: state.name.trim(), type: "local", command: cmd,
      env: parseEnv(state.env),
      enabled: state.enabled, timeout_seconds: state.timeout_seconds,
    };
    setSaving(true);
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
      onSaved(body.name);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function testRun() {
    setErr(null); setNote(null);
    const cmd = splitCommand(state.command);
    if (cmd.length === 0) { setErr("command is required to test"); return; }
    setSaving(true);
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.name || "test", type: "local", command: cmd,
          env: parseEnv(state.env), enabled: true,
          timeout_seconds: state.timeout_seconds,
        }),
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
      setSaving(false);
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
          <Button variant="outline" onClick={() => void testRun()} disabled={saving}>
            Test
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : isAdd ? "Add" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
