"use client";

import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

import { readPreviewBytes } from "@/lib/documents/read-preview-bytes";
const MAX_CANVAS_PIXELS = 16_000_000;
const MAX_CANVAS_SIDE = 8192;
const PDF_MODULE_URL = "/document-assets/pdfjs/pdf.mjs";
const PDF_ASSET_ROOT = "/document-assets/pdfjs/";

type PdfDocument = {
  numPages: number;
  getPage(page: number): Promise<PdfPage>;
  getMetadata?: () => Promise<unknown>;
  destroy(): Promise<void>;
};
type PdfPage = {
  getViewport(options: { scale: number }): { width: number; height: number };
  render(options: { canvasContext: CanvasRenderingContext2D; viewport: unknown }): { promise: Promise<void>; cancel(): void };
  getTextContent(): Promise<{ items: Array<{ str?: string }> }>;
  cleanup?: () => void;
};
type PdfModule = {
  getDocument(options: Record<string, unknown>): { promise: Promise<PdfDocument>; destroy(): Promise<void> };
  GlobalWorkerOptions: { workerSrc: string };
};
type PdfLoadingTask = { promise: Promise<PdfDocument>; destroy(): Promise<void> };

type SearchHit = { page: number; text: string };

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function boundedViewport(page: PdfPage, scale: number) {
  let viewport = page.getViewport({ scale });
  const pixels = viewport.width * viewport.height;
  if (pixels > MAX_CANVAS_PIXELS || viewport.width > MAX_CANVAS_SIDE || viewport.height > MAX_CANVAS_SIDE) {
    const ratio = Math.min(
      Math.sqrt(MAX_CANVAS_PIXELS / Math.max(1, pixels)),
      MAX_CANVAS_SIDE / Math.max(1, viewport.width),
      MAX_CANVAS_SIDE / Math.max(1, viewport.height),
    );
    viewport = page.getViewport({ scale: scale * Math.min(1, ratio) });
  }
  return viewport;
}

