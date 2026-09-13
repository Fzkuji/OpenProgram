"use client";
import { DocumentWindow } from "@/components/files/lazy-document-window";

export function FileTabPane({ projectId, path }: { projectId: string; path: string }) {
  return <DocumentWindow projectId={projectId} path={path} />;
}
