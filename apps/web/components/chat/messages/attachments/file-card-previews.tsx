"use client";

/** The preview area of a reply's file card: what the file shows before
 *  anyone opens it. Each kind loads only once its card nears the screen,
 *  so a long transcript does not decode every PDF or page it ever sent. */
import { useEffect, useRef, useState, type RefObject } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { Folder } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { absFileReadUrl, absRawFileUrl } from "@/lib/files/files-shared";
import { readPreviewBytes } from "@/lib/documents/read-preview-bytes";
import { buildArchiveTree, fetchArchiveListing, formatBytes, type ArchiveListing, type ArchiveNode } from "@/lib/files/archive";
import { FileTypeIcon } from "@/components/files/file-type-icon";
import styles from "./assistant-file-cards.module.css";

export interface PreviewInfo { pages?: number; files?: number; }

function useNearViewport(ref: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || near) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setNear(true); observer.disconnect(); }
    }, { rootMargin: "400px 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, near]);
  return near;
}

function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

const PDF_ROOT = "/document-assets/pdfjs/";

/** The first page, as wide as the card. A tall page is cut at the
 *  preview's height; the whole document is one click away. */
export function PdfFirstPage({ path, sessionId, onInfo }: { path: string; sessionId: string; onInfo(info: PreviewInfo): void }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const near = useNearViewport(host);
  const width = useWidth(host);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [failed, setFailed] = useState(false);
  const report = useRef(onInfo); report.current = onInfo;

  useEffect(() => {
    if (!near) return;
    const abort = new AbortController();
    let task: ReturnType<typeof import("pdfjs-dist")["getDocument"]> | undefined;
    void (async () => {
      try {
        const bytes = await readPreviewBytes(absRawFileUrl(path, sessionId), abort.signal);
        const core = await import(/* webpackIgnore: true */ PDF_ROOT + "pdf.mjs") as typeof import("pdfjs-dist");
        core.GlobalWorkerOptions.workerSrc = PDF_ROOT + "pdf.worker.mjs";
        if (abort.signal.aborted) return;
        // Same decoder settings as the full reader (pdf-preview.tsx).
        const options = { data: bytes, isEvalSupported: false, enableXfa: false,
          cMapUrl: PDF_ROOT + "cmaps/", cMapPacked: true, standardFontDataUrl: PDF_ROOT + "standard_fonts/", wasmUrl: PDF_ROOT + "wasm/" };
        task = core.getDocument(options);
        const loaded = await task.promise;
        if (abort.signal.aborted) return;
        setPdf(loaded);
        report.current({ pages: loaded.numPages });
      } catch {
        if (!abort.signal.aborted) setFailed(true);
      }
    })();
    return () => { abort.abort(); void task?.destroy().catch(() => undefined); };
  }, [near, path, sessionId]);

  useEffect(() => {
    const element = canvas.current;
    if (!pdf || !element || width <= 0) return;
    let cancelled = false;
    let task: { cancel(): void; promise: Promise<void> } | undefined;
    void pdf.getPage(1).then(page => {
      if (cancelled) return;
      const ratio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: (width / page.getViewport({ scale: 1 }).width) * ratio });
      element.width = Math.ceil(viewport.width); element.height = Math.ceil(viewport.height);
      element.style.width = `${width}px`; element.style.height = `${Math.ceil(viewport.height / ratio)}px`;
      task = page.render({ canvas: element, viewport });
      return task.promise;
    }).catch(() => undefined);
    return () => { cancelled = true; task?.cancel(); };
  }, [pdf, width]);

  return <div ref={host} className={styles.pdfPage}>
    {failed ? <span className={styles.previewHint}>{text("Preview unavailable", "无法生成预览")}</span>
      : <canvas ref={canvas} aria-label={text("First page", "第一页")} />}
  </div>;
}

const PAGE_WIDTH = 1280;

/** The page drawn in a 1280×720 desktop window and scaled into the card's 16:9 frame. The worker
 *  serves it under a ``sandbox allow-scripts`` policy, so its scripts run
 *  with an opaque origin: the page cannot reach the app or its storage,
 *  and cannot navigate or open windows. Files the page loads by relative
 *  path are not served here; the opened page has them. */
