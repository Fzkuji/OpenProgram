"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { systemAccessRequired } from "./system-access-result";

type Row = { id: string; status: string; can_request?: boolean };

/** Native setup belongs to the visible owner UI, never to model-written prose. */
export function SystemAccessRecovery({ output, autoOpen, onContinue }: {
  output: unknown; autoOpen: boolean; onContinue?: () => void;
}) {
  const required = systemAccessRequired(output);
  const { text } = useTranslation();
  const [rows, setRows] = useState<Row[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(false);
  const [visibleNow, setVisibleNow] = useState(() => typeof document !== "undefined" && document.visibilityState === "visible");
  const attempted = useRef(false);
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
      if (busy.current) return;
      const current = ++version.current;
      try {
        const response = await fetch("/api/system/access", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        if (!controller.signal.aborted && current === version.current) { setRows(data.capabilities); setChecked(true); setError(""); }
      } catch {
        if (!controller.signal.aborted && current === version.current) setError(text("Could not verify system access.", "无法确认系统权限。"));
      }
    }
    const visible = () => { const active = document.visibilityState === "visible"; setVisibleNow(active); if (active) void check(); };
    refresh.current = check;
    void check();
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => { controller.abort(); window.removeEventListener("focus", visible); document.removeEventListener("visibilitychange", visible); };
  }, [key, text]);
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
    if (!autoOpen || !local || !checked || attempted.current || !missing.length || !visibleNow) return;
    attempted.current = true;
    void setup(missing[0]);
  }, [autoOpen, local, checked, key, visibleNow]); // Only one automatic request for this visible completion.
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
      setRows(data.capabilities);
      if (required.every(id => data.capabilities.some((row: Row) => row.id === id && row.status === "granted"))) onContinue?.();
      else setError(text("Access has not taken effect for the executor yet.", "执行程序的权限尚未生效。"));
    } catch { if (!signal.aborted) setError(text("Could not verify system access.", "无法确认系统权限。")); }
    finally { if (!signal.aborted) { busy.current = false; setPending(false); } }
  }
  return <section role="status" aria-label={text("System access", "系统权限")}>
    <p>{text("Waiting for system authorization. Your task is saved.", "等待系统授权，原任务已保留。")}</p>
    {!local && <p>{text("Complete authorization on the execution computer.", "请在实际执行任务的电脑上完成授权。")}</p>}
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {local && missing.map(id => <Button key={id} variant="secondary" disabled={pending || !checked} onClick={() => void setup(id)}>
        {id === "screen_recording" ? text("Enable screen recording", "开启屏幕录制") : text("Enable desktop control", "开启辅助功能")}
      </Button>)}
      {onContinue && <Button disabled={pending || !checked || missing.length > 0} onClick={() => void resume()}>{text("Continue task", "继续任务")}</Button>}
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
