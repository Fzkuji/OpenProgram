"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { systemAccessRequired } from "@/lib/access/system-access-result";

type Row = {
  id: string;
  label?: string;
  status: string;
  can_request?: boolean;
  request_mode?: string;
  settings_available?: boolean;
  setup_group?: string;
  category?: string;
  detail?: string;
  instruction?: string;
};

/**
 * Native setup belongs to the visible owner UI, never to model-written prose.
 * `autoOpen` only starts status polling and lets the parent acknowledge a live
 * wait. It never POSTs an authorization request. Every POST below is directly
 * reachable from a user click.
 */
export function SystemAccessRecovery({ output, requiredCapabilities, autoOpen, onAutoOpen }: {
  output?: unknown; requiredCapabilities?: string[]; autoOpen: boolean; onAutoOpen?: () => void;
}) {
  const required = requiredCapabilities ?? systemAccessRequired(output);
  const { text } = useTranslation();
  const [rows, setRows] = useState<Row[]>([]);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(false);
  const [visibleNow, setVisibleNow] = useState(() => typeof document !== "undefined" && document.visibilityState === "visible");
  const [armed, setArmed] = useState(false);
  const version = useRef(0);
  const busy = useRef(false);
  const lifetime = useRef<AbortController | null>(null);
  const refresh = useRef<() => Promise<void>>(async () => {});
  const local = typeof window !== "undefined" && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  const key = required.join(",");

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    busy.current = false;
    setPending(""); setChecked(false); setRows([]);
    async function check() {
      if (busy.current || controller.signal.aborted) return;
      const current = ++version.current;
      try {
        const response = await fetch("/api/system/access", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        if (!Array.isArray(data.capabilities)) throw new Error("Invalid access report");
        if (!controller.signal.aborted && current === version.current) { setRows(data.capabilities); setChecked(true); setError(""); }
      } catch {
        if (!controller.signal.aborted && current === version.current) { setChecked(false); setError(text("Could not verify system access.", "无法确认系统权限。")); }
      }
    }
    const visible = () => { const active = document.visibilityState === "visible"; setVisibleNow(active); if (active) void check(); };
    refresh.current = check;
    void check();
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => { controller.abort(); window.removeEventListener("focus", visible); document.removeEventListener("visibilitychange", visible); };
  }, [key, text]);

  useEffect(() => {
    if (!local || !armed) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh.current(); }, 2000);
    return () => window.clearInterval(timer);
  }, [local, armed]);

  // This acknowledgement starts polling only. It is intentionally separate
  // from setup(), so reconnect/history replay cannot produce a native prompt.
  useEffect(() => {
    if (!autoOpen || !local || !visibleNow || armed) return;
    setArmed(true);
    onAutoOpen?.();
  }, [autoOpen, local, visibleNow, armed, onAutoOpen]);

  const rowById = new Map(rows.map(row => [row.id, row]));
  const missing = required.map(id => rowById.get(id) || {
    id, status: "unknown", settings_available: false,
  }).filter(row => row.status !== "granted");

  async function setup(row: Row, openSettings = false) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || busy.current) return;
    busy.current = true;
    setPending(row.id); setError("");
    try {
      const response = await fetch(`/api/system/access/${encodeURIComponent(row.id)}?open_settings=${openSettings ? "true" : "false"}`, { method: "POST", signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || String(response.status));
      if (!signal.aborted) {
        setRows(previous => previous.map(item => item.id === row.id ? result : item));
        if (openSettings) setArmed(true);
        void refresh.current();
      }
    } catch (e) {
      if (!signal.aborted) setError(e instanceof Error ? e.message : text("Could not open authorization.", "无法打开授权窗口。"));
    } finally {
      if (!signal.aborted) { busy.current = false; setPending(""); }
    }
  }

  async function setupAll() {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || busy.current) return;
    busy.current = true;
    setPending("all"); setError("");
    try {
      const response = await fetch("/api/system/access/setup", { method: "POST", signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || String(response.status));
      if (!signal.aborted) { setRows(Array.isArray(result.capabilities) ? result.capabilities : []); setArmed(true); void refresh.current(); }
    } catch (e) {
      if (!signal.aborted) setError(e instanceof Error ? e.message : text("Could not initialize system access.", "无法初始化系统权限。"));
    } finally {
      if (!signal.aborted) { busy.current = false; setPending(""); }
    }
  }

  function statusText(status: string) {
    return ({
      granted: text("Granted", "已授权"),
      not_granted: text("Not authorized", "未授权"),
      unknown: text("Not verified", "未确认"),
      unavailable: text("Unavailable", "不可用"),
      unsupported: text("Unsupported", "暂不支持"),
    } as Record<string, string>)[status] || status;
  }

  return <div role="status" aria-label={text("System access", "系统权限")}
    style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", color: "var(--text-secondary)", fontSize: "inherit" }}>
    <span>{error || (!local ? text("Waiting for authorization on the execution computer.", "等待执行电脑完成系统授权。")
      : !checked ? text("Checking system access…", "正在检查系统权限…")
      : !missing.length ? text("System access is ready.", "系统权限已就绪。")
      : text("Waiting for system authorization…", "等待系统授权…"))}</span>
    {checked && missing.length > 0 && <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
      {local && missing.some(row => row.request_mode === "native" || row.can_request) && <button type="button" disabled={!!pending}
        style={{ border: 0, background: "none", padding: 0, color: "var(--text-secondary)", font: "inherit", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}
        onClick={() => void setupAll()}>{pending === "all" ? text("Setting up…", "正在初始化…") : text("Set up all", "初始化全部")}</button>}
      {missing.map(row => <span key={row.id} style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
        <span>{row.label || row.id} · {statusText(row.status)}</span>
        {local && row.status === "not_granted" && row.can_request && <button type="button" disabled={!!pending}
          style={{ border: 0, background: "none", padding: 0, color: "var(--text-secondary)", font: "inherit", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}
          onClick={() => void setup(row)}>{pending === row.id ? text("Requesting…", "正在申请…") : text("Request", "请求授权")}</button>}
        {local && row.settings_available !== false && row.status !== "granted" && row.status !== "unsupported" && row.status !== "unavailable" && <button type="button" disabled={!!pending}
          style={{ border: 0, background: "none", padding: 0, color: "var(--text-secondary)", font: "inherit", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}
          onClick={() => void setup(row, true)}>{text("Open Settings", "打开设置")}</button>}
      </span>)}
    </span>}
  </div>;
}
