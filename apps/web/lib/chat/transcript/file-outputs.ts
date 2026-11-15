import { fileCapabilities } from "../../documents/file-formats.ts";

export interface FileOutput { path: string; name: string; }
/** The reply's file cards: the attachments the server put on the message
 *  (``send_file`` and the files the turn produced, ``send_file.py``), in
 *  order and once each. A path linked from the prose stays a link; which
 *  files get a card is decided on the server alone. */
export function replyFiles(attachments: { path: string; filename: string }[]): FileOutput[] {
  const files = new Map<string, FileOutput>();
  for (const { path, filename } of attachments)
    if (path && !files.has(path)) files.set(path, { path, name: filename || path.replace(/\\/g, "/").split("/").pop() || path });
  return [...files.values()];
}

export function previewTarget(path: string, project?: { id: string; path: string } | null) {
  const root = project?.path.replace(/\\/g, "/").replace(/\/$/, "");
  const normalized = path.replace(/\\/g, "/");
  if (root && normalized.startsWith(root + "/")) {
    const relative = normalized.slice(root.length + 1);
    if (!relative.split("/").some(part => !part || part === "." || part === ".."))
      return { projectId: project!.id, path: relative, readOnly: false };
  }
  return { projectId: "", path, readOnly: true };
}

export type InlineMediaKind = "image" | "video" | "audio";
/** An attached image, video or audio file plays in the reply itself. */
export function inlineMediaKind(path: string): InlineMediaKind | null {
  const kind = fileCapabilities(path).preview;
  return kind === "image" || kind === "video" || kind === "audio" ? kind : null;
}
