"use client";

import { LoaderCircle, FileText } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { useEffect, useRef, useState } from "react";
import type { DocumentController } from "@/lib/files/document/document-controller";
import { createBoundOfficeEditor, OFFICE_EDITOR_DISABLED, officeHostAvailability, type OfficeEditorInstance } from "@/lib/documents/office-editor";
import styles from "./document-window.module.css";
import officeStyles from "./office-surface.module.css";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Toolbar actions are the app's raised buttons (Undo / Review). */
const BUTTON = cn(buttonVariants({ variant: "elevated", size: "xs" }), styles.button);

export function OfficeSurface({ controller, bytes, path, readOnly, mode }: {
  controller: DocumentController; bytes: Blob; path: string; readOnly: boolean; mode: "preview" | "edit";
}) {
  const { text } = useTranslation();
  const [needsInstall, setNeedsInstall] = useState(false);
  // The ONLYOFFICE application is disabled: only the toolbar's download remains.
  const [disabled, setDisabled] = useState(false);
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
  // The editor opens on the bytes and mode of that moment; later edits and
  // mode switches go through the open editor instead of reopening it.
  const editorOpening = useEffectEvent(() => ({ bytes, mode }));
  // A language switch must not reopen the editor and drop its state.
  const message = useEffectEvent(text);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    setError(null);
    setNeedsInstall(false);
    setDisabled(false);
    setLoading(true);
    setPhase("resources");
    setConfirmedReadonly(true);
    const session = crypto.randomUUID().replace(/-/g, "").slice(0, 20);
    const { bytes: openingBytes, mode: openingMode } = editorOpening();
    void (async () => {
      try {
        let available = await officeHostAvailability(session, fetch, abort.signal);
        if (!active) return;
        if (available.reason === OFFICE_EDITOR_DISABLED) { setDisabled(true); setLoading(false); return; }
        if (!available.available && available.installable) {
          if (!installRequested) { setNeedsInstall(true); setLoading(false); return; }
          setPhase("install");
          const result = await fetch("/api/documents/office-install", { method: "POST", signal: abort.signal });
          if (!result.ok) {
            const failure = await result.json().catch(() => ({})) as { code?: string; version?: string | null; updateTo?: string | null };
            // No release this version reads carries the Office files: a retry cannot help, an update can.
            if (failure.code === "office_release_missing") throw new Error(failure.updateTo
              ? message(`OpenProgram ${failure.version} cannot download Office support: releases before ${failure.updateTo} do not include it. Update OpenProgram to ${failure.updateTo} or newer, then install again.`,
                `OpenProgram ${failure.version} 无法下载 Office 支持：${failure.updateTo} 之前的版本不包含它。请把 OpenProgram 更新到 ${failure.updateTo} 或更高版本后再安装。`)
              : message("No OpenProgram release offers the Office files this version needs. Update OpenProgram to the newest version, then install again.",
                "没有任何 OpenProgram 版本提供此版本所需的 Office 文件。请把 OpenProgram 更新到最新版本后再安装。"));
            throw new Error(message("Installation failed. Check your connection and available disk space, then retry.", "安装失败，请检查网络和可用磁盘空间后重试。"));
          }
          available = await officeHostAvailability(session, fetch, abort.signal);
          if (!available.available) throw new Error(message("Office installation is unavailable.", "Office 安装尚不可用。"));
        }
        if (!active || !host.current) return;
        setPhase("document");
        const instance = await createBoundOfficeEditor({ container: host.current, controller, bytes: openingBytes,
          fileName: path.split("/").pop() ?? "document", hostUrl: available.hostUrl!, readonly: readOnly, initialMode: openingMode, moduleUrl: available.moduleUrl ?? "",
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
  return <div className={`office-editor-surface relative h-full min-h-[320px] overflow-hidden`}>
    <div ref={host} data-office-editor="true" aria-busy={busy} className="h-full"
      style={{ visibility: loading || startupError || needsInstall || disabled ? "hidden" : "visible" }} />
    {error && !startupError && <div role="alert" className={`${styles.error} ${officeStyles.officeRuntimeError}`}>{error}</div>}
    {disabled ? <div role="status" className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("ONLYOFFICE is turned off", "ONLYOFFICE 已关闭")}</span>
      <span className={officeStyles.officeLoadingDetail}>{text("Turn it on in Applications to open this file here. You can still download the original file.", "在“应用”中开启后即可在这里打开此文件。仍可下载原文件。")}</span>
    </div> : needsInstall ? <div className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("Install Office document support", "安装 Office 文档支持")}</span>
      <span className={officeStyles.officeLoadingDetail}>{text("Open presentations, documents and spreadsheets locally with ONLYOFFICE. Optional download: about 699 MiB. Your files stay on this device.", "使用 ONLYOFFICE 在本地打开演示文稿、文档和表格。可选下载约 699 MiB，文件在本机处理。")}</span>
      <a href="https://github.com/ONLYOFFICE/DocumentServer/blob/master/LICENSE.txt" target="_blank" rel="noreferrer" className={officeStyles.officeLoadingDetail}>{text("ONLYOFFICE license (AGPLv3)", "ONLYOFFICE 许可证（AGPLv3）")}</a>
      {dismissed ? <button className={BUTTON} onClick={() => setDismissed(false)}>{text("Installation options", "安装选项")}</button> : <div className="flex gap-[8px] mt-[4px]">
        <button className={BUTTON} onClick={() => setDismissed(true)}>{text("Cancel", "取消")}</button>
        <button className={BUTTON} onClick={() => setInstallRequested(true)}>{text("Install", "安装")}</button>
      </div>}
    </div> : startupError ? <div role="alert" className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("Unable to open document", "无法打开文档")}</span>
      <span className={officeStyles.officeLoadingDetail}>{error}</span>
      <button className={BUTTON} onClick={() => setAttempt(value => value + 1)}>{text("Retry", "重试")}</button>
    </div> : loading && <div role="status" className={officeStyles.officeLoading}>
      <LoaderCircle size={24} strokeWidth={1.5} className={officeStyles.officeSpinner} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{phase === "install" ? text("Downloading and installing Office support…", "正在下载并安装 Office 支持…") : phase === "resources"
        ? text("Preparing document viewer…", "正在准备文档预览…")
        : text("Opening document…", "正在打开文档…")}</span>
      <span className={officeStyles.officeLoadingDetail}>{path.split("/").pop()}</span>
    </div>}
  </div>;
}