export function HtmlThumbnail({ path, sessionId }: { path: string; sessionId: string }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const near = useNearViewport(host);
  const width = useWidth(host);
  const scale = width > 0 ? width / PAGE_WIDTH : 0;
  const src = `/api/file-page?path=${encodeURIComponent(path)}&session_id=${encodeURIComponent(sessionId)}`;
  // The frame cannot report a refused or missing file, and would show
  // the error body as the page; ask first and say so plainly instead.
  const [state, setState] = useState<"checking" | "ok" | "failed">("checking");
  useEffect(() => {
    if (!near) return;
    const abort = new AbortController();
    fetch(src, { signal: abort.signal })
      .then(response => { void response.body?.cancel(); setState(response.ok ? "ok" : "failed"); })
      .catch(() => { if (!abort.signal.aborted) setState("failed"); });
    return () => abort.abort();
  }, [near, src]);
  return <div ref={host} className={styles.pageThumb}>
    {state === "failed" && <span className={styles.previewHint}>{text("Preview unavailable", "无法生成预览")}</span>}
    {state === "ok" && scale > 0 && <iframe
      title={text("Page preview", "页面预览")}
      src={src}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      loading="lazy"
      tabIndex={-1}
      aria-hidden="true"
      style={{ width: PAGE_WIDTH, height: PAGE_WIDTH * 9 / 16, transform: `scale(${scale})` }}
    />}
  </div>;
}

/** A reply re-mounts its cards when it settles (streaming → final), so
 *  a listing is fetched once per archive and shared, never aborted by a
 *  card going away; a failed read is forgotten so a later card retries. */
const listings = new Map<string, Promise<ArchiveListing>>();
function sharedListing(path: string, sessionId: string): Promise<ArchiveListing> {
  const key = `${sessionId}\n${path}`;
  let pending = listings.get(key);
  if (!pending) {
    pending = fetchArchiveListing({ kind: "absolute", path, sessionId });
    pending.catch(() => listings.delete(key));
    listings.set(key, pending);
  }
  return pending;
}

/** The archive's first level, folders first, with a count of the rest. */
export function ArchiveSummary({ path, sessionId, onInfo, onOpen }: { path: string; sessionId: string; onInfo(info: PreviewInfo): void; onOpen(): void }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const near = useNearViewport(host);
  const [nodes, setNodes] = useState<ArchiveNode[] | null>(null);
  const [failed, setFailed] = useState(false);
  const report = useRef(onInfo); report.current = onInfo;
  useEffect(() => {
    if (!near) return;
    let live = true;
    sharedListing(path, sessionId)
      .then(listing => { if (live) { setNodes(buildArchiveTree(listing.entries)); report.current({ files: listing.total }); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [near, path, sessionId]);
  const shown = nodes?.slice(0, 6) ?? [];
  const rest = (nodes?.length ?? 0) - shown.length;
  return <div ref={host} className="grid px-[14px] pt-[8px] pb-[10px]">
    {failed ? <span className={styles.previewHint}>{text("Couldn't read this archive.", "无法读取该压缩包。")}</span>
      : !nodes ? <span className={styles.previewHint}>{text("Loading…", "加载中…")}</span>
        : nodes.length === 0 ? <span className={styles.previewHint}>{text("Empty archive", "空压缩包")}</span>
          : <>
            {shown.map(node => <div key={node.path} className={styles.archiveRow}>
              {node.dir ? <Folder size={16} aria-hidden className={styles.folderIcon} /> : <FileTypeIcon name={node.name} size={16} />}
              <span className={styles.archiveName}>{node.dir ? `${node.name}/` : node.name}</span>
              <span className={styles.archiveMeta}>{node.dir ? text(`${node.files} files`, `${node.files} 个文件`) : formatBytes(node.size)}</span>
            </div>)}
            {rest > 0 && <button type="button" className={styles.archiveMore} onClick={onOpen}>{text(`${rest} more`, `还有 ${rest} 项`)}</button>}
          </>}
  </div>;
}

/** The opening lines of a text file. */
export function TextHead({ path, sessionId }: { path: string; sessionId: string }) {
  const { text } = useTranslation();
  const host = useRef<HTMLElement>(null);
  const near = useNearViewport(host);
  const [lines, setLines] = useState<string[] | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!near) return;
    const abort = new AbortController();
    fetch(absFileReadUrl(path, sessionId), { signal: abort.signal })
      .then(response => response.ok ? response.json() as Promise<{ content?: string; binary?: boolean }> : null)
      .then(body => setLines(body?.content !== undefined && !body.binary ? body.content.split("\n").slice(0, 10) : null))
      .then(() => undefined, () => undefined)
      .finally(() => { if (!abort.signal.aborted) setDone(true); });
    return () => abort.abort();
  }, [near, path, sessionId]);
  if (done && lines === null) return <span ref={host as RefObject<HTMLSpanElement>} className={styles.previewHint}>{text("Preview unavailable", "无法生成预览")}</span>;
  return <pre ref={host as RefObject<HTMLPreElement>} className={styles.textHead}>{lines?.join("\n") ?? ""}</pre>;
}
