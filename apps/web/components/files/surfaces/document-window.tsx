"use client";
import type { FileSectionNavigation } from "@/lib/tabs/panes/file-section-requests";
import { useEffect, useMemo, useRef, useState } from "react";
import { FileTextIcon, RefreshCwIcon } from "@/components/animated-icons";
import { useActionIconAnimation } from "@/components/chat/messages/bubbles/use-action-icon-animation";
import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { EditorArea, FileViewer } from "../file-viewer";
import { getOrCreateDocumentController } from "@/lib/files/document/document-controller";
import type { DocumentHistoryEntry, DocumentHistoryPage } from "@/lib/files/document/document-types";
import styles from "./document-window.module.css";
import { CAPSULE_GROOVE, CAPSULE_OPTION, CHOSEN_OPTION } from "@/components/ui/capsule";
import { SlidingThumb } from "@/components/ui/sliding-thumb";

import { fileCapabilities } from "@/lib/documents/file-formats";
import { canBrowseLocalFiles, isHtmlPath } from "@/lib/files/local-file-url";
import { openHtmlPage, projectFileAbsolutePath } from "@/lib/files/open-html-page";
import { rawFileUrl, absRawFileUrl } from "@/lib/files/files-shared";
import { officeCapability } from "@/lib/documents/file-formats";
import { convertOfficeDocument } from "@/lib/documents/office-editor";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { OfficeSurface } from "./office-surface";
import { convertRasterToPng } from "@/lib/documents/raster-format";
import { PdfSurface } from "./pdf-surface";
import { RasterSurface } from "./raster-surface";
import type { RasterEditorInstance } from "@/lib/documents/raster-editor";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { isPermanent, retryDelay } from "@/lib/abilities/kept-read";
import { useChanged } from "@/lib/net/ws/changes";

/**
 * What a failed first read of a document means. The controller names the
 * HTTP status in its message ("Unable to read document (404)."): 404/410 is
 * a file that is gone; another 4xx is the server's final answer; a 5xx,
 * 408/425/429 or a network failure (fetch's TypeError) is asked again.
 * Anything else (an unreadable local draft, a missing revision) is a fact.
 */
export function documentReadOutcome(failure: unknown): "retry" | "gone" | "final" {
  if (failure instanceof TypeError) return "retry";
  const message = failure instanceof Error ? failure.message : String(failure ?? "");
  const match = /^Unable to read document \((\d{3})\)/.exec(message);
  if (!match) return "final";
  const status = Number(match[1]);
  if (status === 404 || status === 410) return "gone";
  return isPermanent({ status }) ? "final" : "retry";
}

/** Close every tab showing this file; false when no tab shows it. */
function closeFileTabs(projectId: string, path: string, sessionId: string | undefined): boolean {
  const tabs = useCenterTabs.getState();
  const open = tabs.tabs.filter(tab => tab.kind === "file" && tab.path === path
    && (tab.projectId || "") === projectId && (tab.fileSessionId || undefined) === (sessionId || undefined));
  for (const tab of open) tabs.closeTab(tab.id);
  return open.length > 0;
}

/** Toolbar actions are the app's raised buttons (Undo / Review). */
const BUTTON = cn(buttonVariants({ variant: "elevated", size: "xs" }), styles.button);

interface VersionPreview { blob: Blob; content?: string; version?: string; side?: "before" | "after"; disk?: boolean; }

