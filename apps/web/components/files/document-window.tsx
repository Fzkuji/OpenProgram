"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { EditorArea, FileViewer } from "./file-viewer";
import { getOrCreateDocumentController } from "@/lib/state/document-controller";
import type { DocumentHistoryEntry, DocumentHistoryPage } from "@/lib/state/document-types";
import styles from "./document-window.module.css";

import { fileCapabilities } from "@/lib/documents/file-formats";
import { rawFileUrl, absRawFileUrl } from "@/lib/state/files-shared";
import { officeCapability } from "@/lib/documents/file-formats";
import { convertOfficeDocument } from "@/lib/documents/office-editor";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { OfficeSurface } from "./office-surface";
import { convertRasterToPng } from "@/lib/documents/raster-format";
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
  const imageConversion = useRef<{ source: Blob; path: string; key: string; bytes: File } | null>(null);
  const conversionAbort = useRef<AbortController | null>(null);
  useEffect(() => () => conversionAbort.current?.abort(), []);
  const [editorOpened, setEditorOpened] = useState(false);
  const [content, setContent] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistoryEntry[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState<DocumentHistoryPage["model_index"]>();
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<VersionPreview | null>(null);
  const selectionRequest = useRef(0);
  const editedBytes = useRef<Blob | null>(null);
  const rasterEditor = useRef<RasterEditorInstance | null>(null);
  const [rasterReady, setRasterReady] = useState(false);
  const [cropping, setCropping] = useState(false);
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
  async function imageCommand(action: (editor: RasterEditorInstance) => Promise<unknown>) {
    if (rasterEditor.current) await perform(() => action(rasterEditor.current!));
  }
  async function openHistory(cursor?: string) {
    setHistoryOpen(true);
    await perform(async () => {
      const page = await controller.listHistory(25, cursor);
      setHistory((previous) => cursor ? [...previous, ...page.entries] : page.entries);
      setHistoryCursor(page.next_cursor ?? null);
      setHistoryIndex(page.model_index);
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
  async function convertImage() {
    if (converting || !currentBytes || readOnly) return;
    const target = window.prompt(text("New PNG file path", "新 PNG 文件路径"), path.replace(/\.[^.]+$/, ".png"));
    if (target === null) return;
    if (!target.toLowerCase().endsWith(".png") || target === path) {
      setError(text("Choose a different .png file path.", "请选择另一个 .png 文件路径。")); return;
    }
    setConverting(true);
    const abort = new AbortController(); conversionAbort.current = abort;
    try {
      await controller.flush();
      const bytes = controller.getState().draft ?? controller.getState().snapshot?.bytes;
      if (!bytes) throw new Error("Image content is unavailable.");
      if (!imageConversion.current || imageConversion.current.path !== target || imageConversion.current.source !== bytes) {
        const output = await convertRasterToPng(bytes, target.split("/").pop()!, abort.signal);
        imageConversion.current = { source: bytes, path: target, key: crypto.randomUUID(), bytes: output };
      }
      abort.signal.throwIfAborted();
      await controller.publishNewDocument(target, imageConversion.current.bytes, imageConversion.current.key);
      if (!abort.signal.aborted) { setError(null); useCenterTabs.getState().openFileTab(projectId, target); }
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
      {!readOnly && isRaster && !selected && <button className={styles.button}
        disabled={!currentBytes || converting || state.restoring || state.renaming} onClick={() => void convertImage()}>
        {text("Convert to PNG", "转换为 PNG")}</button>}
      {(isOffice || isRaster) && <a className={styles.button} href={readOnly ? absRawFileUrl(path, sessionId) : rawFileUrl(projectId, path)}
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
    {isRaster && editorOpened && mode === "edit" && !selected && <fieldset className={styles.toolbar} role="toolbar" aria-label={text("Image tools", "图片工具")} disabled={!rasterReady || state.restoring || state.renaming} style={{border:0,margin:0}}>
      {cropping ? <>
        <button className={styles.button} onClick={() => void imageCommand(async editor => {await editor.applyCrop();setCropping(false);})}>{text("Apply crop", "应用裁剪")}</button>
        <button className={styles.button} onClick={() => void imageCommand(async editor => {await editor.cancelTool();setCropping(false);})}>{text("Cancel crop", "取消裁剪")}</button>
      </> : <>
        <button className={styles.button} onClick={() => void imageCommand(editor => editor.rotate())}>{text("Rotate", "旋转")}</button>
        <button className={styles.button} onClick={() => void imageCommand(async editor => {await editor.startCrop();setCropping(true);})}>{text("Crop", "裁剪")}</button>
        <button className={styles.button} onClick={() => {const value=window.prompt(text("Text to add", "要添加的文字"));if(value)void imageCommand(editor=>editor.addText(value));}}>{text("Text", "文字")}</button>
        <button className={styles.button} onClick={() => void imageCommand(editor => editor.addShape("rect"))}>{text("Shape", "形状")}</button>
        <button className={styles.button} onClick={() => void imageCommand(editor => editor.draw())}>{text("Draw", "绘制")}</button>
        <button className={styles.button} onClick={() => void imageCommand(editor => editor.undo())}>{text("Undo", "撤销")}</button>
        <button className={styles.button} onClick={() => void imageCommand(editor => editor.redo())}>{text("Redo", "重做")}</button>
      </>}
    </fieldset>}
    <div className={styles.body}>
      {isRaster && currentBytes && editorOpened && <div hidden={mode !== "edit" || Boolean(selected)} style={{ height: "100%" }}>
        <RasterSurface key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly} mode={mode} onReady={(value) => { rasterEditor.current = value; setRasterReady(Boolean(value)); }} />
      </div>}

      {isOffice && currentBytes ? <><div hidden={Boolean(selected)} style={{ height: "100%" }}><OfficeSurface key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly || !office?.editable} mode={mode} /></div>{selected && <OfficeSurface key={`${selected.version ?? "disk"}:${selected.side ?? "after"}`} controller={controller} bytes={selected.blob} path={path} readOnly={true} mode="preview" />}</> : selected ? <FileViewer projectId={projectId} path={path} sourceBlob={selected.blob}
        snapshot={{ project_id: projectId, path, content: selected.content, binary: !isText, size: selected.blob.size, mtime: 0 }} /> : <>
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
      {!history.length && !historyCursor && (!historyIndex || historyIndex.state === "complete") && <span>{text("No retained versions.", "暂无保留的版本。")}</span>}
      {historyIndex?.state === "partial" && <button className={styles.button} onClick={() => void openHistory()}>
        {text("Continue loading history", "继续加载历史")}</button>}
      {historyIndex?.state === "unavailable" && <span>{text("Some older model versions are unavailable.", "部分旧模型修改版本不可用。")}</span>}
      {history.map((entry) => <div className={styles.entry} key={entry.version_id}>
        <span>{entry.actor === "user" ? text("You", "你") : entry.actor === "model" ? text("Model", "模型") : entry.actor || text("Version", "版本")}</span>
        {entry.created_at && <time dateTime={new Date(entry.created_at * 1000).toISOString()}>{new Date(entry.created_at * 1000).toLocaleString()}</time>}
        {entry.actor === "model" && entry.status !== "committed" && <span>{text("After version unavailable", "修改后版本不可用")}</span>}
        <button className={styles.button} disabled={entry.actor === "model" && (!entry.before_revision || entry.before_revision === "absent")}
          onClick={() => void previewVersion(entry.version_id, "before")}>{text("Before", "之前")}</button>
        <button className={styles.button} disabled={entry.actor === "model" && (!entry.after_revision || entry.after_revision === "absent")}
          onClick={() => void previewVersion(entry.version_id, "after")}>{text("After", "之后")}</button>
        <button className={styles.button} disabled={state.restoring || state.renaming || (entry.actor === "model" && (!entry.after_revision || entry.after_revision === "absent"))}
          onClick={() => void restore(entry.version_id)}>{text("Restore", "恢复")}</button>
      </div>)}
      {historyCursor && <button className={styles.button} onClick={() => void openHistory(historyCursor)}>{text("Load more", "加载更多")}</button>}
    </aside>}
  </div>;
}