export default function PdfPreview({ sourceUrl, path }: { sourceUrl: string; path: string }) {
  const { text } = useTranslation();
  const [pdf, setPdf] = useState<PdfDocument | null>(null);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1.25);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchComplete, setSearchComplete] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const generationRef = useRef(0);
  const searchRequestRef = useRef(0);
  const renderTaskRef = useRef<{ cancel(): void } | null>(null);
  const { numPages = 0 } = pdf ?? {};

  useEffect(() => {
    const generation = ++generationRef.current;
    const controller = new AbortController();
    let loadingTask: PdfLoadingTask | null = null;
    let documentHandle: PdfDocument | null = null;
    setPdf(null);
    setPage(1);
    setHits([]);
    searchRequestRef.current += 1;
    setSearchComplete(false);
    setError(null);
    setLoading(true);

    void (async () => {
      try {
        const bytes = await readPreviewBytes(sourceUrl, controller.signal);
        const moduleUrl = PDF_MODULE_URL;
        const pdfjs = await import(/* webpackIgnore: true */ moduleUrl) as unknown as PdfModule;
        if (controller.signal.aborted || generation !== generationRef.current) return;
        pdfjs.GlobalWorkerOptions.workerSrc = `${PDF_ASSET_ROOT}pdf.worker.mjs`;
        loadingTask = pdfjs.getDocument({
          data: bytes,
          disableScripting: true,
          isEvalSupported: false,
          enableXfa: false,
          cMapUrl: `${PDF_ASSET_ROOT}cmaps/`,
          cMapPacked: true,
          standardFontDataUrl: `${PDF_ASSET_ROOT}standard_fonts/`,
          wasmUrl: `${PDF_ASSET_ROOT}wasm/`,
        });
        documentHandle = await loadingTask.promise;
        if (controller.signal.aborted || generation !== generationRef.current) {
          const task = loadingTask;
          loadingTask = null;
          await task?.destroy();
          return;
        }
        setPdf(documentHandle);
        setLoading(false);
      } catch (failure) {
        if (controller.signal.aborted || generation !== generationRef.current || isAbort(failure)) return;
        const task = loadingTask;
        loadingTask = null;
        await task?.destroy().catch(() => undefined);
        setLoading(false);
        const message = failure instanceof Error ? failure.message : "";
        setError(message === "PREVIEW_RESOURCE_LIMIT"
          ? text("This PDF is too large to preview (64 MiB limit).", "PDF 超过 64 MiB，无法预览。")
          : text("This PDF could not be opened. The file may be damaged, encrypted, or its local decoder unavailable.", "无法打开该 PDF。文件可能已损坏、已加密，或本地解码器不可用。"));
      }
    })();

    return () => {
      generationRef.current += 1;
      searchRequestRef.current += 1;
      controller.abort();
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const task = loadingTask;
      loadingTask = null;
      void task?.destroy().catch(() => undefined);
    };
  }, [sourceUrl, text]);

  useEffect(() => {
    if (!pdf) return;
    const generation = generationRef.current;
    let cancelled = false;
    let currentPage: PdfPage | null = null;
    let task: ReturnType<PdfPage["render"]> | null = null;
    renderTaskRef.current?.cancel();
    renderTaskRef.current = null;
    void (async () => {
      try {
        currentPage = await pdf.getPage(page);
        if (cancelled || generation !== generationRef.current) return;
        const viewport = boundedViewport(currentPage, scale);
        const canvas = canvasRef.current;
        if (!canvas) return;
        const context = canvas.getContext("2d");
        if (!context) throw new Error(text("Canvas rendering is unavailable.", "Canvas 渲染不可用。"));
        canvas.width = Math.max(1, Math.floor(viewport.width));
        canvas.height = Math.max(1, Math.floor(viewport.height));
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        task = currentPage.render({ canvasContext: context, viewport });
        renderTaskRef.current = task;
        await task.promise;
      } catch (failure) {
        if (!cancelled && generation === generationRef.current && !isAbort(failure)) {
          setError(failure instanceof Error ? failure.message : text("This PDF page could not be rendered.", "无法渲染 PDF 页面。"));
        }
      } finally {
        currentPage?.cleanup?.();
        if (task && renderTaskRef.current === task) renderTaskRef.current = null;
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      currentPage?.cleanup?.();
    };
  }, [pdf, page, scale, text]);

  async function search() {
    const request = ++searchRequestRef.current;
    if (!pdf || !query.trim()) { setHits([]); setSearchComplete(false); return; }
    const needle = query.trim().toLocaleLowerCase();
    const generation = generationRef.current;
    const results: SearchHit[] = [];
    setSearching(true);
    setSearchComplete(false);
    try {
      for (let index = 1; index <= Math.min(pdf.numPages, 500); index += 1) {
        if (generation !== generationRef.current || request !== searchRequestRef.current) return;
        const current = await pdf.getPage(index);
        const content = await current.getTextContent();
        const value = content.items.map((item) => item.str ?? "").join(" ");
        if (value.toLocaleLowerCase().includes(needle)) results.push({ page: index, text: value.slice(0, 240) });
        current.cleanup?.();
      }
      if (generation === generationRef.current && request === searchRequestRef.current) {
        setHits(results);
        setSearchComplete(true);
      }
    } catch (failure) {
      if (generation === generationRef.current && request === searchRequestRef.current) setError(failure instanceof Error ? failure.message : text("PDF search failed.", "PDF 搜索失败。"));
    } finally {
      if (request === searchRequestRef.current) setSearching(false);
    }
  }

  const buttonStyle = { minWidth: 32, minHeight: 30 };
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div role="toolbar" aria-label={text("PDF controls", "PDF 控制")}
        style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", padding: 8, borderBottom: "1px solid var(--border-subtle)" }}>
        <button type="button" style={buttonStyle} disabled={!pdf || page <= 1} aria-label={text("Previous page", "上一页")} onClick={() => setPage((value) => Math.max(1, value - 1))}>‹</button>
        <span aria-live="polite">{pdf ? `${page} / ${numPages}` : text("Loading…", "加载中…")}</span>
        <button type="button" style={buttonStyle} disabled={!pdf || page >= numPages} aria-label={text("Next page", "下一页")} onClick={() => setPage((value) => Math.min(numPages, value + 1))}>›</button>
        <button type="button" style={buttonStyle} disabled={!pdf} aria-label={text("Zoom out", "缩小")} onClick={() => setScale((value) => Math.max(0.75, Number((value - 0.25).toFixed(2))))}>−</button>
        <span>{Math.round(scale * 100)}%</span>
        <button type="button" style={buttonStyle} disabled={!pdf} aria-label={text("Zoom in", "放大")} onClick={() => setScale((value) => Math.min(2.5, Number((value + 0.25).toFixed(2))))}>+</button>
        <form onSubmit={(event) => { event.preventDefault(); void search(); }} style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
          <input aria-label={text("Search PDF", "搜索 PDF")} value={query} onChange={(event) => { searchRequestRef.current += 1; setSearching(false); setSearchComplete(false); setHits([]); setQuery(event.target.value); }} placeholder={text("Search", "搜索")} />
          <button type="submit" disabled={!pdf || !query.trim()}>{text("Find", "查找")}</button>
        </form>
        <a href={sourceUrl} download={path.split("/").pop() ?? "document.pdf"} aria-label={text("Download original PDF", "下载原始 PDF")}><Download size={15} /></a>
      </div>
      {error ? <div role="alert" style={{ padding: 12 }}>{error} <a href={sourceUrl} download={path.split("/").pop() ?? "document.pdf"}>{text("Download original", "下载原文件")}</a></div> : null}
      {searching ? <div role="status" aria-live="polite">{text("Searching…", "搜索中…")}</div> : null}
      {searchComplete && !hits.length ? <div role="status">{text("No matches in the first 500 pages.", "前 500 页中没有匹配项。")}</div> : null}
      {hits.length ? <div aria-label={text("Search results", "搜索结果")} style={{ padding: "4px 8px", borderBottom: "1px solid var(--border-subtle)" }}>{hits.map((hit) => <button type="button" key={hit.page} onClick={() => setPage(hit.page)} style={{ marginRight: 6 }}>{text("Page", "第")} {hit.page}: {hit.text}</button>)}{numPages > 500 ? <span>{text(" (first 500 pages)", "（前 500 页）")}</span> : null}</div> : null}
      <div style={{ flex: 1, overflow: "auto", padding: 16, textAlign: "center", background: "var(--bg-secondary)" }}>
        <canvas ref={canvasRef} aria-label={`${path} ${text("page", "第")} ${page}`} style={{ display: loading || error ? "none" : "inline-block", background: "white", boxShadow: "0 1px 4px rgb(0 0 0 / 20%)" }} />
        {loading && !error ? <p>{text("Loading PDF…", "正在加载 PDF…")}</p> : null}
      </div>
    </div>
  );
}

export { PdfPreview };
