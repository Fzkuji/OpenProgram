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
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<"resources" | "document">("resources");
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
    setLoading(true);
    setPhase("resources");
    setConfirmedReadonly(true);
    const session = crypto.randomUUID().replace(/-/g, "").slice(0, 20);
    void (async () => {
      try {
        const available = await officeHostAvailability(session, fetch, abort.signal);
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
  }, [controller, path, readOnly, attempt]);
  useEffect(() => { if (editor.current) editor.current.setReadonly(readOnly || mode === "preview"); }, [mode, readOnly]);
  useEffect(() => { host.current?.toggleAttribute("inert", busy); }, [busy]);
  const startupError = error !== null && editor.current === null;
  return <div className={`office-editor-surface ${officeStyles.officeSurface}`}>
    <div ref={host} data-office-editor="true" aria-busy={busy} className={officeStyles.officeHost}
      style={{ visibility: loading || startupError ? "hidden" : "visible" }} />
    {error && !startupError && <div role="alert" className={`${styles.error} ${officeStyles.officeRuntimeError}`}>{error}</div>}
    {startupError ? <div role="alert" className={officeStyles.officeLoading}>
      <FileText size={28} strokeWidth={1.25} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{text("Unable to open document", "无法打开文档")}</span>
      <span className={officeStyles.officeLoadingDetail}>{error}</span>
      <button className={styles.button} onClick={() => setAttempt(value => value + 1)}>{text("Retry", "重试")}</button>
    </div> : loading && <div role="status" className={officeStyles.officeLoading}>
      <LoaderCircle size={24} strokeWidth={1.5} className={officeStyles.officeSpinner} aria-hidden="true" />
      <span className={officeStyles.officeLoadingTitle}>{phase === "resources"
        ? text("Preparing document viewer…", "正在准备文档预览…")
        : text("Opening document…", "正在打开文档…")}</span>
      <span className={officeStyles.officeLoadingDetail}>{path.split("/").pop()}</span>
    </div>}
  </div>;
}