export function DocumentWindow({ projectId, path, sessionId, readOnly = false, sectionNavigation, previewSessionId, onPageOpened, onGone }: {
  projectId: string; path: string; sessionId?: string; readOnly?: boolean; sectionNavigation?: FileSectionNavigation;
  /** The chat this file previews for; an HTML page opens beside it. */
  previewSessionId?: string;
  onPageOpened?: () => void;
  /** A window outside the tabs (an attachment preview) closes itself when the file is gone. */
  onGone?: () => void;
}) {
  const onGoneRef = useRef(onGone);
  onGoneRef.current = onGone;
  const { text } = useTranslation();
  const controller = useMemo(() => getOrCreateDocumentController({ projectId, path, sessionId, readOnly }),
    [projectId, path, sessionId, readOnly]);
  const [state, setState] = useState(controller.getState());
  const host = useRef<HTMLDivElement>(null);
  const refreshIcon = useActionIconAnimation(state.restoring || state.renaming);
  // An editable Office file has no Preview/Edit switch: it opens writable.
  const officeAlwaysEdit = !readOnly && fileCapabilities(path).preview === "office" && !!officeCapability(path)?.editable;
  const restingMode = officeAlwaysEdit ? "edit" : "preview";
  const [mode, setMode] = useState<"preview" | "edit">(restingMode);
  const [converting, setConverting] = useState(false);
  const conversion = useRef<{ path: string; key: string; bytes: File } | null>(null);
  const imageConversion = useRef<{ source: Blob; path: string; key: string; bytes: File } | null>(null);
  const conversionAbort = useRef<AbortController | null>(null);
  useEffect(() => () => conversionAbort.current?.abort(), []);
  const [editorOpened, setEditorOpened] = useState(false);
  const [content, setContent] = useState("");
  const [decodedBytes, setDecodedBytes] = useState<Blob | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistoryEntry[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState<DocumentHistoryPage["model_index"]>();
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<VersionPreview | null>(null);
  const selectionRequest = useRef(0);
  const sectionNavigationRef = useRef(sectionNavigation);
  sectionNavigationRef.current = sectionNavigation;
  const answerSectionRequest = useEffectEvent(() => {
    if (!sectionNavigation) return;
    selectionRequest.current++;
    setSelected(null);
    setMode(restingMode);
    if (!state.snapshot && state.status === "error") sectionNavigation.consume(sectionNavigation.request.sequence);
    if (!path.toLowerCase().endsWith(".md") || state.snapshot?.binary) {
      setError(text("Section not found.", "未找到章节。"));
      sectionNavigation.consume(sectionNavigation.request.sequence);
    }
  });
  const sectionSequence = sectionNavigation?.request.sequence;
  const binarySnapshot = state.snapshot?.binary;
  useEffect(() => answerSectionRequest(), [sectionSequence, binarySnapshot]);
  const editedBytes = useRef<Blob | null>(null);
  const rasterEditor = useRef<RasterEditorInstance | null>(null);
  const [rasterReady, setRasterReady] = useState(false);
  const [cropping, setCropping] = useState(false);
  const isText = fileCapabilities(path).textEditable && !state.snapshot?.binary;
  const office = officeCapability(path);
  const isPdf = fileCapabilities(path).preview === "pdf";
  const isOffice = fileCapabilities(path).preview === "office";
  const isRaster = ["png", "jpg", "jpeg", "webp"].includes(fileCapabilities(path).preview === "image" ? path.split(".").pop()?.toLowerCase() ?? "" : "");
  const currentBytes = state.draft ?? state.snapshot?.bytes;

  useEffect(() => controller.subscribe(setState), [controller]);
  // Opening reads the file until it answers: a network failure or a 5xx is
  // asked again, quietly, with backoff. A file that is gone closes its tab;
  // any other refusal is a fact about the file and is shown as before.
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const selections = selectionRequest;
    const read = (attempt: number) => {
      void controller.load().catch((failure: unknown) => {
        if (!active) return;
        const outcome = documentReadOutcome(failure);
        if (outcome === "retry") {
          console.warn("document read failed; reading again", { path, failure });
          timer = setTimeout(() => read(attempt + 1), retryDelay(attempt));
          return;
        }
        if (outcome === "gone" && !controller.hasLocalChanges()) {
          if (onGoneRef.current) { onGoneRef.current(); return; }
          if (closeFileTabs(projectId, path, sessionId)) return;
        }
        setError(failure instanceof Error ? failure.message : String(failure));
        const navigation = sectionNavigationRef.current;
        navigation?.consume(navigation.request.sequence);
      });
    };
    read(0);
    return () => { active = false; if (timer) clearTimeout(timer); selections.current++; };
  }, [controller, projectId, path, sessionId]);
  // A first read still being asked again says nothing; its failure is not news.
  const quietLoad = !state.snapshot && state.status === "error" && !error;
  useEffect(() => {
    let active = true;
    if (currentBytes && currentBytes !== editedBytes.current && isText) void currentBytes.text().then((value) => { if (active) { setContent(value); setDecodedBytes(currentBytes); } });
    return () => { active = false; };
  }, [currentBytes, isText]);

  // The file is looked at again only when the Worker says it changed on
  // disk: the first look asks it to follow the file and names the key its
  // `documents` frames carry (document_changes.py). A reconnect counts as a
  // change, and a hidden window looks once it shows.
  const [watchKey, setWatchKey] = useState<string | null>(null);
  const hasSnapshot = !!state.snapshot;
  const checkAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    checkAbort.current = abort;
    setWatchKey(null);
    if (hasSnapshot) void (async () => {
      try {
        const key = await controller.follow(AbortSignal.any([abort.signal, AbortSignal.timeout(4000)]));
        if (abort.signal.aborted) return;
        setWatchKey(key);
        // A change made before the Worker began following it.
        await controller.checkForUpdates(abort.signal);
      } catch (failure) {
        if (!abort.signal.aborted) console.warn("file updates not followed", { path, failure });
      }
    })();
    return () => { abort.abort(); };
  }, [controller, path, hasSnapshot]);
  useChanged("documents", async () => {
    const abort = checkAbort.current;
    if (!abort || abort.signal.aborted) return;
    try { await controller.checkForUpdates(AbortSignal.any([abort.signal, AbortSignal.timeout(4000)])); }
    catch (failure) { if (!abort.signal.aborted) console.warn("file update check failed", { path, failure }); }
  }, watchKey ? [watchKey] : []);

  async function refresh() {
    await perform(async () => {
      await controller.refresh();
      selectionRequest.current++; setSelected(null);
    });
  }

  // HTML is a page: it renders in a browser tab, which loads its relative
  // assets from disk. Saving first makes the page show these edits.
  const isHtml = isHtmlPath(path);
  const canOpenPage = isHtml && canBrowseLocalFiles();
  async function openPage() {
    await perform(async () => {
      if (!readOnly && controller.hasLocalChanges()) await controller.flush();
      const absolute = readOnly ? path : await projectFileAbsolutePath(projectId, path);
      if (!absolute?.startsWith("/")) throw new Error(text("This file has no local disk path to open.", "这个文件没有可打开的本地路径。"));
      openHtmlPage(absolute, previewSessionId);
      onPageOpened?.();
    });
  }

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
      selectionRequest.current++; setSelected(null); setMode(restingMode);
    });
  }
  async function restore(version: string, side: "before" | "after" = "after") {
    if (!(await confirmAction({
      title: text("Restore this version?", "恢复此版本？"),
      confirmLabel: text("Restore", "恢复"),
    }))) return;
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
    if (!(await confirmAction({
      title: text("Discard your local changes?", "丢弃本地修改？"),
      description: text("The file is read again from disk.", "将重新读取磁盘文件。"),
      confirmLabel: text("Discard", "丢弃"),
      destructive: true,
    }))) return;
    await perform(async () => { await controller.discardDraft(); showCurrent(); });
  }
  const snapshot = state.snapshot ? { project_id: projectId, path,
    content: isText ? content : undefined, binary: !isText,
    size: currentBytes?.size ?? 0, mtime: state.snapshot.mtime ?? 0, revision: state.snapshot.revision } : null;

  return <div ref={host} className={styles.window} data-document-window="true" onKeyDown={(event) => {
    if (!readOnly && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void perform(() => controller.flush());
    }
  }}>
    <div className={styles.toolbar} role="toolbar" aria-label={text("Document", "文档")}>
      <span className={styles.title}><FileTextIcon size={16} aria-hidden />{path.split("/").pop()}</span><span className="flex-1" />
      <button type="button" className={BUTTON} disabled={!state.snapshot || state.restoring || state.renaming}
        onClick={() => void refresh()} {...refreshIcon.handlers}>
        <RefreshCwIcon ref={refreshIcon.ref} size={14} aria-hidden />{text("Refresh", "刷新")}
      </button>
      {(!isOffice || (!readOnly && (isText || isRaster))) && <div className={CAPSULE_GROOVE} role="group" aria-label={text("View", "视图")}>
        {!isOffice && <button type="button" className={CAPSULE_OPTION}
          aria-pressed={mode === "preview" && !selected} onClick={() => void showCurrent()}>{isHtml ? text("Source", "源代码") : text("Preview", "预览")}</button>}
        {!readOnly && (isText || isRaster) && <button type="button" className={CAPSULE_OPTION}
          disabled={!state.snapshot || state.restoring || state.renaming} aria-pressed={mode === "edit" && !selected}
          onClick={() => { setSelected(null); setEditorOpened(true); setMode("edit"); }}>{text("Edit", "编辑")}</button>}
        <SlidingThumb selector={CHOSEN_OPTION} />
      </div>}
      {canOpenPage && <button type="button" className={BUTTON} disabled={state.restoring || state.renaming}
        onClick={() => void openPage()}>{text("View page", "网页预览")}</button>}
      {!readOnly && office?.conversionRequired && !selected && <button className={BUTTON}
        disabled={!currentBytes || converting} onClick={() => void convert()}>
        {text(`Convert to ${office.format.toUpperCase()}`, `转换为 ${office.format.toUpperCase()}`)}</button>}
      {!readOnly && isRaster && !selected && <button className={BUTTON}
        disabled={!currentBytes || converting || state.restoring || state.renaming} onClick={() => void convertImage()}>
        {text("Convert to PNG", "转换为 PNG")}</button>}
      {(isOffice || isRaster) && <a className={BUTTON} href={readOnly ? absRawFileUrl(path, sessionId) : rawFileUrl(projectId, path)}
        download={path.split("/").pop()}>{text("Download disk file", "下载磁盘文件")}</a>}
      {!readOnly && <button className={BUTTON} aria-expanded={historyOpen}
        onClick={() => historyOpen ? setHistoryOpen(false) : void openHistory()}>{text("History", "历史")}</button>}
    </div>
    {state.externalChanged && <div className={styles.updateNotice} role="status">
      {text("File changed. Refresh to load the latest version.", "文件已修改。点击刷新加载最新版本。")}
      {controller.hasLocalChanges() && <span>{text("Your local edits are preserved.", "本地编辑已保留。")}</span>}
    </div>}
    {(error || state.error) && !quietLoad && <div className={styles.error} role="alert">
      {error || state.error}
      {(state.status === "error" || state.status === "conflict") && <span>
        <button className={BUTTON} onClick={() => void perform(() => state.snapshot ? controller.flush() : controller.load())}>{text("Retry", "重试")}</button>
        <button className={BUTTON} onClick={() => void showDisk()}>{text("View disk version", "查看磁盘版本")}</button>
        <button className={BUTTON} disabled={!state.draft} onClick={exportDraft}>{text("Export draft", "导出草稿")}</button>
        <button className={BUTTON} disabled={!state.draft} onClick={() => void discard()}>{text("Discard draft", "丢弃草稿")}</button>
      </span>}
    </div>}
    {selected && <div className={styles.toolbar}>
      <span>{selected.disk ? text("Disk version", "磁盘版本") : text("History version", "历史版本")}</span>
      <button className={BUTTON} onClick={showCurrent}>{text("Back to current file", "返回当前文件")}</button>
      {selected.version && <button className={BUTTON} disabled={state.restoring || state.renaming}
        onClick={() => void restore(selected.version!, selected.side)}>{text("Restore this version", "恢复此版本")}</button>}
    </div>}
    {isRaster && editorOpened && mode === "edit" && !selected && <fieldset className={styles.toolbar} role="toolbar" aria-label={text("Image tools", "图片工具")} disabled={!rasterReady || state.restoring || state.renaming} style={{border:0,margin:0}}>
      {cropping ? <>
        <button className={BUTTON} onClick={() => void imageCommand(async editor => {await editor.applyCrop();setCropping(false);})}>{text("Apply crop", "应用裁剪")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(async editor => {await editor.cancelTool();setCropping(false);})}>{text("Cancel crop", "取消裁剪")}</button>
      </> : <>
        <button className={BUTTON} onClick={() => void imageCommand(editor => editor.rotate())}>{text("Rotate", "旋转")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(async editor => {await editor.startCrop();setCropping(true);})}>{text("Crop", "裁剪")}</button>
        <button className={BUTTON} onClick={() => {const value=window.prompt(text("Text to add", "要添加的文字"));if(value)void imageCommand(editor=>editor.addText(value));}}>{text("Text", "文字")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(editor => editor.addShape("rect"))}>{text("Shape", "形状")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(editor => editor.draw())}>{text("Draw", "绘制")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(editor => editor.undo())}>{text("Undo", "撤销")}</button>
        <button className={BUTTON} onClick={() => void imageCommand(editor => editor.redo())}>{text("Redo", "重做")}</button>
      </>}
    </fieldset>}
    <div className="flex-1 min-h-0 overflow-auto">
      {isRaster && currentBytes && editorOpened && <div hidden={mode !== "edit" || Boolean(selected)} style={{ height: "100%" }}>
        <RasterSurface key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly} mode={mode} onReady={(value) => { rasterEditor.current = value; setRasterReady(Boolean(value)); }} />
      </div>}

      {isPdf && currentBytes ? <><div hidden={Boolean(selected)} style={{ height: "100%" }}><PdfSurface saveStatus={state.status} key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly} /></div>{selected && <FileViewer projectId={projectId} path={path} abs={readOnly} sessionId={sessionId} sourceBlob={selected.blob} snapshot={{ project_id: projectId, path, binary: true, size: selected.blob.size, mtime: 0 }} />}</> : isOffice && currentBytes ? <><div hidden={Boolean(selected)} style={{ height: "100%" }}><OfficeSurface key={state.editorRevision} controller={controller} bytes={currentBytes} path={path} readOnly={readOnly || !office?.editable} mode={mode} /></div>{selected && <OfficeSurface key={`${selected.version ?? "disk"}:${selected.side ?? "after"}`} controller={controller} bytes={selected.blob} path={path} readOnly={true} mode="preview" />}</> : selected ? <FileViewer projectId={projectId} path={path} abs={readOnly} sessionId={sessionId} sourceBlob={selected.blob}
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
          sectionNavigation={mode === "preview" && (decodedBytes === currentBytes || editedBytes.current === currentBytes) ? sectionNavigation : undefined} snapshot={snapshot} sourceBlob={currentBytes} /> : null}
      </div>
      </>}
    </div>
    {historyOpen && <aside className={styles.history} aria-label={text("Document history", "文档历史")}>
      {!history.length && !historyCursor && (!historyIndex || historyIndex.state === "complete") && <span>{text("No retained versions.", "暂无保留的版本。")}</span>}
      {historyIndex?.state === "partial" && <button className={BUTTON} onClick={() => void openHistory()}>
        {text("Continue loading history", "继续加载历史")}</button>}
      {historyIndex?.state === "unavailable" && <span>{text("Some older model versions are unavailable.", "部分旧模型修改版本不可用。")}</span>}
      {history.map((entry) => <div className={styles.entry} key={entry.version_id}>
        <span>{entry.actor === "user" ? text("You", "你") : entry.actor === "model" ? text("Model", "模型") : entry.actor || text("Version", "版本")}</span>
        {entry.created_at && <time dateTime={new Date(entry.created_at * 1000).toISOString()}>{new Date(entry.created_at * 1000).toLocaleString()}</time>}
        {entry.actor === "model" && entry.status !== "committed" && <span>{text("After version unavailable", "修改后版本不可用")}</span>}
        <button className={BUTTON} disabled={entry.actor === "model" && (!entry.before_revision || entry.before_revision === "absent")}
          onClick={() => void previewVersion(entry.version_id, "before")}>{text("Before", "之前")}</button>
        <button className={BUTTON} disabled={entry.actor === "model" && (!entry.after_revision || entry.after_revision === "absent")}
          onClick={() => void previewVersion(entry.version_id, "after")}>{text("After", "之后")}</button>
        <button className={BUTTON} disabled={state.restoring || state.renaming || (entry.actor === "model" && (!entry.after_revision || entry.after_revision === "absent"))}
          onClick={() => void restore(entry.version_id)}>{text("Restore", "恢复")}</button>
      </div>)}
      {historyCursor && <button className={BUTTON} onClick={() => void openHistory(historyCursor)}>{text("Load more", "加载更多")}</button>}
    </aside>}
  </div>;
}
