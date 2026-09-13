"use client";

import { LoaderCircle, FileText } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useEffect, useRef, useState } from "react";
import type { DocumentController } from "@/lib/files/document-controller";
import { createBoundOfficeEditor, officeHostAvailability, type OfficeEditorInstance } from "@/lib/documents/office-editor";
import styles from "./document-window.module.css";
import officeStyles from "./office-surface.module.css";

export function OfficeSurface({ controller, bytes, path, readOnly, mode }: {
  controller: DocumentController; bytes: Blob; path: string; readOnly: boolean; mode: "preview" | "edit";
}) {
  const { text } = useTranslation();
  const [needsInstall, setNeedsInstall] = useState(false);
  const [installRequested, setInstallRequested] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<"resources" | "install" | "document">("resources");
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<OfficeEditorInstance | null>(null);
  const currentMode = useRef(mode);
  currentMode.current = mode;
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirmedReadonly, setConfirmedReadonly] = useState(true);
  const busy = !error && (loading || confirmedReadonly !== (readOnly || mode === "preview"));
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    setError(null);
    setNeedsInstall(false);
    setLoading(true);
    setPhase("resources");
    setConfirmedReadonly(true);
    const session = crypto.randomUUID().replace(/-/g, "").slice(0, 20);
    void (async () => {
      try {
        let available = await officeHostAvailability(session, fetch, abort.signal);
        if (!active) return;
        if (!available.available && available.installable) {
          if (!installRequested) { setNeedsInstall(true); setLoading(false); return; }
          setPhase("install");
          const result = await fetch("/api/documents/office-install", { method: "POST", signal: abort.signal });
          if (!result.ok) throw new Error(text("Installation failed. Check your connection and available disk space, then retry.", "安装失败，请检查网络和可用磁盘空间后重试。"));
          available = await officeHostAvailability(session, fetch, abort.signal);
          if (!available.available) throw new Error(text("Office installation is unavailable.", "Office 安装尚不可用。"));
        }
        if (!active || !host.current) return;
        setPhase("document");
        const instance = await createBoundOfficeEditor({ container: host.current, controller, bytes,
          fileName: path.split("/").pop() ?? "document", hostUrl: available.hostUrl!, readonly: readOnly, initialMode: mode, moduleUrl: available.moduleUrl ?? "",
          packageVersion: available.packageVersion, hostBuildId: available.hostBuildId,
          assetManifestDigest: available.assetManifestDigest, signal: abort.signal,
          onReadonlyChange: (value) => { if (active) setConfirmedReadonly(value); },
          onError: (failure) => { if (active) setError(failure.message); } });
        if (!active) { await instance.destroy(); return; }
        editor.current = instance;
        instance.setReadonly(readOnly || currentMode.current === "preview");
        setLoading(false);
      } catch (failure) {
        if (active) { setError(failure instanceof Error ? failure.message : String(failure)); setLoading(false); }
      }
    })();
    return () => { active = false; abort.abort(); const instance = editor.current; editor.current = null; if (instance) void instance.destroy(); };
  }, [controller, path, readOnly, attempt, installRequested]);
  useEffect(() => { if (editor.current) editor.current.setReadonly(readOnly || mode === "preview"); }, [mode, readOnly]);
  useEffect(() => { host.current?.toggleAttribute("inert", busy); }, [busy]);
  const startupError = error !== null && editor.current === null;
  return <div className={`office-editor-surface ${officeStyles.officeSurface}`}>
    <div ref={host} data-office-editor="true" aria-busy={busy} className={officeStyles.officeHost}
      style={{ visibility: loading || startupError || needsInstall ? "hidden" : "visible" }} />
    {error && !startupError && <div role="alert" className={`${styles.error} ${officeStyles.officeRuntimeError}`}>{error}</div>}
    {needsInstall ? <div className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("Install Office document support", "安装 Office 文档支持")}</span>
      <span className={officeStyles.officeLoadingDetail}>{text("Open presentations, documents and spreadsheets locally with ONLYOFFICE. Optional download: about 699 MiB. Your files stay on this device.", "使用 ONLYOFFICE 在本地打开演示文稿、文档和表格。可选下载约 699 MiB，文件在本机处理。")}</span>
      <a href="https://github.com/ONLYOFFICE/DocumentServer/blob/master/LICENSE.txt" target="_blank" rel="noreferrer" className={officeStyles.officeLoadingDetail}>{text("ONLYOFFICE license (AGPLv3)", "ONLYOFFICE 许可证（AGPLv3）")}</a>
      {dismissed ? <button className={styles.button} onClick={() => setDismissed(false)}>{text("Installation options", "安装选项")}</button> : <div className={officeStyles.officeActions}>
        <button className={styles.button} onClick={() => setDismissed(true)}>{text("Cancel", "取消")}</button>
        <button className={styles.button} onClick={() => setInstallRequested(true)}>{text("Install", "安装")}</button>
      </div>}
    </div> : startupError ? <div role="alert" className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("Unable to open document", "无法打开文档")}</span>
      <span className={officeStyles.officeLoadingDetail}>{error}</span>
      <button className={styles.button} onClick={() => setAttempt(value => value + 1)}>{text("Retry", "重试")}</button>
    </div> : loading && <div role="status" className={officeStyles.officeLoading}>
      <LoaderCircle size={24} strokeWidth={1.5} className={officeStyles.officeSpinner} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{phase === "install" ? text("Downloading and installing Office support…", "正在下载并安装 Office 支持…") : phase === "resources"
        ? text("Preparing document viewer…", "正在准备文档预览…")
        : text("Opening document…", "正在打开文档…")}</span>
      <span className={officeStyles.officeLoadingDetail}>{path.split("/").pop()}</span>
    </div>}
  </div>;
}
