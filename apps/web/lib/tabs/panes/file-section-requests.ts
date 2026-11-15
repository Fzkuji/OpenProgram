import type { CenterTab } from "../store/types";

export interface FileSectionRequest { sequence: number; document: string; fragment: string; }
export type FileSectionRequests = Record<string, FileSectionRequest>;
export interface FileSectionNavigation { request: FileSectionRequest; consume: (sequence: number) => void; }
export function sectionDocument(tab: Pick<CenterTab, "kind" | "projectId" | "path" | "readOnly" | "fileSessionId">): string | null {
  return tab.kind === "file" && tab.path ? JSON.stringify([tab.projectId ?? "", tab.path, !!tab.readOnly, tab.fileSessionId ?? ""]) : null;
}
export function pruneFileSections(requests: FileSectionRequests, tabs: CenterTab[]): FileSectionRequests {
  const entries = Object.entries(requests).filter(([id, request]) => {
    const tab = tabs.find(item => item.id === id);
    return tab && sectionDocument(tab) === request.document;
  });
  return entries.length === Object.keys(requests).length ? requests : Object.fromEntries(entries);
}
export function requestFileSection(requests: FileSectionRequests = {}, tab: CenterTab, fragment: string | undefined, sequence: number): FileSectionRequests {
  const document = sectionDocument(tab);
  const next = Object.fromEntries(Object.entries(requests).filter(([id, request]) => id !== tab.id && request.document !== document));
  if (fragment && document) next[tab.id] = { sequence, document, fragment };
  return next;
}
