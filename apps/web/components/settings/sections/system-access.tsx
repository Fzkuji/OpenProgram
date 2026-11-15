"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "@/lib/i18n";
import { systemAccessQuery, type Capability } from "@/lib/settings/system-page-queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SettingsHeading, SettingsRow } from "../settings-ui";
import { managePageStyles as shared } from "@/components/ui/manage-page";

const legacyLabels: Record<string, [string, string]> = {
  screen_recording: ["Screen recording", "屏幕录制"],
  accessibility: ["Desktop control", "桌面控制"],
};

function fallbackLabel(id: string, text: (en: string, zh: string) => string) {
  const known = legacyLabels[id];
  if (known) return text(...known);
  return id.replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function groupLabel(id: string, text: (en: string, zh: string) => string) {
  const names: Record<string, [string, string]> = {
    desktop: ["Desktop", "桌面"],
    privacy: ["Privacy", "隐私"],
    media: ["Media", "媒体"],
    files: ["Files", "文件"],
    automation: ["Automation", "自动化"],
    integrations: ["Integrations", "集成"],
    storage: ["Storage", "存储"],
  };
  return names[id] ? text(...names[id]) : id;
}

/** Live executor status; reading this report never opens a permission prompt. */
export function SystemAccess() {
  const { text } = useTranslation();
  const client = useQueryClient();
  const access = useQuery(systemAccessQuery);
  const report = access.data ?? null;
  const [failure, setError] = useState("");
  const error = failure || (access.isError ? text("Could not check system access.", "无法检查系统权限。") : "");
  const [pending, setPending] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const changed = () => void client.invalidateQueries({ queryKey: systemAccessQuery.queryKey });
    window.addEventListener("openprogram-system-access-changed", changed);
    return () => window.removeEventListener("openprogram-system-access-changed", changed);
  }, [client]);

  const local = typeof window !== "undefined" && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  async function request(row: Capability) {
    setPending(row.id); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/system/access/${encodeURIComponent(row.id)}?open_settings=false`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || String(response.status));
      setNotice(result.status === "granted" ? text("Access confirmed.", "已确认授权。") : text("Authorization request finished without confirmation.", "授权请求结束，但尚未确认授权。"));
      window.dispatchEvent(new Event("openprogram-system-access-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : text("Permission request failed.", "申请权限失败。")); }
    finally { setPending(""); }
  }
  async function setupAll() {
    setPending("all"); setError(""); setNotice("");
    try {
      const response = await fetch("/api/system/access/setup", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || String(response.status));
      client.setQueryData(systemAccessQuery.queryKey, previous => previous ? { ...previous, capabilities: result.capabilities || previous.capabilities } : previous);
      setNotice(result.status === "granted"
        ? text("All registered native access is confirmed.", "已确认所有已登记的本机权限。")
        : text("Setup finished; review the remaining capabilities below.", "初始化完成，请检查下方仍未授权的权限。"));
      window.dispatchEvent(new Event("openprogram-system-access-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : text("Permission setup failed.", "权限初始化失败。")); }
    finally { setPending(""); }
  }

  const labels: Record<string, string> = {
    granted: text("Granted", "已授权"), not_granted: text("Not authorized", "未授权"),
    unknown: text("Not verified", "未确认"), unavailable: text("Unavailable", "不可用"),
    unsupported: text("Unsupported", "暂不支持"), requesting: text("Requesting", "申请中"),
  };
  const groups = useMemo(() => {
    const result = new Map<string, Capability[]>();
    for (const row of report?.capabilities || []) {
      const group = row.setup_group || row.category || "other";
      const rows = result.get(group) || [];
      rows.push(row); result.set(group, rows);
    }
    return [...result.entries()];
  }, [report]);

  const describe = (row: Capability) => {
    if (row.status === "granted") return text("OpenProgram has this access.", "OpenProgram 已获得这项授权。");
    if (row.status === "not_granted") return row.instruction || text("OpenProgram does not have this access yet.", "OpenProgram 还没有这项授权。");
    if (row.status === "unknown") return row.instruction || row.detail || text("This can't be checked from here yet.", "目前无法在这里确认这项权限。");
    if (row.status === "unsupported") return text("Not supported on this computer.", "这台电脑不支持这项能力。");
    return row.detail || text("Not available right now.", "当前无法使用。");
  };

  const canAct = report?.platform === "Darwin" && local;
  return <section className="flex flex-col gap-3">
    <SettingsHeading
      title={text("System access", "系统权限")}
      description={text("What OpenProgram is allowed to do on this computer. Checking never opens a prompt; a prompt appears only when you press a button.", "OpenProgram 在这台电脑上能做什么。检查状态不会弹窗，只有点按钮才会申请授权。")}
    />
    {report && <div className="flex flex-col gap-3">
      {canAct && report.capabilities.some(row => row.status !== "granted" && row.request_mode === "native") && <Card className="gap-0 py-0">
        <CardContent>
          <SettingsRow
            label={text("Request all access", "全部申请授权")}
            description={text("Asks macOS for everything below that OpenProgram doesn't have yet. You still decide each one in macOS's own prompt.", "向 macOS 申请下面所有尚未获得的权限，每一项仍由你在 macOS 的弹窗里决定。")}
          >
            <Button variant="elevated" disabled={!!pending} onClick={() => void setupAll()}>
              {pending === "all" ? text("Requesting…", "正在申请…") : text("Request", "申请")}
            </Button>
          </SettingsRow>
        </CardContent>
      </Card>}
      {groups.map(([group, rows]) => <Card className="gap-0 py-0" key={group}>
        <CardContent>
          <h4 className="pt-4 text-fs-sm font-semibold text-text-primary">{groupLabel(group, text)}</h4>
          <div className="divide-y divide-border">{rows.map(row => {
            const status = labels[row.status] || row.status;
            const canRequest = row.can_request === true && row.status === "not_granted" && row.request_mode !== "settings";
            const name = text(row.label || fallbackLabel(row.id, text), row.label_zh || fallbackLabel(row.id, text));
            return <SettingsRow
              key={row.id}
              label={<span className="flex flex-wrap items-center gap-2">{name}<span className={`${shared.badge} ${row.status === "granted" ? shared.badgeGreen : row.status === "not_granted" ? shared.badgeYellow : ""}`}>{status}</span></span>}
              description={describe(row)}
            >
              {canAct && canRequest ? <Button variant="elevated" disabled={!!pending} onClick={() => void request(row)}>{pending === row.id ? text("Requesting…", "正在申请…") : text("Request", "申请")}</Button> : null}
            </SettingsRow>;
          })}</div>
        </CardContent>
      </Card>)}
      <Card className="gap-0 py-0">
        <CardContent>
          <SettingsRow
            label={text("This computer", "这台电脑")}
            description={<>
              {report.application ? `${report.application} · ` : ""}{report.host} · {report.platform}
              <details className="mt-1.5"><summary className="cursor-pointer">{text("Program that runs tasks", "执行任务的程序")}</summary><code className="mt-1 block font-mono text-fs-xs [overflow-wrap:anywhere]">{report.executable}</code></details>
            </>}
          />
        </CardContent>
      </Card>
    </div>}
    {notice && <p role="status" className="text-fs-sm text-accent-green">{notice}</p>}
    {error && <p role="alert" className="text-fs-sm text-danger-soft">{error}</p>}
  </section>;
}
