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
import { isPermanent, retryDelay } from "@/lib/abilities/kept-read";
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
      } catch (error) {
        if (!abort.signal.aborted) { console.warn("pdf card preview failed", { path, error }); setFailed(true); }
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
    {failed ? null : <canvas ref={canvas} aria-label={text("First page", "第一页")} />}
  </div>;
}

const PAGE_WIDTH = 1280;
/** Pages whose preview was already asked for: the answer holds for the session. */
const checkedPages = new Map<string, "ok" | "failed">();

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
  // the error body as the page; ask first, and draw nothing when refused.
  // A page already checked (the card drawn before) skips the question.
  const [state, setState] = useState<"checking" | "ok" | "failed">(() => checkedPages.get(src) ?? "checking");
  useEffect(() => {
    if (!near || checkedPages.has(src)) return;
    const abort = new AbortController();
    fetch(src, { signal: abort.signal })
      .then(response => {
        void response.body?.cancel();
        const result = response.ok ? "ok" : "failed";
        checkedPages.set(src, result);
        if (!response.ok) console.warn("page card preview refused", { path, status: response.status });
        setState(result);
      })
      .catch(error => { if (!abort.signal.aborted) { console.warn("page card preview failed", { path, error }); setState("failed"); } });
    return () => abort.abort();
  }, [near, src, path]);
  return <div ref={host} className={styles.pageThumb}>
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

/** A Word, PowerPoint or Excel file's first page, drawn by the worker
 *  (``/api/file-thumbnail``: macOS Quick Look, else the picture the
 *  authoring app saved in the file) and cut to the same 16:9 frame as a
 *  PDF. With no picture the card drops its preview for the name row. */
export function OfficeFirstPage({ path, sessionId, onFail }: { path: string; sessionId: string; onFail(): void }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const near = useNearViewport(host);
  const src = `/api/file-thumbnail?path=${encodeURIComponent(path)}&session_id=${encodeURIComponent(sessionId)}`;
  return <div ref={host} className={styles.officePage}>
    {near && <img src={src} alt={text("First page", "第一页")} onError={onFail} />}
  </div>;
}

/** A reply re-mounts its cards when it settles (streaming → final), so
 *  a listing is fetched once per archive and shared, never aborted by a
 *  card going away; a failed read is forgotten so a later card retries. */
const listings = new Map<string, Promise<ArchiveListing>>();
/** Listings already read, so a card drawn again shows its rows on its first frame. */
const readListings = new Map<string, ArchiveListing>();
function sharedListing(path: string, sessionId: string): Promise<ArchiveListing> {
  const key = `${sessionId}\n${path}`;
  let pending = listings.get(key);
  if (!pending) {
    pending = fetchArchiveListing({ kind: "absolute", path, sessionId });
    pending.then(listing => { readListings.set(key, listing); }, () => listings.delete(key));
    listings.set(key, pending);
  }
  return pending;
}

/** The server refused the listing (HTTP 4xx: corrupt or encrypted, too
 *  large, not an archive, gone): a fact about the file, not a load problem.
 *  A network failure or a 5xx is asked again. */
export function archiveListRefused(error: unknown): boolean {
  const status = /^ARCHIVE_LIST_(\d+)$/.exec(error instanceof Error ? error.message : "")?.[1];
  return isPermanent({ status: status ? Number(status) : undefined });
}
/** Archives the server refused to list, so a card drawn again says so at once. */
const unreadableArchives = new Set<string>();

/** The archive's first level, folders first, with a count of the rest. */
export function ArchiveSummary({ path, sessionId, onInfo, onOpen }: { path: string; sessionId: string; onInfo(info: PreviewInfo): void; onOpen(): void }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const near = useNearViewport(host);
  const key = `${sessionId}\n${path}`;
  const [nodes, setNodes] = useState<ArchiveNode[] | null>(() => {
    const held = readListings.get(key);
    return held ? buildArchiveTree(held.entries) : null;
  });
  const [unreadable, setUnreadable] = useState(() => unreadableArchives.has(key));
  const report = useRef(onInfo); report.current = onInfo;
  useEffect(() => {
    if (!near || unreadable) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    const read = () => {
      sharedListing(path, sessionId)
        .then(listing => { if (live) { setNodes(buildArchiveTree(listing.entries)); report.current({ files: listing.total }); } })
        .catch(error => {
          if (!live) return;
          if (archiveListRefused(error)) {
            console.warn("archive card listing refused", { path, error });
            unreadableArchives.add(key);
            setUnreadable(true);
            return;
          }
          console.warn("archive card listing failed; reading again", { path, error });
          timer = setTimeout(read, retryDelay(attempt++));
        });
    };
    read();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [near, path, sessionId, key, unreadable]);
  const shown = nodes?.slice(0, 6) ?? [];
  const rest = (nodes?.length ?? 0) - shown.length;
  return <div ref={host} className="grid px-[14px] pt-[8px] pb-[10px]">
    {unreadable && !nodes ? <span className={styles.previewHint}>{text("Couldn't read this archive.", "无法读取该压缩包。")}</span>
      : !nodes ? null
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
/** Opening lines already read, by session and path: a card drawn again
 *  (the reply settling, the conversation opened again) shows them at once
 *  and refreshes them behind. */
const textHeads = new Map<string, string[]>();

interface FileReadReply { content?: unknown; binary?: unknown }

export function TextHead({ path, sessionId }: { path: string; sessionId: string }) {
  const host = useRef<HTMLPreElement>(null);
  const near = useNearViewport(host);
  const key = `${sessionId}\n${path}`;
  const [lines, setLines] = useState<string[] | null>(() => textHeads.get(key) ?? null);
  useEffect(() => {
    if (!near) return;
    const abort = new AbortController();
    fetch(absFileReadUrl(path, sessionId), { signal: abort.signal })
      .then(async response => (response.ok ? await response.json() as FileReadReply : null))
      .then(body => {
        if (typeof body?.content !== "string" || body.binary) return;
        const head = body.content.split("\n").slice(0, 10);
        textHeads.set(key, head);
        setLines(head);
      })
      .catch(error => { if (!abort.signal.aborted) console.warn("text card preview failed", { path, error }); });
    return () => abort.abort();
  }, [near, path, sessionId, key]);
  return <pre ref={host} className={styles.textHead}>{lines?.join("\n") ?? ""}</pre>;
}
