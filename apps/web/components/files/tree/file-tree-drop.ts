/** Files and folders dropped onto the file tree from the operating system. */
import { jsonFetch, HttpError } from "@/lib/net/fetch-client";
import { joinPath } from "./file-tree-query";

interface DropEntry {
  name: string;
  isFile: boolean;
  isDirectory: boolean;
  file?(success: (file: File) => void, failure: (error: unknown) => void): void;
  createReader?(): { readEntries(success: (entries: DropEntry[]) => void, failure: (error: unknown) => void): void };
}

export interface DroppedFile { path: string; file: File }

/** A drag from the desktop carries files; a drag of tree rows does not. */
export function carriesFiles(transfer: DataTransfer | null): boolean {
  return Boolean(transfer && Array.from(transfer.types).includes("Files"));
}

/** Every file in the drop, with its path relative to the drop. Folder
 *  handles are taken synchronously: the transfer empties after the event. */
export async function collectDroppedFiles(transfer: DataTransfer): Promise<DroppedFile[]> {
  const roots: DropEntry[] = [];
  const loose: File[] = [];
  Array.from(transfer.items).forEach((item, index) => {
    if (item.kind !== "file") return;
    const entry = item.webkitGetAsEntry?.() as DropEntry | null | undefined;
    if (entry) roots.push(entry);
    else {
      const file = item.getAsFile() ?? transfer.files[index];
      if (file) loose.push(file);
    }
  });
  const found: DroppedFile[] = loose.map(file => ({ path: file.name, file }));
  const visit = async (entry: DropEntry, prefix: string): Promise<void> => {
    const path = joinPath(prefix, entry.name);
    if (entry.isFile && entry.file) {
      const file = await new Promise<File>((resolve, reject) => entry.file!(resolve, reject));
      found.push({ path, file });
    } else if (entry.isDirectory && entry.createReader) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise<DropEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child, path);
      }
    }
  };
  for (const entry of roots) await visit(entry, "");
  return found;
}

export interface UploadOutcome { added: number; existing: string[]; failed: string[] }

/** Write each file under ``dir``; an existing name is reported, never replaced. */
export async function uploadDroppedFiles(projectId: string, dir: string, files: DroppedFile[]): Promise<UploadOutcome> {
  const outcome: UploadOutcome = { added: 0, existing: [], failed: [] };
  for (const { path, file } of files) {
    const target = joinPath(dir, path);
    const query = new URLSearchParams({ project_id: projectId, path: target });
    try {
      await jsonFetch(`/api/files/upload?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      outcome.added += 1;
    } catch (error) {
      if (error instanceof HttpError && error.code === "EXISTS") outcome.existing.push(target);
      else outcome.failed.push(target);
    }
  }
  return outcome;
}
