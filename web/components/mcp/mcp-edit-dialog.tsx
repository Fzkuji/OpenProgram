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
import { useTranslation } from "@/lib/i18n";

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
  const { t, text } = useTranslation();
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
    if (!state.name.trim()) return { ok: false, err: text("name is required", "名称为必填项") };
    const base = {
      name: state.name.trim(),
      type: state.transport,
      enabled: state.enabled,
      timeout_seconds: state.timeout_seconds,
      always_load: state.alwaysLoad,
    };
    if (state.transport === "local") {
      const cmd = splitCommand(state.command);
      if (cmd.length === 0) return { ok: false, err: text("command is required", "命令为必填项") };
      return { ok: true, body: { ...base, command: cmd, env: parseEnv(state.env) } };
    }
    if (!state.url.trim()) return { ok: false, err: text("url is required", "URL 为必填项") };
    let auth: Record<string, unknown>;
    if (state.authKind === "bearer") {
      if (!state.bearerToken.trim())
        return { ok: false, err: text("bearer token is required", "Bearer token 为必填项") };
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
    if (!built.ok) { setErr(text(`${built.err} (to test)`, `${built.err}（测试前需要修正）`)); return; }
    const body = { ...built.body, name: state.name || "test", enabled: true };
    setBusy("test");
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr(text(`test failed: ${data.error || data.detail || `HTTP ${r.status}`}`, `测试失败：${data.error || data.detail || `HTTP ${r.status}`}`));
        return;
      }
      setNote(text(
        `✓ test ok - ${data.tool_count} tool(s): ${data.tools.join(", ")}`,
        `✓ 测试通过 - ${data.tool_count} 个工具：${data.tools.join(", ")}`,
      ));
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
            {isAdd ? text("Add MCP server", "添加 MCP 服务器") : text(`Edit ${target.name}`, `编辑 ${target.name}`)}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-name">{text("Name", "名称")}</Label>
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
            <Label htmlFor="mcp-transport">{text("Transport", "传输")}</Label>
            <select
              id="mcp-transport"
              value={state.transport}
              onChange={(e) =>
                setState({ ...state, transport: e.target.value as EditTarget["transport"] })}
              className="flex h-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] border bg-background px-3 font-mono text-sm
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
                <Label htmlFor="mcp-cmd">{text("Command", "命令")}</Label>
                <Input
                  id="mcp-cmd"
                  value={state.command}
                  onChange={(e) => setState({ ...state, command: e.target.value })}
                  placeholder="npx -y @drawio/mcp"
                  className="font-mono"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mcp-env">{text("Environment (KEY=VALUE per line)", "环境变量（每行 KEY=VALUE）")}</Label>
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
                <Label htmlFor="mcp-headers">{text("Headers (Key: Value per line)", "请求头（每行 Key: Value）")}</Label>
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
                <Label htmlFor="mcp-auth">{text("Authentication", "认证")}</Label>
                <select
                  id="mcp-auth"
                  value={state.authKind}
                  onChange={(e) =>
                    setState({ ...state, authKind: e.target.value as EditTarget["authKind"] })}
                  className="flex h-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] border bg-background px-3 font-mono text-sm
                              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="none">{text("none", "无")}</option>
                  <option value="bearer">{text("bearer token (static)", "Bearer token（静态）")}</option>
                  <option value="oauth">{text("OAuth 2.1 (browser flow)", "OAuth 2.1（浏览器流程）")}</option>
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
                    placeholder={text("paste token", "粘贴 token")}
                    className="font-mono"
                  />
                </div>
              )}

              {state.authKind === "oauth" && (
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-name">{text("Client name", "客户端名称")}</Label>
                    <Input
                      id="mcp-oa-name"
                      value={state.oauthClientName}
                      onChange={(e) =>
                        setState({ ...state, oauthClientName: e.target.value })}
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-scope">{text("Scope (optional)", "Scope（可选）")}</Label>
                    <Input
                      id="mcp-oa-scope"
                      value={state.oauthScope}
                      onChange={(e) => setState({ ...state, oauthScope: e.target.value })}
                      placeholder="read write"
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-cid">{text("Client ID (optional)", "Client ID（可选）")}</Label>
                    <Input
                      id="mcp-oa-cid"
                      value={state.oauthClientId}
                      onChange={(e) =>
                        setState({ ...state, oauthClientId: e.target.value })}
                      placeholder={text("leave blank for dynamic registration", "留空则使用动态注册")}
                      className="font-mono"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mcp-oa-csec">{text("Client secret (optional)", "Client secret（可选）")}</Label>
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
                    <Label htmlFor="mcp-oa-port">{text("Redirect port (0 = auto)", "重定向端口（0 = 自动）")}</Label>
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
              <Label htmlFor="mcp-timeout">{text("Timeout (s)", "超时（秒）")}</Label>
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
              <Label htmlFor="mcp-enabled" className="cursor-pointer">{text("Enabled", "已启用")}</Label>
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
                {text("Always load tool schemas (skip deferred-loading)", "始终加载工具 schema（跳过延迟加载）")}
              </Label>
              <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
                {text(
                  "Off (default): tools appear by name in a system-prompt catalog; the model loads schemas on demand via",
                  "关闭（默认）：工具只以名称出现在系统提示词目录中；模型按需通过",
                )}
                <code className="mx-1">tool_search</code>
                {text(
                  "to save tokens when this server has many tools. On: every tool sends its full JSON Schema with each LLM request; use this for small focused servers (3-5 tools) the model uses every turn.",
                  "加载 schema，以便在工具较多时节省 token。开启后，每次大模型请求都会发送所有工具的完整 JSON Schema；适合模型每轮都会使用的小型服务器（3-5 个工具）。",
                )}
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
            {busy === "test" ? text("Testing...", "测试中...") : text("Test", "测试")}
          </button>
          <button className={styles.actionBtn} onClick={onClose} disabled={saving}>
            {t("sidebar.cancel")}
          </button>
          <button
            className={cn(styles.actionBtn, styles.actionBtnPrimary)}
            onClick={() => void save()}
            disabled={saving}
          >
            {busy === "save" ? text("Saving...", "保存中...") : isAdd ? text("Add", "添加") : text("Save", "保存")}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
