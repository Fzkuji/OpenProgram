"use client";

import { useCallback, useEffect, useState } from "react";
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
  registered_tool_names?: string[];
}

function cls(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

function statePill(s: ServerStatus): { label: string; cls: string } {
  if (s.ready) return { label: "ready", cls: styles.stateReady };
  if (s.error === "disabled") return { label: "disabled", cls: styles.stateDisabled };
  if (s.error) return { label: "error", cls: styles.stateError };
  return { label: "starting", cls: styles.stateStarting };
}

export function McpPage() {
  const [servers, setServers] = useState<ServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/mcp/servers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setServers((data.servers as ServerStatus[]) || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [reload]);

  const fetchDetail = useCallback(async (name: string) => {
    try {
      const r = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`);
      if (!r.ok) {
        setDetail({
          ...(servers.find((s) => s.name === name) as ServerStatus),
          tool_schemas: [],
        });
        return;
      }
      const data = await r.json();
      setDetail(data as ServerDetail);
    } catch {
      // ignore
    }
  }, [servers]);

  function toggleExpand(name: string) {
    if (expanded === name) {
      setExpanded(null);
      setDetail(null);
    } else {
      setExpanded(name);
      setDetail(null);
      void fetchDetail(name);
    }
  }

  async function withBusy(name: string, fn: () => Promise<void>) {
    setBusy(name);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  async function doRestart(name: string) {
    await withBusy(name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/restart`,
        { method: "POST" });
      await reload();
      if (expanded === name) await fetchDetail(name);
    });
  }
  async function doEnable(name: string) {
    await withBusy(name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/enable`,
        { method: "POST" });
      await reload();
    });
  }
  async function doDisable(name: string) {
    await withBusy(name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/disable`,
        { method: "POST" });
      await reload();
    });
  }
  async function doDelete(name: string) {
    if (!confirm(`Remove MCP server "${name}"? Config will be deleted.`)) return;
    await withBusy(name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`,
        { method: "DELETE" });
      await reload();
      if (expanded === name) {
        setExpanded(null);
        setDetail(null);
      }
    });
  }

  function openEdit(s: ServerStatus) {
    setEditing({
      mode: "edit",
      name: s.name,
      command: s.command.join(" "),
      env: Object.entries(s.env).map(([k, v]) => `${k}=${v}`).join("\n"),
      enabled: s.enabled,
      timeout_seconds: s.timeout_seconds,
    });
  }
  function openAdd() {
    setEditing({
      mode: "add",
      name: "",
      command: "npx -y @modelcontextprotocol/server-...",
      env: "",
      enabled: true,
      timeout_seconds: 30,
    });
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>MCP Servers</div>
          <div className={styles.subtitle}>
            外部 MCP 进程提供的工具,LLM 自动调用。配置保存在 <code>~/.agentic/mcp_servers.json</code>。
          </div>
        </div>
        <div className={styles.actions}>
          <button className={styles.btn} onClick={() => void reload()}>
            Refresh
          </button>
          <button
            className={cls(styles.btn, styles.btnPrimary)}
            onClick={openAdd}
          >
            + Add server
          </button>
        </div>
      </div>

      {error && <div className={styles.errorBox}>load failed: {error}</div>}

      {loading ? (
        <div className={styles.emptyState}>Loading…</div>
      ) : servers.length === 0 ? (
        <div className={styles.emptyState}>
          No MCP servers configured. Click <b>+ Add server</b> to add one.
        </div>
      ) : (
        <div className={styles.list}>
          {servers.map((s) => (
            <ServerRow
              key={s.name}
              s={s}
              expanded={expanded === s.name}
              detail={expanded === s.name ? detail : null}
              busy={busy === s.name}
              onToggle={() => toggleExpand(s.name)}
              onRestart={() => void doRestart(s.name)}
              onEnable={() => void doEnable(s.name)}
              onDisable={() => void doDisable(s.name)}
              onDelete={() => void doDelete(s.name)}
              onEdit={() => openEdit(s)}
            />
          ))}
        </div>
      )}

      {editing !== null && (
        <EditDialog
          target={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void reload();
          }}
        />
      )}
    </div>
  );
}

function ServerRow({
  s, expanded, detail, busy,
  onToggle, onRestart, onEnable, onDisable, onDelete, onEdit,
}: {
  s: ServerStatus;
  expanded: boolean;
  detail: ServerDetail | null;
  busy: boolean;
  onToggle: () => void;
  onRestart: () => void;
  onEnable: () => void;
  onDisable: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const { label, cls: stateCls } = statePill(s);
  return (
    <div className={styles.serverCard}>
      <div className={styles.serverHeaderRow}>
        <span className={styles.serverName}>{s.name}</span>
        <span className={cls(styles.statePill, stateCls)}>{label}</span>
        <div className={styles.serverMeta}>
          <span className={styles.serverCommand}>{s.command.join(" ")}</span>
          {" · "}
          {s.tool_count} tool{s.tool_count === 1 ? "" : "s"}
        </div>
        <div className={styles.serverActions}>
          <button
            className={styles.iconBtn}
            onClick={onToggle}
            disabled={busy}
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "▾" : "▸"}
          </button>
          {s.enabled ? (
            <button
              className={styles.iconBtn}
              onClick={onDisable}
              disabled={busy}
              title="Disable (stop)"
            >
              Disable
            </button>
          ) : (
            <button
              className={styles.iconBtn}
              onClick={onEnable}
              disabled={busy}
              title="Enable (start)"
            >
              Enable
            </button>
          )}
          <button
            className={styles.iconBtn}
            onClick={onRestart}
            disabled={busy || !s.enabled}
            title="Restart"
          >
            Restart
          </button>
          <button
            className={styles.iconBtn}
            onClick={onEdit}
            disabled={busy}
            title="Edit config"
          >
            Edit
          </button>
          <button
            className={cls(styles.iconBtn, styles.danger)}
            onClick={onDelete}
            disabled={busy}
            title="Delete"
          >
            Delete
          </button>
        </div>
      </div>

      {s.error && s.error !== "disabled" && (
        <div className={styles.errorBox}>{s.error}</div>
      )}

      {expanded && (
        <div className={styles.toolList}>
          {!detail ? (
            <div className={styles.subtitle}>Loading tools…</div>
          ) : (detail.tool_schemas || []).length === 0 ? (
            <div className={styles.subtitle}>
              {s.ready ? "No tools exposed." : "Server not ready — no tool list yet."}
            </div>
          ) : (
            (detail.tool_schemas || []).map((t) => (
              <ToolItem key={t.name} server={s.name} tool={t} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ToolItem({ server, tool }: { server: string; tool: ToolSchema }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <div className={styles.toolRow} onClick={() => setShow((v) => !v)}>
        <div>
          <div className={styles.toolName}>{server}__{tool.name}</div>
        </div>
        <div className={styles.toolDesc}>{tool.description || tool.title || "—"}</div>
      </div>
      {show && (
        <div className={styles.toolSchema}>
          {JSON.stringify(tool.input_schema, null, 2)}
        </div>
      )}
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
  onSaved: () => void;
}) {
  const [state, setState] = useState<EditTarget>(target);
  const [err, setErr] = useState<string | null>(null);
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
    setErr(null);
    if (!state.name.trim()) {
      setErr("name is required");
      return;
    }
    const cmd = splitCommand(state.command);
    if (cmd.length === 0) {
      setErr("command is required");
      return;
    }
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
        ? await fetch("/api/mcp/servers",
            { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body) })
        : await fetch(`/api/mcp/servers/${encodeURIComponent(state.name)}`,
            { method: "PATCH", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body) });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        setErr(detail.detail || `HTTP ${r.status}`);
        return;
      }
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function testRun() {
    setErr(null);
    const cmd = splitCommand(state.command);
    if (cmd.length === 0) {
      setErr("command is required to test");
      return;
    }
    setSaving(true);
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.name || "test",
          type: "local",
          command: cmd,
          env: parseEnv(state.env),
          enabled: true,
          timeout_seconds: state.timeout_seconds,
        }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr(`test failed: ${data.error || data.detail || `HTTP ${r.status}`}`);
        return;
      }
      setErr(`✓ test ok — ${data.tool_count} tool(s): ${data.tools.join(", ")}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div className={styles.modalTitle}>
            {isAdd ? "Add MCP server" : `Edit ${target.name}`}
          </div>
          <button className={styles.iconBtn} onClick={onClose}>✕</button>
        </div>

        <div className={styles.modalBody}>
          <div className={styles.field}>
            <label className={styles.fieldLabel}>Name</label>
            <input
              className={styles.fieldInput}
              value={state.name}
              disabled={!isAdd}
              onChange={(e) => setState({ ...state, name: e.target.value })}
              placeholder="drawio"
            />
            <span className={styles.fieldHint}>
              Tool prefix — exposed to the LLM as <code>&lt;name&gt;__&lt;tool&gt;</code>.
            </span>
          </div>

          <div className={styles.field}>
            <label className={styles.fieldLabel}>Command</label>
            <input
              className={styles.fieldInput}
              value={state.command}
              onChange={(e) => setState({ ...state, command: e.target.value })}
              placeholder="npx -y @drawio/mcp"
            />
            <span className={styles.fieldHint}>
              Whitespace-separated. Run with current $PATH; if it requires <code>npx</code>,
              install Node first.
            </span>
          </div>

          <div className={styles.field}>
            <label className={styles.fieldLabel}>Environment (KEY=VALUE per line)</label>
            <textarea
              className={styles.fieldTextarea}
              value={state.env}
              onChange={(e) => setState({ ...state, env: e.target.value })}
              placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=ghp_..."
            />
            <span className={styles.fieldHint}>
              Inherits the worker&apos;s environment. Anything here adds / overrides.
            </span>
          </div>

          <div className={styles.field}>
            <label className={styles.fieldLabel}>Startup / per-call timeout (s)</label>
            <input
              className={styles.fieldInput}
              type="number"
              value={state.timeout_seconds}
              onChange={(e) => setState({
                ...state,
                timeout_seconds: Number(e.target.value) || 30,
              })}
              min={1}
              max={300}
            />
          </div>

          <div className={styles.field}>
            <label className={styles.fieldLabel}>
              <input
                type="checkbox"
                checked={state.enabled}
                onChange={(e) => setState({ ...state, enabled: e.target.checked })}
              />{" "}
              Enabled (spawn on save)
            </label>
          </div>

          {err && <div className={styles.errorBox}>{err}</div>}
        </div>

        <div className={styles.modalFooter}>
          <button className={styles.btn} onClick={() => void testRun()}
            disabled={saving}>
            Test
          </button>
          <button className={styles.btn} onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className={cls(styles.btn, styles.btnPrimary)}
            onClick={() => void save()}
            disabled={saving}
          >
            {saving ? "Saving…" : isAdd ? "Add" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
