/**
 * MCP server add/edit dialog.
 *
 * Extracted from mcp-page.tsx so the page file isn't carrying ~400
 * lines of unrelated form rendering. The dialog is self-contained:
 * it owns its own state, validates the form, fires the test +
 * save endpoints, and signals the parent via ``onSaved`` (with the
 * final name) or ``onClose`` (cancel).
 */
"use client";

import { useState } from "react";

import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import styles from "./mcp-page.module.css";

export interface EditTarget {
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

export function EditDialog({
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
