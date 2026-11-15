"use client";

/** A zip or tar file opened in a tab: its whole tree on the left and the
 *  selected member on the right, read from the archive in place. The
 *  member goes through the same FileViewer every other file uses, so an
 *  image, PDF, Markdown or code file inside looks as it would on disk. */
import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Folder, FolderOpen } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { archiveMemberUrl, buildArchiveTree, fetchArchiveListing, formatBytes, type ArchiveListing, type ArchiveNode, type ArchiveSource } from "@/lib/files/archive";
import { fileCapabilities } from "@/lib/documents/file-formats";
import type { FileReadResult } from "@/lib/files/files-shared";
import { FileTypeIcon } from "../file-type-icon";
import { FileViewer } from "../file-viewer";
import styles from "./archive-preview.module.css";

type Member = { path: string; blob?: Blob; snapshot?: FileReadResult; error?: string };

export function ArchivePreview({ source }: { source: ArchiveSource }) {
  const { text } = useTranslation();
  const [listing, setListing] = useState<ArchiveListing | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [member, setMember] = useState<Member | null>(null);
  const key = source.kind === "absolute" ? `${source.path}\n${source.sessionId ?? ""}` : `${source.projectId}\n${source.path}`;

  useEffect(() => {
    const abort = new AbortController();
    setListing(null); setFailed(false); setMember(null);
    fetchArchiveListing(source, abort.signal)
      .then(result => {
        setListing(result);
        // A single top folder is opened, so the first view shows files.
        const top = buildArchiveTree(result.entries);
        if (top.length === 1 && top[0].dir) setOpen(new Set([top[0].path]));
      })
      .catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the source's identity.
  }, [key]);

  const tree = useMemo(() => listing ? buildArchiveTree(listing.entries) : [], [listing]);

  async function select(node: ArchiveNode) {
    setMember({ path: node.path });
    try {
      const response = await fetch(archiveMemberUrl(source, node.path));
      if (!response.ok) throw new Error(response.status === 413 ? "large" : "read");
      const blob = await response.blob();
      const textual = fileCapabilities(node.path).preview === "text";
      const content = textual ? await blob.text() : undefined;
      const binary = textual && content!.slice(0, 8192).includes("\0");
      setMember(current => current?.path !== node.path ? current : { path: node.path, blob, snapshot: {
        project_id: "", path: node.path, size: blob.size, mtime: 0,
        binary: !textual || binary, content: textual && !binary ? content : undefined,
      } as FileReadResult });
    } catch (error) {
      setMember(current => current?.path !== node.path ? current : { path: node.path, error: error instanceof Error && error.message === "large"
        ? text("This file is too large to preview from the archive.", "该文件太大，无法从压缩包中预览。")
        : text("Couldn't read this file from the archive.", "无法从压缩包中读取该文件。") });
    }
  }

  function rows(nodes: ArchiveNode[], depth: number): React.ReactNode[] {
    return nodes.flatMap(node => {
      const expanded = open.has(node.path);
      const row = <button key={node.path} type="button" className={styles.row} style={{ paddingLeft: 8 + depth * 16 }}
        aria-expanded={node.dir ? expanded : undefined} aria-current={member?.path === node.path ? "true" : undefined}
        onClick={() => {
          if (!node.dir) { void select(node); return; }
          setOpen(current => { const next = new Set(current); if (next.has(node.path)) next.delete(node.path); else next.add(node.path); return next; });
        }}>
        <ChevronRight size={14} aria-hidden className={styles.chevron} data-open={expanded ? "" : undefined} style={{ visibility: node.dir ? "visible" : "hidden" }} />
        {node.dir ? (expanded ? <FolderOpen size={16} aria-hidden className={styles.folder} /> : <Folder size={16} aria-hidden className={styles.folder} />) : <FileTypeIcon name={node.name} size={16} />}
        <span className={styles.name}>{node.name}</span>
        <span className={styles.meta}>{node.dir ? node.files : formatBytes(node.size)}</span>
      </button>;
      return node.dir && expanded ? [row, ...rows(node.children, depth + 1)] : [row];
    });
  }

  if (failed) return <div className={styles.hint}>{text("Couldn't read this archive. It may be damaged, encrypted, or too large.", "无法读取该压缩包。文件可能已损坏、已加密或过大。")}</div>;
  if (!listing) return <div className={styles.hint}>{text("Loading…", "加载中…")}</div>;
  return <div className="grid h-full min-h-0 grid-cols-[minmax(220px,320px)_minmax(0,1fr)] @max-[560px]:grid-cols-1 @max-[560px]:grid-rows-[minmax(160px,40%)_1fr]">
    <nav className={styles.tree} aria-label={text("Archive contents", "压缩包内容")}>
      <div className={styles.summary}>
        {listing.format.toUpperCase()} · {text(`${listing.total} files`, `${listing.total} 个文件`)}
        {listing.truncated && <span> · {text("list cut short", "列表已截断")}</span>}
      </div>
      {tree.length ? rows(tree, 0) : <div className={styles.hint}>{text("Empty archive", "空压缩包")}</div>}
    </nav>
    <section className="flex min-h-0 min-w-0 flex-col overflow-auto" aria-live="polite">
      {!member ? <div className={styles.hint}>{text("Choose a file to preview it.", "选择一个文件进行预览。")}</div>
        : member.error ? <div className={styles.hint}>{member.error}</div>
          : !member.snapshot ? <div className={styles.hint}>{text("Loading…", "加载中…")}</div>
            : <FileViewer key={member.path} projectId="" path={member.path} inArchive
              sourceBlob={member.blob} snapshot={member.snapshot} mdRendered />}
    </section>
  </div>;
}
