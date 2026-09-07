"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import styles from "./settings-page.module.css";

type Capability = { id: string; label: string; status: string; detail: string; instruction: string; can_request: boolean };
type Report = { application?: string; platform: string; host: string; executable: string; capabilities: Capability[] };

/** Live executor status; never persist a grant or prompt while checking. */
export function SystemAccess() {
  const { text } = useTranslation();
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let generation = 0;
    const controller = new AbortController();
    const check = async () => {
      const current = ++generation;
      try {
        const response = await fetch("/api/system/access", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        if (current === generation) { setReport(data); setError(""); }
      } catch {
        if (!controller.signal.aborted && current === generation) setError(text("Could not check system access.", "无法检查系统权限。"));
      }
    };
    const visible = () => { if (document.visibilityState === "visible") void check(); };
    void check();
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("openprogram-system-access-changed", visible);
    return () => {
      controller.abort();
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("openprogram-system-access-changed", visible);
    };
  }, [text]);
  const local = typeof window !== "undefined" && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  async function request(row: Capability) {
    setPending(row.id); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/system/access/${encodeURIComponent(row.id)}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      setNotice(result.status === "granted" ? text("Access confirmed.", "已确认授权。") : text("Complete authorization in System Settings on this computer, then return here.", "请在这台电脑的系统设置中完成授权，然后返回此处。"));
      window.dispatchEvent(new Event("openprogram-system-access-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : text("Permission request failed.", "申请权限失败。")); }
    finally { setPending(""); }
  }
  const labels: Record<string, string> = {
    granted: text("Granted", "已授权"), not_granted: text("Not authorized", "未授权"),
    unknown: text("Not verified", "未确认"), unavailable: text("Unavailable", "不可用"),
    unsupported: text("Unsupported", "暂不支持"),
  };
  return <section>
    <h3 className={styles.sectionTitle}>{text("System access", "系统权限")}</h3>
    <p className={styles.pageMeta}>{text("Optional capabilities on the execution computer. Checks never open permission prompts.", "执行电脑上的可选功能。检查状态不会弹出授权窗口。")}</p>
    {!report && !error && <p role="status">{text("Checking…", "正在检查…")}</p>}
    {report && <>
      <p className={styles.pageMeta}>{report.application ? `${report.application} · ` : ""}{report.host} · {report.platform}</p>
      <div className={styles.card}>{report.capabilities.map(row => <div className={`${styles.row} ${styles.rowTop}`} key={row.id}>
        <div className={styles.label}>
          <div>{row.id === "screen_recording" ? text("Screen recording", "屏幕录制") : row.id === "accessibility" ? text("Desktop control", "桌面控制") : text("Desktop access", "桌面访问")} · {labels[row.status] || row.status}</div>
          <p className={styles.pageMeta}>{text(row.detail,
            row.status === "granted" ? "当前执行程序已获得授权。" :
            row.status === "not_granted" ? "当前执行程序尚未获得这项系统授权。" :
            row.status === "unknown" ? "尚不能确认当前执行程序是否可以访问桌面。" :
            report.platform === "Linux" ? "当前桌面环境或后端无法提供这项功能。" : "当前运行环境缺少所需支持。")}</p>
          {row.status !== "granted" && <p className={styles.pageMeta}>{text(row.instruction,
            report.platform === "Darwin" ? `请在执行电脑的系统设置 → 隐私与安全性 → ${row.id === "accessibility" ? "辅助功能" : "屏幕与系统音频录制"}，授权系统提示的执行程序。返回后自动检查；仅在系统要求时重启程序。` :
            report.platform === "Linux" ? "在已登录的受支持图形桌面中运行；无桌面服务器可使用浏览器或虚拟机功能，不需要开放桌面权限。" :
            "请在已登录的桌面中运行。锁屏、系统确认界面或高权限应用可能不可访问，无需把整个程序改为管理员运行。")}</p>}
        </div>
        {row.can_request && local && <Button variant="secondary" disabled={!!pending} onClick={() => void request(row)}>{pending === row.id ? text("Requesting…", "正在申请…") : text("Set up access", "设置权限")}</Button>}
      </div>)}</div>
      <details><summary>{text("Execution process", "执行程序")}</summary><code>{report.executable}</code></details>
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
