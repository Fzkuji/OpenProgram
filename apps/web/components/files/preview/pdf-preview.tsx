"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import type { PDFViewer, EventBus, PDFLinkService } from "pdfjs-dist/web/pdf_viewer.mjs";
import type { DocumentController } from "@/lib/files/document-controller";
import { useTranslation } from "@/lib/i18n";
import { readPreviewBytes } from "@/lib/documents/read-preview-bytes";
import { normalizePdfCopy } from "@/lib/documents/pdf-copy";
import { bindPdfEditor } from "@/lib/documents/pdf-editor";
import styles from "./pdf-preview.module.css";

const ROOT = "/document-assets/pdfjs/";
type Outline = Awaited<ReturnType<PDFDocumentProxy["getOutline"]>>;
type Runtime = { pdf: PDFDocumentProxy; viewer: PDFViewer; bus: EventBus; links: PDFLinkService };

export default function PdfPreview({ sourceUrl, path, controller, saveStatus }: { sourceUrl: string; path: string; controller?: DocumentController; saveStatus?: string }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const pages = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [scale, setScale] = useState(100);
  const [sidebar, setSidebar] = useState(false);
  const [sidebarMode, setSidebarMode] = useState<"pages" | "outline">("pages");
  const [outline, setOutline] = useState<Outline | null>(null);
  const [query, setQuery] = useState("");
  const activeSearch = useRef(false);
  const [matches, setMatches] = useState({ current: 0, total: 0 });
  const [cleanCopy, setCleanCopy] = useState(true);
  const cleanCopyRef = useRef(cleanCopy); cleanCopyRef.current = cleanCopy;
  const [mode, setMode] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    let task: ReturnType<typeof import("pdfjs-dist")["getDocument"]> | undefined;
    let editor: ReturnType<typeof bindPdfEditor> | undefined;
    let viewer: PDFViewer | undefined;
    const container = host.current!;
    setReady(false); setError(null); setPage(1); setTotal(0); setOutline(null); setMode(0);
    const copy = (event: ClipboardEvent) => {
      const selection = window.getSelection();
      const active = document.activeElement;
      if (!cleanCopyRef.current || !selection || selection.isCollapsed || !event.clipboardData ||
          active?.closest("input,textarea,[contenteditable=true]")) return;
      const layer = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest(".textLayer");
      if (!container.contains(layer(selection.anchorNode) ?? null) || !container.contains(layer(selection.focusNode) ?? null)) return;
      event.clipboardData.setData("text/plain", normalizePdfCopy(selection.toString()));
      event.preventDefault(); event.stopImmediatePropagation();
    };
    window.addEventListener("copy", copy, { capture: true, signal: abort.signal });
    void (async () => {
      try {
        const bytes = await readPreviewBytes(sourceUrl, abort.signal);
        const coreUrl = ROOT + "pdf.mjs", viewerUrl = ROOT + "pdf_viewer.mjs";
        const core = await import(/* webpackIgnore: true */ coreUrl) as typeof import("pdfjs-dist");
        const library = await import(/* webpackIgnore: true */ viewerUrl) as typeof import("pdfjs-dist/web/pdf_viewer.mjs");
        if (abort.signal.aborted) return;
        core.GlobalWorkerOptions.workerSrc = ROOT + "pdf.worker.mjs";
        const bus = new library.EventBus();
        const links = new library.PDFLinkService({ eventBus: bus, externalLinkTarget: 2, externalLinkRel: "noopener noreferrer nofollow" });
        const find = new library.PDFFindController({ eventBus: bus, linkService: links });
        const viewerOptions = { container, viewer: pages.current!, eventBus: bus, linkService: links, findController: find,
          annotationEditorMode: controller ? core.AnnotationEditorType.NONE : core.AnnotationEditorType.DISABLE,
          annotationMode: core.AnnotationMode.ENABLE, enablePermissions: true,
          annotationEditorHighlightColors: "yellow=#FFFF98,green=#A8F2A0,blue=#9FD8FF,pink=#FFC4DD",
          imageResourcesPath: ROOT + "images/", maxCanvasPixels: 16_000_000, maxCanvasDim: 8192,
          enableDetailCanvas: false, abortSignal: abort.signal };
        viewer = new library.PDFViewer(viewerOptions);
        links.setViewer(viewer);
        const currentViewer = viewer;
        bus.on("pagesinit", () => { if (abort.signal.aborted) return; currentViewer.currentScaleValue = "page-width"; setReady(true); }, { signal: abort.signal });
        bus.on("pagechanging", ({ pageNumber }: { pageNumber: number }) => setPage(pageNumber), { signal: abort.signal });
        bus.on("scalechanging", ({ scale: value }: { scale: number }) => setScale(Math.round(value * 100)), { signal: abort.signal });
        bus.on("updatefindmatchescount", ({ matchesCount }: { matchesCount: typeof matches }) => { if (activeSearch.current) setMatches(matchesCount); }, { signal: abort.signal });
        bus.on("annotationeditormodechanged", ({ mode: value }: { mode: number }) => setMode(value), { signal: abort.signal });
        const documentOptions = { data: bytes, isEvalSupported: false, enableXfa: false,
          cMapUrl: ROOT + "cmaps/", cMapPacked: true, standardFontDataUrl: ROOT + "standard_fonts/", wasmUrl: ROOT + "wasm/" };
        task = core.getDocument(documentOptions);
        const pdf = await task.promise;
        if (abort.signal.aborted) return;
        runtime.current = { pdf, viewer, bus, links };
        setTotal(pdf.numPages); links.setDocument(pdf); viewer.setDocument(pdf);
        if (controller) editor = bindPdfEditor(pdf, bus, container, controller);
        const entries = await pdf.getOutline();
        if (!abort.signal.aborted) setOutline(entries);
      } catch (failure) {
        if (abort.signal.aborted) return;
        setError(failure instanceof Error && failure.message === "PREVIEW_RESOURCE_LIMIT"
          ? text("This PDF is too large to preview (64 MiB limit).", "PDF 超过 64 MiB，无法预览。")
          : text("This PDF could not be opened. The file may be damaged, encrypted, or its local decoder unavailable.", "无法打开该 PDF。文件可能已损坏、已加密，或本地解码器不可用。"));
      }
    })();
    return () => { abort.abort(); runtime.current = null; void editor?.destroy(); viewer?.setDocument(null!); void task?.destroy().catch(() => undefined); };
  }, [sourceUrl, controller, text]);

  function go(value: number) { if (runtime.current && Number.isFinite(value)) runtime.current.viewer.currentPageNumber = Math.max(1, Math.min(total, value)); }
  function zoom(value: string) { if (runtime.current) runtime.current.viewer.currentScaleValue = value; }
  function search(previous = false) {
    activeSearch.current = Boolean(query.trim());
    runtime.current?.bus.dispatch("find", { source: host.current, type: "again", query, caseSensitive: false, entireWord: false, highlightAll: true, findPrevious: previous, matchDiacritics: false });
  }
  function annotate(value: number) {
    const viewer = runtime.current?.viewer;
    if (viewer && (viewer.annotationEditorMode as unknown) !== -1) viewer.annotationEditorMode = { mode: value };
  }
  function renderOutline(items: NonNullable<Outline>) {
    return <ul>{items.map((item, index) => <li key={index}><button type="button" disabled={!item.dest} onClick={() => { if (item.dest) void runtime.current?.links.goToDestination(item.dest); }}>{item.title}</button>{item.items.length > 0 && renderOutline(item.items)}</li>)}</ul>;
  }
  return <div data-pdf-reader className={styles.reader} style={{ height: "100%", minHeight: 320, display: "flex", flexDirection: "column" }}>
    <link rel="stylesheet" href={ROOT + "pdf_viewer.css"} />
    <div className={styles.toolbar} role="toolbar" aria-label={text("PDF controls", "PDF 控制")}>
      <button type="button" aria-label={text("Toggle sidebar", "切换侧栏")} aria-pressed={sidebar} onClick={() => setSidebar(!sidebar)}>☷</button>
      <button type="button" disabled={!ready || page <= 1} aria-label={text("Previous page", "上一页")} onClick={() => go(page - 1)}>‹</button>
      <span aria-live="polite">{page} / {total}</span>
      <button type="button" disabled={!ready || page >= total} aria-label={text("Next page", "下一页")} onClick={() => go(page + 1)}>›</button>
      <input className={styles.pageInput} aria-label={text("Go to page", "跳转页码")} type="number" min={1} max={total} defaultValue={page} key={page} onKeyDown={event => { if (event.key === "Enter") go(Number(event.currentTarget.value)); }} />
      <button type="button" disabled={!ready} aria-label={text("Zoom out", "缩小")} onClick={() => zoom(String(Math.max(0.25, scale / 100 - 0.25)))}>−</button>
      <select aria-label={text("Zoom", "缩放")} value="current" disabled={!ready} onChange={event => zoom(event.target.value)}><option value="current">{scale}%</option><option value="page-width">{text("Fit width", "适合宽度")}</option><option value="page-fit">{text("Fit page", "适合页面")}</option>{[50, 75, 100, 125, 150, 200].map(value => <option key={value} value={value / 100}>{value}%</option>)}</select>
      <button type="button" disabled={!ready} aria-label={text("Zoom in", "放大")} onClick={() => zoom(String(Math.min(4, scale / 100 + 0.25)))}>+</button>
      {controller && <><span className={styles.divider} /><button type="button" disabled={!ready} aria-pressed={mode === 0} onClick={() => annotate(0)}>{text("Read", "阅读")}</button><button type="button" disabled={!ready} aria-pressed={mode === 9} onClick={() => annotate(9)}>{text("Highlight", "高亮")}</button><button type="button" disabled={!ready} aria-pressed={mode === 3} onClick={() => annotate(3)}>{text("Text note", "文字批注")}</button><button type="button" disabled={!ready} onClick={() => runtime.current?.bus.dispatch("editingaction", { name: "undo" })}>{text("Undo", "撤销")}</button><button type="button" disabled={!ready} onClick={() => runtime.current?.bus.dispatch("editingaction", { name: "redo" })}>{text("Redo", "重做")}</button><button type="button" disabled={!ready} onClick={() => void controller.flush().catch(() => undefined)}>{text("Save", "保存")}</button></>}
      <form onSubmit={event => { event.preventDefault(); search(); }} className={styles.search}>
        <input aria-label={text("Search PDF", "搜索 PDF")} placeholder={text("Search", "搜索")} value={query} onChange={event => { setQuery(event.target.value); activeSearch.current = false; setMatches({ current: 0, total: 0 }); runtime.current?.bus.dispatch("find", { query: "", type: "", highlightAll: true }); }} />
        <button type="submit" disabled={!ready || !query.trim()}>{text("Find", "查找")}</button>
        <button type="button" disabled={!ready || !query.trim()} aria-label={text("Previous match", "上一个匹配")} onClick={() => search(true)}>↑</button>
        <span aria-label={text("Search results", "搜索结果")}>{matches.current} / {matches.total}</span>
      </form>
      <a href={sourceUrl} download={path.split("/").pop()} aria-label={text("Download original PDF", "下载原始 PDF")}>↓</a>
    </div>
    <div className={styles.options}>{controller && <span role="status">{saveStatus === "idle" ? text("Saved", "已保存") : saveStatus === "saving" ? text("Saving…", "保存中…") : saveStatus === "dirty" ? text("Unsaved changes", "有未保存的修改") : saveStatus === "error" || saveStatus === "conflict" ? text("Save failed", "保存失败") : ""}</span>}<label><input type="checkbox" checked={cleanCopy} onChange={event => setCleanCopy(event.target.checked)} />{text("Remove line breaks when copying", "复制时清除排版换行")}</label>{mode !== 0 && <span>{mode === 9 ? text("Select text to highlight", "选中文字添加高亮") : text("Click a page to add a text note", "点击页面添加文字批注")}</span>}</div>
    {error && <div role="alert">{error} <a href={sourceUrl} download={path.split("/").pop()}>{text("Download original", "下载原文件")}</a></div>}
    <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
      {sidebar && <aside className={styles.sidebar} aria-label={text("PDF navigation", "PDF 导航")}><div><button type="button" aria-pressed={sidebarMode === "pages"} onClick={() => setSidebarMode("pages")}>{text("Pages", "页面")}</button><button type="button" aria-pressed={sidebarMode === "outline"} onClick={() => setSidebarMode("outline")}>{text("Outline", "目录")}</button></div>{sidebarMode === "outline" ? (outline?.length ? renderOutline(outline) : <p>{text("No outline", "没有目录")}</p>) : Array.from({ length: total }, (_, index) => <Thumbnail key={index} pdf={runtime.current?.pdf} page={index + 1} selected={page === index + 1} onClick={() => go(index + 1)} />)}</aside>}
      <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
        <div ref={host} className={styles.viewport} style={{ position: "absolute", inset: 0, overflow: "auto" }} tabIndex={0} aria-label={text("PDF pages", "PDF 页面")}><div ref={pages} className="pdfViewer" /></div>
        {!ready && !error && <span role="status">{text("Loading PDF…", "正在加载 PDF…")}</span>}
      </div>
    </div>
  </div>;
}

function Thumbnail({ pdf, page, selected, onClick }: { pdf?: PDFDocumentProxy; page: number; selected: boolean; onClick(): void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!pdf || !element) return;
    let cancelled = false;
    let task: ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]> | undefined;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.disconnect();
      void pdf.getPage(page).then(async handle => {
        if (cancelled) return;
        const original = handle.getViewport({ scale: 1 });
        const viewport = handle.getViewport({ scale: Math.min(120 / original.width, 160 / original.height) });
        element.width = Math.ceil(viewport.width); element.height = Math.ceil(viewport.height);
        task = handle.render({ canvas: element, viewport }); await task.promise;
      }).catch(() => undefined);
    });
    observer.observe(element);
    return () => { cancelled = true; observer.disconnect(); task?.cancel(); };
  }, [pdf, page]);
  return <button type="button" className={styles.thumbnail} aria-label={`Page ${page}`} aria-current={selected ? "page" : undefined} onClick={onClick}><canvas ref={canvas} style={{ maxWidth: 120, maxHeight: 160 }} /><span>{page}</span></button>;
}

export { PdfPreview };
