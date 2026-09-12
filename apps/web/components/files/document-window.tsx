"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { EditorArea, FileViewer } from "./file-viewer";
import { getOrCreateDocumentController } from "@/lib/state/document-controller";
import type { DocumentHistoryEntry } from "@/lib/state/document-types";
import styles from "./document-window.module.css";

import { fileCapabilities } from "@/lib/documents/file-formats";
import { rawFileUrl, absRawFileUrl } from "@/lib/state/files-shared";
import { officeCapability } from "@/lib/documents/file-formats";
import { convertOfficeDocument } from "@/lib/documents/office-editor";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { OfficeSurface } from "./office-surface";
import { RasterSurface } from "./raster-surface";
import type { RasterEditorInstance } from "@/lib/documents/raster-editor";

interface VersionPreview { blob: Blob; content?: string; version?: string; side?: "before" | "after"; disk?: boolean; }

export function DocumentWindow({ projectId, path, sessionId, readOnly = false }: {
  projectId: string; path: string; sessionId?: string; readOnly?: boolean;
}) {
  const { text } = useTranslation();
  const controller = useMemo(() => getOrCreateDocumentController({ projectId, path, sessionId, readOnly }),
    [projectId, path, sessionId, readOnly]);
  const [state, setState] = useState(controller.getState());
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [converting, setConverting] = useState(false);
  const conversion = useRef<{ path: string; key: string; bytes: File } | null>(null);
  const conversionAbort = useRef<AbortController | null>(null);
  useEffect(() => () => conversionAbort.current?.abort(), []);
  const [editorOpened, setEditorOpened] = useState(false);
  const [content, setContent] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistoryEntry[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<VersionPreview | null>(null);
  const selectionRequest = useRef(0);
  const editedBytes = useRef<Blob | null>(null);
  const rasterEditor = useRef<RasterEditorInstance | null>(null);
  const isText = fileCapabilities(path).textEditable && !state.snapshot?.binary;
  const office = officeCapability(path);
  const isOffice = fileCapabilities(path).preview === "office";
  const isRaster = ["png", "jpg", "jpeg", "webp"].includes(fileCapabilities(path).preview === "image" ? path.split(".").pop()?.toLowerCase() ?? "" : "");
  const currentBytes = state.draft ?? state.snapshot?.bytes;

  useEffect(() => controller.subscribe(setState), [controller]);
  useEffect(() => {
    let active = true;
    void controller.load().catch((failure) => { if (active) setError(String(failure.message ?? failure)); });
    return () => { active = false; selectionRequest.current++; };
  }, [controller]);
  useEffect(() => {
    let active = true;
    if (currentBytes && currentBytes !== editedBytes.current && isText) void currentBytes.text().then((value) => { if (active) setContent(value); });
    return () => { active = false; };
  }, [currentBytes, isText]);

  async function perform(action: () => Promise<unknown>) {
    try { await action(); setError(null); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
  }
  async function openHistory(cursor?: string) {
    setHistoryOpen(true);
    await perform(async () => {
      const page = await controller.listHistory(25, cursor);
      setHistory((previous) => cursor ? [...previous, ...page.entries] : page.entries);
      setHistoryCursor(page.next_cursor ?? null);
    });
  }
  async function previewVersion(version: string, side: "before" | "after") {
    const request = ++selectionRequest.current;
    await perform(async () => {
      const blob = await controller.historyContent(version, side);
      const value = isText ? await blob.text() : undefined;
      if (request === selectionRequest.current) setSelected({ blob, content: value, version, side });
    });
  }
  async function convert() {
    if (converting || !office?.conversionRequired || !currentBytes || readOnly) return;
    const target = window.prompt(text("New file path", "新文件路径"), path.replace(/\.[^.]+$/, `.${office.format}`));
    if (target === null) return;
    if (!target.toLowerCase().endsWith(`.${office.format}`)) {
      setError(text(`The new file must use .${office.format}.`, `新文件必须使用 .${office.format} 扩展名。`)); return;
    }
    setConverting(true);
    const abort = new AbortController(); conversionAbort.current = abort;
    try {
      if (!conversion.current || conversion.current.path !== target) {
        const bytes = await convertOfficeDocument(controller, currentBytes, path.split("/").pop()!, office.format, abort.signal);
        conversion.current = { path: target, bytes, key: crypto.randomUUID() };
      }
      abort.signal.throwIfAborted();
      await controller.publishNewDocument(target, conversion.current.bytes, conversion.current.key);
      if (!abort.signal.aborted) {
        setError(null);
        useCenterTabs.getState().openFileTab(projectId, target);
      }
    } catch (failure) {
      if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure));
    } finally { if (!abort.signal.aborted) setConverting(false); }
  }
  async function showCurrent() {
    await perform(async () => {
      if (isOffice && !readOnly) await controller.flush();
      selectionRequest.current++; setSelected(null); setMode("preview");
    });
  }
  async function restore(version: string, side: "before" | "after" = "after") {
    if (!window.confirm(text("Restore this version?", "恢复此版本？"))) return;
    await perform(async () => { await controller.restore(version, side); showCurrent(); await openHistory(); });
  }
  async function showDisk() {
    await perform(async () => {
      const disk = await controller.readDisk();
      setSelected({ blob: disk.bytes, content: isText ? await disk.bytes.text() : undefined, disk: true });
    });
  }
  function exportDraft() {
    if (!state.draft) return;
    const url = URL.createObjectURL(state.draft);
    const link = document.createElement("a");
    link.href = url; link.download = path.split("/").pop() ?? "document";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  async function discard() {
    if (!window.confirm(text("Discard your local changes and read the file from disk?", "丢弃本地修改并重新读取磁盘文件？"))) return;
    await perform(async () => { await controller.discardDraft(); showCurrent(); });
  }
  const snapshot = state.snapshot ? { project_id: projectId, path,
    content: isText ? content : undefined, binary: !isText,
    size: currentBytes?.size ?? 0, mtime: state.snapshot.mtime ?? 0, revision: state.snapshot.revision } : null;

  return <div className={styles.window} data-document-window="true" onKeyDown={(event) => {
    if (!readOnly && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void perform(() => controller.flush());
    }
  }}>
    <div className={styles.toolbar} role="toolbar" aria-label={text("Document", "文档")}>
      <span className={styles.title}>{path.split("/").pop()}</span><span className={styles.spacer} />
      <button className={`${styles.button} ${mode === "preview" && !selected ? styles.active : ""}`}
        aria-pressed={mode === "preview" && !selected} onClick={() => void showCurrent()}>{text("Preview", "预览")}</button>
      {!readOnly && (isText || (isOffice && office?.editable) || isRaster) && <button className={`${styles.button} ${mode === "edit" && !selected ? styles.active : ""}`}
        disabled={!state.snapshot || state.restoring || state.renaming} aria-pressed={mode === "edit" && !selected}
        onClick={() => { setSelected(null); setEditorOpened(true); setMode("edit"); }}>{text("Edit", "编辑")}</button>}
      {!readOnly && office?.conversionRequired && !selected && <button className={styles.button}
        disabled={!currentBytes || converting} onClick={() => void convert()}>
        {text(`Convert to ${office.format.toUpperCase()}`, `转换为 ${office.format.toUpperCase()}`)}</button>}
      {isOffice && <a className={styles.button} href={readOnly ? absRawFileUrl(path, sessionId) : rawFileUrl(projectId, path)}
        download={path.split("/").pop()}>{text("Download disk file", "下载磁盘文件")}</a>}
      {!readOnly && <button className={styles.button} aria-expanded={historyOpen}
        onClick={() => historyOpen ? setHistoryOpen(false) : void openHistory()}>{text("History", "历史")}</button>}
    </div>
    {(error || state.error) && <div className={styles.error} role="alert">
      {error || state.error}
      {(state.status === "error" || state.status === "conflict") && <span>
        <button className={styles.button} onClick={() => void perform(() => state.snapshot ? controller.flush() : controller.load())}>{text("Retry", "重试")}</button>
        <button className={styles.button} onClick={() => void showDisk()}>{text("View disk version", "查看磁盘版本")}</button>
        <button className={styles.button} disabled={!state.draft} onClick={exportDraft}>{text("Export draft", "导出草稿")}</button>
        <button className={styles.button} disabled={!state.draft} onClick={() => void discard()}>{text("Discard draft", "丢弃草稿")}</button>
      </span>}
    </div>}
    {selected && <div className={styles.toolbar}>
      <span>{selected.disk ? text("Disk version", "磁盘版本") : text("History version", "历史版本")}</span>
      <button className={styles.button} onClick={showCurrent}>{text("Back to current file", "返回当前文件")}</button>
      {selected.version && <button className={styles.button} disabled={state.restoring || state.renaming}
        onClick={() => void restore(selected.version!, selected.side)}>{text("Restore this version", "恢复此版本")}</button>}
    </div>}
    {isRaster && editorOpened && mode === "edit" && !selected && <div className={styles.toolbar} role="toolbar" aria-label={text("Image tools", "图片工具")}>
      <button className={styles.button} onClick={() => void rasterEditor.current?.rotate()}>{text("Rotate", "旋转")}</button>
      <button className={styles.button} onClick={() => { const value = window.prompt(text("Crop as left,top,width,height", "裁剪范围：左,上,宽,高"), "0,0,100,100"); if (!value) return; const numbers = value.split(",").map(Number); if (numbers.length === 4 && numbers.every(Number.isFinite)) void rasterEditor.current?.crop({ left: numbers[0], top: numbers[1], width: numbers[2], height: numbers[3] }); }}>{text("Crop", "裁剪")}</button>
      <button className={styles.button} onClick={() => { const value = window.prompt(text("Text to add", "要添加的文字"), "Text"); if (value) void rasterEditor.current?.addText(value); }}>{text("Text", "文字")}</button>
      <button className={styles.button} onClick={() => void rasterEditor.current?.addShape("rect")}>{text("Shape", "形状")}</button>
      <button className={styles.button} onClick={() => void rasterEditor.current?.draw()}>{text("Draw", "绘制")}</button>
      <button className={styles.button} onClick={() => void rasterEditor.current?.undo()}>{text("Undo", "撤销")}</button>
      <button className={styles.button} onClick={() => void rasterEditor.current?.redo()}>{text("Redo", "重做")}</button>
    </div>}
    <div className={styles.body}>
      {isOffice && currentBytes ? <><div hidden={Boolean(selected)} style={{ height: "100%" }}><OfficeSurface key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly || !office?.editable} mode={mode} /></div>{selected && <OfficeSurface key={`${selected.version ?? "disk"}:${selected.side ?? "after"}`} controller={controller} bytes={selected.blob} path={path} readOnly={true} mode="preview" />}</> : selected ? <FileViewer projectId={projectId} path={path} sourceBlob={selected.blob}
        snapshot={{ project_id: projectId, path, content: selected.content, binary: !isText, size: selected.blob.size, mtime: 0 }} /> : <>
      {isRaster && currentBytes && editorOpened && <div hidden={mode !== "edit" || Boolean(selected)} style={{ height: "100%" }}>
        <RasterSurface controller={controller} bytes={currentBytes} path={path} readOnly={readOnly} mode={mode} onReady={(value) => { rasterEditor.current = value; }} />
      </div>}
      {!isRaster && editorOpened && <div hidden={mode !== "edit" || Boolean(selected)} style={{ height: "100%" }}>
        <fieldset disabled={state.restoring || state.renaming} style={{ border: 0, margin: 0, padding: 0, height: "100%" }}>
          <EditorArea value={content} onChange={(value) => {
            setContent(value);
            controller.update(value);
            // The editor already owns this generation's text. Only bytes
            // loaded from disk, history, or another editor need decoding.
            editedBytes.current = controller.currentDraft();
          }} />
        </fieldset>
      </div>}
      <div hidden={(mode === "edit" && (isRaster || editorOpened)) || Boolean(selected)} style={{ height: "100%" }}>
        {snapshot ? <FileViewer projectId={projectId} path={path} abs={readOnly} sessionId={sessionId}
          snapshot={snapshot} sourceBlob={readOnly ? undefined : currentBytes} /> : <span>{text("Loading…", "加载中…")}</span>}
      </div>
      </>}
    </div>
    {historyOpen && <aside className={styles.history} aria-label={text("Document history", "文档历史")}>
      {!history.length && <span>{text("No retained versions.", "暂无保留的版本。")}</span>}
      {history.map((entry) => <div className={styles.entry} key={entry.version_id}>
        <span>{entry.actor === "user" ? text("You", "你") : entry.actor || text("Version", "版本")}</span>
        {entry.created_at && <time dateTime={new Date(entry.created_at * 1000).toISOString()}>{new Date(entry.created_at * 1000).toLocaleString()}</time>}
        <button className={styles.button} onClick={() => void previewVersion(entry.version_id, "before")}>{text("Before", "之前")}</button>
        <button className={styles.button} onClick={() => void previewVersion(entry.version_id, "after")}>{text("After", "之后")}</button>
        <button className={styles.button} disabled={state.restoring || state.renaming} onClick={() => void restore(entry.version_id)}>{text("Restore", "恢复")}</button>
      </div>)}
      {historyCursor && <button className={styles.button} onClick={() => void openHistory(historyCursor)}>{text("Load more", "加载更多")}</button>}
    </aside>}
  </div>;
}
