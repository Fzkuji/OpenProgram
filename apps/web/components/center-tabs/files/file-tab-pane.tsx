"use client";
import type { FileSectionNavigation } from "@/lib/tabs/panes/file-section-requests";
import { DocumentWindow } from "@/components/files/surfaces/lazy-document-window";

export function FileTabPane({ projectId, path, sessionId, readOnly, sectionNavigation, previewSessionId }: { projectId: string; path: string; sessionId?: string; readOnly?: boolean; sectionNavigation?: FileSectionNavigation; previewSessionId?: string }) {
  return <DocumentWindow projectId={projectId} path={path} sessionId={sessionId} readOnly={readOnly} sectionNavigation={sectionNavigation} previewSessionId={previewSessionId} />;
}
