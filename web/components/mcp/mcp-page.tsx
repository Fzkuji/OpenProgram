"use client";

/**
 * /mcp — MCP server management page (master / detail).
 *
 * Layout parity with /functions:
 *   .view (full height flex col)
 *     .topbar (64px sticky, title + actions)
 *     .body grid: .serversNav (left, server list) + .detail (right)
 *
 * Click a server in the left nav → its config + tool schemas render
 * on the right with action buttons (Restart / Edit / Enable / Disable
 * / Delete). + Add server at the bottom of the nav opens the modal.
 */

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
}

function cls(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

function statePill(s: ServerStatus) {
  if (s.ready) return { label: "ready", pill: styles.stateReady, dot: styles.dotReady };
  if (s.error === "disabled")
    return { label: "disabled", pill: styles.stateDisabled, dot: styles.dotDisabled };
  if (s.error) return { label: "error", pill: styles.stateError, dot: styles.dotError };
  return { label: "starting", pill: styles.stateStarting, dot: styles.dotStarting };
}

export function McpPage() {
  const [servers, setServers] = useState<ServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  // ``busy`` is now a tagged action so we can show "Enabling…" /
  // "Restarting…" instead of a silent disabled button.
  const [busy, setBusy] = useState<null | "enable" | "disable" | "restart" | "delete">(null);

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/mcp/servers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const list = (data.servers as ServerStatus[]) || [];
      setServers(list);
      setLoadErr(null);
      // First load: auto-select first server.
      if (selected === null && list.length > 0) {
        setSelected(list[0].name);
      }
      // Drop selection if it disappeared.
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
      if (!r.ok) {
        setDetail(null);
        return;
      }
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

  async function runAction(
    action: "enable" | "disable" | "restart" | "delete",
    name: string,
    fn: () => Promise<void>,
  ) {
    setBusy(action);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  async function doRestart(name: string) {
    await runAction("restart", name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/restart`,
        { method: "POST" });
      await reload();
      await fetchDetail(name);
    });
  }
  async function doEnable(name: string) {
    await runAction("enable", name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/enable`,
        { method: "POST" });
      await reload();
      await fetchDetail(name);
    });
  }
  async function doDisable(name: string) {
    await runAction("disable", name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}/disable`,
        { method: "POST" });
      await reload();
      await fetchDetail(name);
    });
  }
  async function doDelete(name: string) {
    if (!confirm(`Remove MCP server "${name}"? Config will be deleted.`)) return;
    await runAction("delete", name, async () => {
      await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`,
        { method: "DELETE" });
      await reload();
      if (selected === name) setSelected(null);
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

  const selectedServer = servers.find((s) => s.name === selected) || null;

  return (
    <div className={styles.view}>
      <div className={styles.topbar}>
        <span className={styles.title}>MCP Servers</span>
        <span className={styles.subtitle}>
          External tool processes — config at <code>~/.agentic/mcp_servers.json</code>
        </span>
        <div className={styles.toolbar}>
          <button className={styles.iconBtn} onClick={() => void reload()}>
            Refresh
          </button>
          <button
            className={cls(styles.iconBtn, styles.primary)}
            onClick={openAdd}
          >
            + Add server
          </button>
        </div>
      </div>

      <div className={styles.body}>
        <div className={styles.serversNav}>
          {loadErr && <div className={styles.errorBox}>{loadErr}</div>}

          {loading && servers.length === 0 ? (
            <div className={styles.serverItem} style={{ color: "var(--text-muted)" }}>
              Loading…
            </div>
          ) : servers.length === 0 ? (
            <div className={styles.serverItem} style={{ color: "var(--text-muted)" }}>
              No servers
            </div>
          ) : (
            servers.map((s) => {
              const { dot } = statePill(s);
              return (
                <div
                  key={s.name}
                  className={cls(
                    styles.serverItem,
                    selected === s.name && styles.active,
                  )}
                  onClick={() => setSelected(s.name)}
                >
                  <span className={cls(styles.serverDot, dot)} />
                  <span className={styles.serverNavName}>{s.name}</span>
                  <span className={styles.serverNavCount}>{s.tool_count}</span>
                </div>
              );
            })
          )}

          <div className={styles.navSep} />
          <button className={styles.navAddBtn} onClick={openAdd}>
            <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
            Add server
          </button>
        </div>

        <div className={styles.detail}>
          {selectedServer === null ? (
            <div className={styles.detailEmpty}>
              <div>
                <div className={styles.detailEmptyIcon}>🔌</div>
                <div>Select a server on the left to view tools and settings.</div>
                <div style={{ marginTop: 12, fontSize: 13 }}>
                  Or click <b>+ Add server</b> to attach a new one.
                </div>
              </div>
            </div>
          ) : (
            <DetailView
              server={selectedServer}
              detail={detail}
              busyAction={busy}
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
            // Reload BEFORE selecting so the new entry is in
            // ``servers`` by the time the right pane re-renders —
            // otherwise it briefly falls back to the "select a
            // server" empty state until the 4s poll catches up.
            await reload();
            if (newName) setSelected(newName);
          }}
        />
      )}
    </div>
  );
}

