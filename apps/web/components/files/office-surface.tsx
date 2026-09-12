"use client";

import { useEffect, useRef, useState } from "react";
import type { DocumentController } from "@/lib/state/document-controller";
import { createBoundOfficeEditor, officeHostAvailability, type OfficeEditorInstance } from "@/lib/documents/office-editor";
import styles from "./document-window.module.css";

export function OfficeSurface({ controller, bytes, path, readOnly, mode }: {
  controller: DocumentController; bytes: Blob; path: string; readOnly: boolean; mode: "preview" | "edit";
}) {
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
    const session = crypto.randomUUID().replace(/-/g, "").slice(0, 20);
    void (async () => {
      try {
        const available = await officeHostAvailability(session);
        if (!active || !host.current) return;
        const instance = await createBoundOfficeEditor({ container: host.current, controller, bytes,
          fileName: path.split("/").pop() ?? "document", hostUrl: available.hostUrl!, readonly: readOnly, initialMode: mode, moduleUrl: available.moduleUrl ?? "",
          packageVersion: available.packageVersion, hostBuildId: available.hostBuildId,
          assetManifestDigest: available.assetManifestDigest,
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
    return () => { active = false; const instance = editor.current; editor.current = null; if (instance) void instance.destroy(); };
  }, [controller, path, readOnly]);
  useEffect(() => { if (editor.current) editor.current.setReadonly(readOnly || mode === "preview"); }, [mode, readOnly]);
  useEffect(() => { host.current?.toggleAttribute("inert", busy); }, [busy]);
  return <div className="office-editor-surface" style={{ height: "100%", minHeight: 320 }}>
    {error && <div role="alert" className={styles.error}>{error}</div>}
    {loading && <span>{readOnly ? "Loading preview…" : "Loading Office editor…"}</span>}
    <div ref={host} data-office-editor="true" aria-busy={busy} style={{ height: "100%", visibility: loading ? "hidden" : "visible" }} />
  </div>;
}
