"use client";

import { useEffect, useRef, useState } from "react";
import { systemAccessAction } from "@/lib/system-access-action";
import { useTranslation } from "@/lib/i18n";
import { systemAccessRequired } from "@/lib/system-access-result";

type Row = { id: string; status: string; can_request?: boolean };

/** Native setup belongs to the visible owner UI, never to model-written prose. */
export function SystemAccessRecovery({ output, autoOpen, onContinue, onAutoOpen }: {
  output: unknown; autoOpen: boolean; onContinue?: () => void; onAutoOpen?: () => void;
}) {
  const required = systemAccessRequired(output);
  const { text } = useTranslation();
  const [rows, setRows] = useState<Row[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(false);
  const [visibleNow, setVisibleNow] = useState(() => typeof document !== "undefined" && document.visibilityState === "visible");
  const requested = useRef(new Set<string>());
  const resumed = useRef(false);
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
    setPending(false); setChecked(false); setRows([]);
    async function check() {
      if (busy.current || controller.signal.aborted) return;
      const current = ++version.current;
      try {
        const response = await fetch("/api/system/access", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
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
  const missing = required.filter(id => !rows.some(row => row.id === id && row.status === "granted"));
  async function setup(id: string) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || busy.current) return;
    busy.current = true; ++version.current;
    setPending(true); setError("");
    let succeeded = false;
    try {
      const response = await fetch(`/api/system/access/${encodeURIComponent(id)}`, { method: "POST", signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || String(response.status));
      if (!signal.aborted) { succeeded = true; setRows(previous => previous.map(row => row.id === id ? result : row)); }
    } catch (e) { if (!signal.aborted) setError(e instanceof Error ? e.message : text("Could not open authorization.", "无法打开授权窗口。")); }
    finally {
      if (!signal.aborted) { busy.current = false; setPending(false); if (succeeded) void refresh.current(); }
    }
  }
  useEffect(() => {
    if (autoOpen && local && visibleNow && !armed) { setArmed(true); onAutoOpen?.(); }
  }, [autoOpen, local, visibleNow, armed]);
  useEffect(() => {
    const action = systemAccessAction(local, visibleNow, armed, checked, pending,
      resumed.current, missing, requested.current);
    if (action?.type === "request") {
      requested.current.add(action.id);
      void setup(action.id);
    } else if (action?.type === "resume" && onContinue) {
      void resume();
    }
  }, [local, visibleNow, armed, checked, pending, missing.join(",")]);
  async function resume() {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || busy.current) return;
    busy.current = true; ++version.current;
    setPending(true);
    try {
      const response = await fetch("/api/system/access", { cache: "no-store", signal });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      if (signal.aborted) return;
      if (document.visibilityState !== "visible") { resumed.current = false; setVisibleNow(false); return; }
      setRows(data.capabilities);
      if (required.every(id => data.capabilities.some((row: Row) => row.id === id && row.status === "granted"))) { resumed.current = true; onContinue?.(); }
      else { resumed.current = false; setError(text("Access has not taken effect for the executor yet.", "执行程序的权限尚未生效。")); }
    } catch { if (!signal.aborted) { resumed.current = false; setChecked(false); setError(text("Could not verify system access.", "无法确认系统权限。")); } }
    finally { if (!signal.aborted) { busy.current = false; setPending(false); } }
  }
  return <div role="status" aria-label={text("System access", "系统权限")}
    style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", color: "var(--text-secondary)", fontSize: "inherit" }}>
    <span>{error || (!local ? text("Waiting for authorization on the execution computer.", "等待执行电脑完成系统授权。")
      : checked && !missing.length ? text("System access is ready.", "系统权限已就绪。")
      : text("Waiting for system authorization…", "等待系统授权…"))}</span>
    {local && missing.length > 0 && <button type="button" disabled={pending || !checked}
      style={{ border: 0, background: "none", padding: 0, color: "var(--text-secondary)", font: "inherit", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}
      onClick={() => {
        if (!armed) setArmed(true);
        requested.current.add(missing[0]);
        void setup(missing[0]);
      }}>{text("Open System Settings", "打开系统设置")}</button>}
  </div>;
}
