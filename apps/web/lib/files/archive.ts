/** Zip and tar files read in place through the worker's archive routes:
 *  the listing for a chat card or the archive viewer, and one member's
 *  bytes for previewing it. Nothing is extracted to disk. */

export type ArchiveSource = { kind: "absolute"; path: string; sessionId?: string } | { kind: "project"; projectId: string; path: string };
export interface ArchiveEntry { path: string; size: number; dir: boolean; }
export interface ArchiveListing { format: "zip" | "tar"; entries: ArchiveEntry[]; total: number; truncated: boolean; }
export interface ArchiveNode { name: string; path: string; size: number; dir: boolean; children: ArchiveNode[]; files: number; }

const TAR_SUFFIXES = [".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"];

export function isArchivePath(path: string): boolean {
  const name = (path.replace(/\\/g, "/").split("/").pop() ?? "").toLowerCase();
  return name.endsWith(".zip") || TAR_SUFFIXES.some(suffix => name.endsWith(suffix));
}

function query(source: ArchiveSource): string {
  return source.kind === "absolute"
    ? `path=${encodeURIComponent(source.path)}${source.sessionId ? `&session_id=${encodeURIComponent(source.sessionId)}` : ""}`
    : `project_id=${encodeURIComponent(source.projectId)}&path=${encodeURIComponent(source.path)}`;
}

export function archiveMemberUrl(source: ArchiveSource, member: string): string {
  return `/api/archive-member?${query(source)}&member=${encodeURIComponent(member)}`;
}

export async function fetchArchiveListing(source: ArchiveSource, signal?: AbortSignal): Promise<ArchiveListing> {
  const response = await fetch(`/api/archive-list?${query(source)}`, { signal });
  if (!response.ok) throw new Error(`ARCHIVE_LIST_${response.status}`);
  return response.json() as Promise<ArchiveListing>;
}

/** The flat listing as a tree: folders first, then files, each by name;
 *  every folder knows how many files it holds at any depth. */
export function buildArchiveTree(entries: ArchiveEntry[]): ArchiveNode[] {
  const root: ArchiveNode = { name: "", path: "", size: 0, dir: true, children: [], files: 0 };
  const byPath = new Map<string, ArchiveNode>([["", root]]);
  const sorted = [...entries].sort((a, b) => a.path.split("/").length - b.path.split("/").length);
  for (const entry of sorted) {
    if (byPath.has(entry.path)) continue;
    const slash = entry.path.lastIndexOf("/");
    const parent = byPath.get(slash < 0 ? "" : entry.path.slice(0, slash)) ?? root;
    const node: ArchiveNode = { name: entry.path.slice(slash + 1), path: entry.path, size: entry.size, dir: entry.dir, children: [], files: 0 };
    parent.children.push(node);
    byPath.set(entry.path, node);
  }
  const finish = (node: ArchiveNode): number => {
    node.children.sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
    node.files = node.children.reduce((sum, child) => sum + (child.dir ? finish(child) : 1), 0);
    return node.files;
  };
  finish(root);
  return root.children;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
