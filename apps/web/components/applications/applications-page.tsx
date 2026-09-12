"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { ManagePageHeader, managePageStyles } from "@/components/ui/manage-page";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { useApplicationLauncher } from "./use-application-launcher";
import styles from "./applications-page.module.css";

export function ApplicationsPage() {
  const { text } = useTranslation();
  const router = useRouter();
  const launcher = useApplicationLauncher(() => router.push("/chat"));
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const [applications, setApplications] = useState<ApplicationDefinition[]>([]);
  const [directory, setDirectory] = useState("");
  const [replace, setReplace] = useState(false);
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = useCallback(async () => {
    const response = await applicationRequest<{ applications: ApplicationDefinition[] }>("/api/applications");
    setApplications(response.applications);
    setLoaded(true);
  }, []);
  useEffect(() => { void refresh().catch(reason => setError(String(reason.message ?? reason))); }, [refresh]);
  async function change(action: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); setNotice(message); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function chooseDirectory() {
    try { const selected = await pickFolder(); if (selected) { setDirectory(selected); setReplace(false); setTrust(false); } }
    catch (reason) { setError(String(reason)); }
  }
  return <div className={managePageStyles.view}>
    <ManagePageHeader title={text("Applications", "应用")} actions={[{ label: text("Refresh", "刷新"), onClick: () => void change(refresh, ""), disabled: busy }]} />
    <div className={styles.content}>
      <p>{text("Install software with its own interface and saved data. Enabled applications appear on the new tab page.", "安装有自己界面和数据的软件。已启用的应用会显示在新标签页。")}</p>
      <form className={styles.install} onSubmit={event => { event.preventDefault(); void change(
        () => applicationRequest("/api/applications/install", "POST", { path: directory.trim(), replace, trust }),
        text("Application installed", "应用已安装"),
      ); }}>
        <label htmlFor="application-directory">{text("Application directory", "应用目录")}</label>
        <div className={styles.directory}>
          <input id="application-directory" value={directory} onChange={event => { setDirectory(event.target.value); setTrust(false); }} placeholder={text("Local directory containing application.json", "包含 application.json 的本地目录")} required disabled={busy} />
          <Button type="button" variant="outline" disabled={busy} onClick={() => void chooseDirectory()}>{text("Browse", "浏览")}</Button>
        </div>
        <label><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} disabled={busy} />{text("Replace the installed version from this source", "替换此来源的已安装版本")}</label>
        <label><input type="checkbox" checked={trust} onChange={event => setTrust(event.target.checked)} disabled={busy} />{text("I reviewed and trust this application's Python code to run locally", "我已审查并信任此应用的 Python 代码在本机运行")}</label>
        <Button type="submit" disabled={busy || !directory.trim()}>{busy ? text("Working…", "处理中…") : text("Install application", "安装应用")}</Button>
      </form>
      {(error || launcher.error) && <p role="alert" className={styles.error}>{error || launcher.error}</p>}
      {notice && <p role="status">{notice}</p>}
      {launcher.dialog}{folderPickerDialog}
      {loaded && applications.length === 0 && <p>{text("No applications installed. Tools and Workflows are managed in Abilities.", "尚未安装应用。工具与 Workflow 在能力页管理。")}</p>}
      <div className={styles.list}>
        {applications.map(application => <section className={styles.application} key={application.id} aria-label={application.display_title || application.title}>
          <h2>{application.display_title || application.title}</h2>
          <p>{application.id} · {application.version} · {application.scope === "project" ? text("Per project", "按项目") : text("Global", "全局")}</p>
          <p className={styles.source}>{text("Source", "来源")}: {application.source}</p>
          <div className={styles.actions}>
            <Button disabled={busy || !application.enabled} onClick={() => void launcher.launch(application)}>{text("Open", "打开")}</Button>
            <label><input type="checkbox" checked={application.enabled} disabled={busy} onChange={event => void change(() => applicationRequest(`/api/applications/${encodeURIComponent(application.id)}`, "PATCH", { enabled: event.target.checked }), text("Application settings saved", "应用设置已保存"))} />{text("Enabled", "启用")}</label>
            <label><input type="checkbox" checked={!application.hidden} disabled={busy} onChange={event => void change(() => applicationRequest(`/api/applications/${encodeURIComponent(application.id)}`, "PATCH", { hidden: !event.target.checked }), text("Application settings saved", "应用设置已保存"))} />{text("Show in new tab", "在新标签页显示")}</label>
            <Button variant="outline" disabled={busy} onClick={() => { setDirectory(application.source); setReplace(true); setTrust(false); setNotice(text("Review the source and use the installation form to update.", "请审查来源，并使用安装表单完成更新。")); document.getElementById("application-directory")?.focus(); }}>{text("Update", "更新")}</Button>
            <Button variant="outline" disabled={busy} onClick={() => void change(() => applicationRequest(`/api/applications/${encodeURIComponent(application.id)}`, "DELETE"), text("Application uninstalled; saved data retained", "应用已卸载，已保存的数据保留"))}>{text("Uninstall", "卸载")}</Button>
          </div>
        </section>)}
      </div>
    </div>
  </div>;
}