function DetailView({
  server, detail, busyAction,
  onRestart, onEnable, onDisable, onDelete, onEdit,
}: {
  server: ServerStatus;
  detail: ServerDetail | null;
  busyAction: null | "enable" | "disable" | "restart" | "delete";
  onRestart: () => void;
  onEnable: () => void;
  onDisable: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const { label, pill } = statePill(server);
  const busy = busyAction !== null;
  return (
    <>
      <div className={styles.detailHeader}>
        <div className={styles.detailIcon}>🔌</div>
        <span className={styles.detailName}>{server.name}</span>
        <span className={cls(styles.statePill, pill)}>{label}</span>
        <div className={styles.detailActions}>
          {server.enabled ? (
            <button className={styles.iconBtn} onClick={onDisable} disabled={busy}>
              {busyAction === "disable" ? "Disabling…" : "Disable"}
            </button>
          ) : (
            <button className={styles.iconBtn} onClick={onEnable} disabled={busy}>
              {busyAction === "enable" ? "Enabling…" : "Enable"}
            </button>
          )}
          <button
            className={styles.iconBtn}
            onClick={onRestart}
            disabled={busy || !server.enabled}
          >
            {busyAction === "restart" ? "Restarting…" : "Restart"}
          </button>
          <button className={styles.iconBtn} onClick={onEdit} disabled={busy}>
            Edit
          </button>
          <button
            className={cls(styles.iconBtn, styles.danger)}
            onClick={onDelete}
            disabled={busy}
          >
            {busyAction === "delete" ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>

      {server.error && server.error !== "disabled" && (
        <div className={styles.errorBox}>{server.error}</div>
      )}

      <div className={styles.sectionHead}>Config</div>
      <div className={styles.metaGrid}>
        <div className={styles.metaKey}>Transport</div>
        <div className={styles.metaVal}>{server.type}</div>

        <div className={styles.metaKey}>Command</div>
        <div className={styles.metaVal}>
          <code>{server.command.join(" ")}</code>
        </div>

        <div className={styles.metaKey}>Environment</div>
        <div className={styles.metaVal}>
          {Object.keys(server.env).length === 0
            ? "(inherits worker env)"
            : Object.entries(server.env).map(([k, v]) => (
                <div key={k}>
                  <code>{k}</code>=<code>{v}</code>
                </div>
              ))}
        </div>

        <div className={styles.metaKey}>Timeout (s)</div>
        <div className={styles.metaVal}>{server.timeout_seconds}</div>

        <div className={styles.metaKey}>Tool prefix</div>
        <div className={styles.metaVal}>
          <code>{server.name}__</code>
        </div>
      </div>

      <div className={styles.sectionHead}>
        Tools ({server.tool_count})
      </div>
      {!detail ? (
        <div className={styles.subtitle}>Loading tools…</div>
      ) : (detail.tool_schemas || []).length === 0 ? (
        <div className={styles.subtitle}>
          {server.error === "disabled"
            ? "Server is disabled. Click Enable to spawn it and load its tools."
            : server.error
              ? "Server failed to start — see error above. Tool list unavailable."
              : server.ready
                ? "No tools exposed by this server."
                : "Server starting — tool list will appear shortly."}
        </div>
      ) : (
        <div className={styles.toolList}>
          {(detail.tool_schemas || []).map((t) => (
            <ToolItem key={t.name} server={server.name} tool={t} />
          ))}
        </div>
      )}
    </>
  );
}

function ToolItem({ server, tool }: { server: string; tool: ToolSchema }) {
  const [show, setShow] = useState(false);
  return (
    <div className={styles.toolRow} onClick={() => setShow((v) => !v)}>
      <div className={styles.toolRowHead}>
        <span className={styles.toolName}>{server}__{tool.name}</span>
        <span className={styles.toolExpand}>{show ? "▾" : "▸"}</span>
      </div>
      <div className={styles.toolDesc}>
        {tool.description || tool.title || "—"}
      </div>
      {show && (
        <div
          className={styles.toolSchema}
          onClick={(e) => e.stopPropagation()}
        >
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
    setErr(null);
    setNote(null);
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
      onSaved(body.name);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function testRun() {
    setErr(null);
    setNote(null);
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
      setNote(`✓ test ok — ${data.tool_count} tool(s): ${data.tools.join(", ")}`);
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
              Whitespace-separated. Resolved against worker&apos;s $PATH.
            </span>
          </div>

          <div className={styles.field}>
            <label className={styles.fieldLabel}>
              Environment (KEY=VALUE per line)
            </label>
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
            <label className={styles.fieldLabel}>
              Startup / per-call timeout (s)
            </label>
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
          {note && <div className={styles.okBox}>{note}</div>}
        </div>

        <div className={styles.modalFooter}>
          <button
            className={styles.iconBtn}
            onClick={() => void testRun()}
            disabled={saving}
          >
            Test
          </button>
          <button className={styles.iconBtn} onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className={cls(styles.iconBtn, styles.primary)}
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
