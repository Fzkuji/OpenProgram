/** A drop captures directory handles synchronously; only their first level is read. */
export interface DroppedEntry {
  name: string;
  isDirectory: boolean;
  createReader?(): { readEntries(success: (entries: DroppedEntry[]) => void, failure: (error: unknown) => void): void };
}

export function captureDropEntries(transfer: DataTransfer): Map<File, DroppedEntry> {
  const entries = new Map<File, DroppedEntry>();
  let fileIndex = 0;
  Array.from(transfer.items).forEach((item) => {
    if (item.kind !== "file") return;
    const file = transfer.files[fileIndex++] || item.getAsFile();
    const entry = item.webkitGetAsEntry?.();
    if (file && entry?.isDirectory) entries.set(file, entry as DroppedEntry);
  });
  return entries;
}

export async function firstDirectoryLevel(entry: DroppedEntry): Promise<string> {
  if (!entry.isDirectory || !entry.createReader) throw new Error("Cannot read folder");
  const reader = entry.createReader();
  const names: string[] = [];
  let bytes = 0;
  for (;;) {
    const batch = await new Promise<DroppedEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return names.join("\n") || "(empty folder)";
    for (const child of batch) {
      const line = JSON.stringify(child.name + (child.isDirectory ? "/" : ""));
      bytes += new TextEncoder().encode(line).length + 1;
      if (names.length >= 200 || bytes > 8192) return [...names, "… (first level truncated)"].join("\n");
      names.push(line);
    }
  }
}
