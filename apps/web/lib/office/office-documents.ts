/**
 * The ONLYOFFICE window's two jobs outside the editor itself: a new blank
 * document in the current project, and the Office documents already there.
 * A new file is one of the blank templates under /office-templates/,
 * written through the same upload route a dropped file uses, so it never
 * replaces an existing name.
 */
import { HttpError, jsonFetch } from "@/lib/net/fetch-client";
import { fileCapabilities } from "@/lib/documents/file-formats";
import { fileResponseMatchesOwner } from "@/lib/files/drafts/file-state-shared";
import { wsRequest } from "@/lib/net/ws/ws-request";

export type OfficeKind = "docx" | "xlsx" | "pptx";

const UNTITLED: Record<OfficeKind, string> = {
  docx: "Untitled document",
  xlsx: "Untitled spreadsheet",
  pptx: "Untitled presentation",
};

/** The n-th name tried for a new document: "Untitled document.docx",
 *  then "Untitled document 2.docx", and so on. */
export function untitledName(kind: OfficeKind, attempt: number): string {
  return `${UNTITLED[kind]}${attempt > 1 ? ` ${attempt}` : ""}.${kind}`;
}

/** Writes a blank document at the project root; returns its relative path. */
export async function createOfficeDocument(projectId: string, kind: OfficeKind): Promise<string> {
  const blank = await fetch(`/office-templates/blank.${kind}`);
  if (!blank.ok) throw new Error(`blank ${kind} template is missing (${blank.status})`);
  const body = await blank.blob();
  for (let attempt = 1; attempt <= 50; attempt += 1) {
    const path = untitledName(kind, attempt);
    const query = new URLSearchParams({ project_id: projectId, path });
    try {
      await jsonFetch(`/api/files/upload?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body,
      });
      return path;
    } catch (error) {
      if (!(error instanceof HttpError && error.code === "EXISTS")) throw error;
    }
  }
  throw new Error("too many untitled documents; rename some first");
}

const SEARCHED = [".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".doc", ".xls", ".ppt"];

interface SearchPage { results?: { path?: string; name: string; type: string; mtime?: number }[]; error_code?: string | null }

export interface OfficeDocumentList {
  paths: string[];
  /** The project has more files than the search reads (the home-folder default project). */
  tooLarge: boolean;
}

/** Office documents anywhere in a project (relative paths), most recently
 *  changed first, through the Files panel's project search. A project
 *  larger than that search reads is reported as such, not as empty. */
export async function listOfficeDocuments(projectId: string): Promise<OfficeDocumentList> {
  const found = new Map<string, number>();
  let tooLarge = false;
  await Promise.all(SEARCHED.map(async (needle) => {
    const payload = { project_id: projectId, path: "", query: needle, mode: "contains", type: "file", page_size: 100 };
    const page = await wsRequest<SearchPage>("project_file_search", payload, "project_file_search_result",
      data => fileResponseMatchesOwner(data as unknown as Record<string, unknown>, payload), 8000).catch(() => null);
    if (page?.error_code === "LIMIT_EXCEEDED") tooLarge = true;
    for (const row of page?.results ?? []) {
      const path = row.path ?? row.name;
      if (row.type === "file" && fileCapabilities(path).preview === "office") found.set(path, row.mtime ?? 0);
    }
  }));
  return { paths: [...found].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([path]) => path), tooLarge };
}
