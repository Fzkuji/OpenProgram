"use client";
import { useEffect, useRef, useState } from "react";
import type { DocumentController } from "@/lib/state/document-controller";
import { createBoundRasterEditor, type RasterEditorInstance } from "@/lib/documents/raster-editor";
import styles from "./document-window.module.css";

export function RasterSurface({ controller, bytes, path, readOnly, mode, onReady }: { controller: DocumentController; bytes: Blob; path: string; readOnly: boolean; mode: "preview" | "edit"; onReady?: (editor: RasterEditorInstance | null) => void }) {
  const currentMode = useRef(mode); currentMode.current = mode;
  const host = useRef<HTMLDivElement>(null); const editor = useRef<RasterEditorInstance | null>(null); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true);
  useEffect(() => { let active = true; const container = host.current; if (!container) return; const abort = new AbortController();
    void createBoundRasterEditor({ container, controller, bytes, fileName: path.split("/").pop() ?? "image", readonly: readOnly, signal: abort.signal })
      .then((value) => { if (!active) { void value.destroy(); return; } editor.current = value; onReady?.(value); value.setReadonly(readOnly || currentMode.current === "preview"); setLoading(false); })
      .catch((e) => { if (active) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } });
    return () => { active = false; abort.abort(); const value = editor.current; editor.current = null; onReady?.(null); if (value) void value.destroy(); };
  // The controller owns the editor generation; changing draft bytes must not recreate the undo stack.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, path]);
  useEffect(() => { editor.current?.setReadonly(readOnly || mode === "preview"); }, [mode, readOnly]);
  return <div style={{ height: "100%", minHeight: 320, position: "relative" }}>
    {error && <div role="alert" className={styles.error}>{error}</div>}
    {loading && <span>{readOnly ? "Loading preview…" : "Loading image editor…"}</span>}
    <div ref={host} data-raster-editor="true" aria-busy={loading} style={{ height: "100%", visibility: loading ? "hidden" : "visible" }} />
  </div>;
}
