"use client";

/**
 * /mcp — MCP server management page (master / detail).
 *
 * All UI built from shadcn primitives in @/components/ui — no
 * bespoke CSS module. Disabled / ready / starting / error states
 * share the same layout; only the action button (Enable vs Disable),
 * the state badge, and an inline disabled-hint differ.
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
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

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

function stateLabel(s: ServerStatus): {
  text: string;
  variant: "default" | "secondary" | "destructive" | "outline";
  dot: string;
} {
  if (s.ready) return { text: "ready",    variant: "default",     dot: "bg-emerald-500" };
  if (s.error === "disabled")
    return       { text: "disabled", variant: "secondary",   dot: "bg-slate-400" };
  if (s.error) return { text: "error",    variant: "destructive", dot: "bg-red-500" };
  return         { text: "starting", variant: "outline",     dot: "bg-yellow-400" };
}

export function McpPage() {
  const [servers, setServers] = useState<ServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/mcp/servers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const list = (data.servers as ServerStatus[]) || [];
      setServers(list);
      setLoadErr(null);
      if (selected === null && list.length > 0) setSelected(list[0].name);
      if (selected !== null && !list.some((s) => s.name === selected)) {
        setSelected(list[0]?.name ?? null);
      }
    } catch (e) {
      setLoadErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    void reload();
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [reload]);

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
    if (selected) {
      setDetail(null);
      void fetchDetail(selected);
    }
  }, [selected, fetchDetail]);

  async function runAction(action: Exclude<BusyAction, null>, fn: () => Promise<void>) {
    setBusy(action);
    try { await fn(); } finally { setBusy(null); }
  }

  async function doRestart(name: string) {
    await runAction("restart", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/restart`, { method: "POST" });
      await reload();
      await fetchDetail(name);
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/enable`, { method: "POST" });
      await reload();
      await fetchDetail(name);
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/disable`, { method: "POST" });
      await reload();
      await fetchDetail(name);
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
    <div className="flex h-full flex-col overflow-hidden">
      {/* Topbar */}
      <div className="flex h-16 flex-shrink-0 items-center gap-4 border-b px-6">
        <span className="text-lg font-semibold">MCP Servers</span>
        <span className="truncate text-sm text-muted-foreground">
          External tool processes — config at <code className="font-mono text-xs">~/.agentic/mcp_servers.json</code>
        </span>
        <div className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            Refresh
          </Button>
          <Button size="sm" onClick={openAdd}>+ Add server</Button>
        </div>
      </div>

      {/* Body: left nav + right detail */}
      <div className="grid min-h-0 flex-1 [grid-template-columns:calc(var(--sidebar-width)-1px)_1fr]">
        {/* Left nav */}
        <aside className="flex flex-col gap-1 overflow-y-auto border-r bg-secondary/40 p-2">
          {loadErr && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 p-2 text-xs text-red-400">
              {loadErr}
            </div>
          )}
          {loading && servers.length === 0 ? (
            <div className="px-2 py-1 text-sm text-muted-foreground">Loading…</div>
          ) : servers.length === 0 ? (
            <div className="px-2 py-1 text-sm text-muted-foreground">No servers</div>
          ) : (
            servers.map((s) => {
              const { dot } = stateLabel(s);
              const active = selected === s.name;
              return (
                <button
                  key={s.name}
                  onClick={() => setSelected(s.name)}
                  className={cn(
                    "flex h-8 items-center gap-2 rounded-md px-2 text-sm transition-colors",
                    active
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                  )}
                >
                  <span className={cn("h-2 w-2 flex-shrink-0 rounded-full", dot)} />
                  <span className="flex-1 truncate text-left font-mono">{s.name}</span>
                  <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground">
                    {s.tool_count}
                  </span>
                </button>
              );
            })
          )}

          <Separator className="my-2" />
          <Button variant="ghost" size="sm" className="justify-start" onClick={openAdd}>
            + Add server
          </Button>
        </aside>

        {/* Right detail */}
        <div className="min-w-0 overflow-y-auto p-6">
          {selectedServer === null ? (
            <div className="flex h-full items-center justify-center text-center text-muted-foreground">
              <div>
                <div className="mb-4 text-5xl opacity-50">🔌</div>
                <div>Select a server on the left to view tools and settings.</div>
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
  const st = stateLabel(server);
  const isBusy = busy !== null;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-lg">
          🔌
        </div>
        <span className="font-mono text-lg font-semibold text-primary">{server.name}</span>
        <Badge variant={st.variant} className="uppercase">{st.text}</Badge>

        <div className="ml-auto flex gap-2">
          {server.enabled ? (
            <Button variant="outline" size="sm" onClick={onDisable} disabled={isBusy}>
              {busy === "disable" ? "Disabling…" : "Disable"}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={onEnable} disabled={isBusy}>
              {busy === "enable" ? "Enabling…" : "Enable"}
            </Button>
          )}
          <Button
            variant="outline" size="sm"
            onClick={onRestart}
            disabled={isBusy || !server.enabled}
          >
            {busy === "restart" ? "Restarting…" : "Restart"}
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit} disabled={isBusy}>
            Edit
          </Button>
          <Button variant="outline" size="sm" onClick={onDelete} disabled={isBusy}
            className="text-red-400 hover:text-red-300 hover:border-red-400">
            {busy === "delete" ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>

      {server.error && server.error !== "disabled" && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 font-mono text-xs text-red-400">
          {server.error}
        </div>
      )}

      <ConfigChips server={server} />

      {Object.keys(server.env).length > 0 && (
        <div className="rounded-md border bg-secondary/40 p-3 font-mono text-xs">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Environment
          </div>
          {Object.entries(server.env).map(([k, v]) => (
            <div key={k}>{k}=<span className="text-foreground">{v}</span></div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 pt-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Tools
        </span>
        <Badge variant="secondary" className="px-2 py-0 text-[10px]">
          {server.tool_count}
        </Badge>
        {!server.enabled && (
          <span className="text-xs text-muted-foreground">
            (server disabled — enable to load tool list)
          </span>
        )}
      </div>

      {!detail ? (
        <div className="text-sm text-muted-foreground">Loading tools…</div>
      ) : (detail.tool_schemas || []).length === 0 ? (
        <div className="text-sm text-muted-foreground">
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

function ConfigChips({ server }: { server: ServerStatus }) {
  const chips: Array<[string, React.ReactNode]> = [
    ["Transport", server.type],
    ["Command", <code key="cmd" className="rounded bg-background px-1.5 py-0.5">{server.command.join(" ")}</code>],
    ["Timeout", `${server.timeout_seconds}s`],
    ["Prefix", <code key="pfx" className="rounded bg-background px-1.5 py-0.5">{server.name}__</code>],
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-md border bg-secondary/40 px-3 py-2 text-xs">
      {chips.map(([k, v], i) => (
        <span key={k} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-muted-foreground/40">·</span>}
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {k}
          </span>
          <span className="font-mono text-foreground">{v}</span>
        </span>
      ))}
    </div>
  );
}

function ToolRow({ server, tool }: { server: string; tool: ToolSchema }) {
  return (
    <div className="rounded-md border bg-secondary/40 px-3 py-2 transition-colors hover:border-primary hover:bg-secondary">
      <div className="truncate font-mono text-sm font-medium text-primary">
        {server}__{tool.name}
      </div>
      <div className="mt-0.5 truncate text-xs text-muted-foreground">
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
      name: state.name.trim(),
      type: "local",
      command: cmd,
      env: parseEnv(state.env),
      enabled: state.enabled,
      timeout_seconds: state.timeout_seconds,
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
    <Dialog open={true} onOpenChange={(open) => { if (!open) onClose(); }}>
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
            <p className="text-[11px] text-muted-foreground">
              Tool prefix — exposed to the LLM as <code>&lt;name&gt;__&lt;tool&gt;</code>.
            </p>
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
            <p className="text-[11px] text-muted-foreground">
              Whitespace-separated. Resolved against worker&apos;s $PATH.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-env">Environment (KEY=VALUE per line)</Label>
            <textarea
              id="mcp-env"
              value={state.env}
              onChange={(e) => setState({ ...state, env: e.target.value })}
              placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=ghp_..."
              className="flex min-h-[100px] rounded-md border border-input bg-background px-3 py-2 font-mono text-sm
                          ring-offset-background placeholder:text-muted-foreground
                          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="mcp-timeout">Timeout (s)</Label>
              <Input
                id="mcp-timeout"
                type="number"
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
              <Label htmlFor="mcp-enabled" className="cursor-pointer">
                Enabled
              </Label>
            </div>
          </div>

          {err && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 p-2 font-mono text-xs text-red-400">
              {err}
            </div>
          )}
          {note && (
            <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 font-mono text-xs text-emerald-400">
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
