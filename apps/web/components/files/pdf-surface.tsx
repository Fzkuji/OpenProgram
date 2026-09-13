"use client";
import { lazy, Suspense, useEffect, useState } from "react";
import type { DocumentController } from "@/lib/files/document-controller";
const PdfPreview = lazy(() => import("./preview/pdf-preview"));

/** The owner remounts on editorRevision; autosaved draft bytes retain the editor. */
export function PdfSurface({ bytes, path, controller, readOnly, saveStatus }: { bytes: Blob; path: string; controller: DocumentController; readOnly: boolean; saveStatus: string }) {
  const [sourceUrl] = useState(() => URL.createObjectURL(bytes));
  useEffect(() => () => URL.revokeObjectURL(sourceUrl), [sourceUrl]);
  return <Suspense fallback={<span>Loading PDF…</span>}><PdfPreview sourceUrl={sourceUrl} path={path} controller={readOnly ? undefined : controller} saveStatus={saveStatus} /></Suspense>;
}
