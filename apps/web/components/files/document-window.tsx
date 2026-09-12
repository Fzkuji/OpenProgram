"use client";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { FileViewer } from "./file-viewer";
import { DocumentController, documentControllers } from "@/lib/state/document-controller";
import type { DocumentHistoryEntry, DocumentSnapshot } from "@/lib/state/document-types";
import styles from "./document-window.module.css";

const TEXT_EXTENSIONS = new Set(["txt", "md", "mdx", "markdown", "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "csv", "tsv", "js", "jsx", "mjs", "cjs", "ts", "tsx", "py", "rb", "go", "rs", "java", "kt", "swift", "c", "h", "cpp", "hpp", "sh", "bash", "zsh", "fish", "css", "scss", "html", "xml", "sql", "log"]);
function extension(path: string) { const name = path.split("/").pop() ?? ""; const dot = name.lastIndexOf("."); return dot > 0 ? name.slice(dot + 1).toLowerCase() : ""; }

export function DocumentWindow({ projectId, path, sessionId, readOnly = false }: { projectId: string; path: string; sessionId?: string; readOnly?: boolean }) {
  const { text } = useTranslation();
  const controller = useMemo(() => { const key = readOnly ? `attachment:${sessionId ?? ""}:${path}` : `project:${projectId}:${path}`; const current = documentControllers.get(key); return current ?? new DocumentController({ projectId, path, sessionId, readOnly }); }, [projectId, path, sessionId, readOnly]);
  const [state, setState] = useState(controller.getState());
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Blob | null>(null);
  const [selectedContent, setSelectedContent] = useState<string | null>(null);
  const [content, setContent] = useState<string | undefined>(undefined);
  const [draftText, setDraftText] = useState<string | undefined>(undefined);
  const [editorValue, setEditorValue] = useState("");
  const isText = TEXT_EXTENSIONS.has(extension(path));
  useEffect(() => { const unsubscribe = controller.subscribe(setState); return () => { unsubscribe(); }; }, [controller]);
  useEffect(() => { void controller.load().catch((error) => setHistoryError(error instanceof Error ? error.message : text("Unable to read document.", "无法读取文件。"))); return () => undefined; }, [controller, text]);
  useEffect(() => { if (state.snapshot && isText) void state.snapshot.bytes.text().then(setContent); }, [state.snapshot]);
  useEffect(() => { if (state.draft && isText) void state.draft.text().then(setDraftText); else setDraftText(undefined); }, [state.draft, isText]);
  useEffect(() => { if (draftText !== undefined) setEditorValue(draftText); else if (content !== undefined) setEditorValue(content); }, [draftText, content]);
  const snapshot = state.snapshot ? { project_id: projectId, path, content, size: state.snapshot.bytes.size, mtime: state.snapshot.mtime ?? 0, revision: state.snapshot.revision } : null;
  const onLoaded = (data: { content?: string; revision?: string; mtime?: number; size: number; project_id: string; path: string } | null) => {
    if (data?.content !== undefined && !state.snapshot) controller.hydrate({ bytes: data.content, revision: data.revision ?? "", mtime: data.mtime });
  };
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  async function openHistory(cursor?: string) { setHistoryOpen(true); try { const page = await controller.listHistory(25, cursor ?? undefined); setHistory((old) => cursor ? [...old, ...page.entries] : page.entries); setHistoryCursor(page.next_cursor ?? null); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to load history.", "无法加载历史记录。")); } }
  async function previewVersion(entry: DocumentHistoryEntry, side: "before" | "after" = "after") { try { const blob = await controller.historyContent(entry.version_id, side); setSelected(blob); setSelectedContent(await blob.text()); setHistoryError(null); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to read history.", "无法读取历史版本。")); } }
  async function restoreVersion(entry: DocumentHistoryEntry) { if (!window.confirm(text("Restore this version?", "恢复此版本？"))) return; try { await controller.restore(entry.version_id); setSelected(null); setSelectedContent(null); setMode("preview"); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to restore history.", "无法恢复历史版本。")); } }
  function exportDraft() { if (!state.draft) return; const url = URL.createObjectURL(state.draft); const link = document.createElement("a"); link.href = url; link.download = path.split("/").pop() ?? "document"; link.click(); URL.revokeObjectURL(url); }
  async function discardDraft() { if (!window.confirm(text("Discard this draft?", "丢弃此草稿？"))) return; const discard = (controller as unknown as { discardDraft?: () => Promise<void> }).discardDraft; if (discard) await discard.call(controller); else setHistoryError(text("Draft discard is unavailable until the document controller is updated.", "文档控制器尚未提供丢弃草稿操作。")); }
  return <div className={styles.window} data-document-window="true">
    <div className={styles.toolbar} role="toolbar" aria-label={text("Document", "文档")}>
      <span className={styles.title}>{path.split("/").pop()}</span><span className={styles.spacer} />
      <button className={`${styles.button} ${mode === "preview" ? styles.active : ""}`} aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>{text("Preview", "预览")}</button>
      {!readOnly && isText ? <button className={`${styles.button} ${mode === "edit" ? styles.active : ""}`} aria-pressed={mode === "edit"} onClick={() => setMode("edit")}>{text("Edit", "编辑")}</button> : null}
      {!readOnly && <button className={styles.button} aria-expanded={historyOpen} onClick={() => void openHistory()}>{text("History", "历史")}</button>}
    </div>
    {historyError || state.error ? <div className={styles.error} role="alert">{historyError || state.error}{state.status === "conflict" || state.status === "error" ? <span><button className={styles.button} onClick={() => void controller.flush()}>{text("Retry", "重试")}</button><button className={styles.button} onClick={exportDraft}>{text("Export draft", "导出草稿")}</button><button className={styles.button} onClick={discardDraft}>{text("Discard draft", "丢弃草稿")}</button></span> : null}</div> : null}
    <div className={styles.body}>
      {selected ? <FileViewer projectId={projectId} path={path} sourceBlob={selected} snapshot={{ project_id: projectId, path, content: selectedContent ?? "", size: selected.size, mtime: 0 }} /> : <><div style={{ display: mode === "edit" && isText ? "block" : "none", height: "100%" }}><FileViewer projectId={projectId} path={path} snapshot={snapshot} draft={editorValue} onDraftChange={(value) => { setEditorValue(value); controller.update(value); }} onLoaded={onLoaded} /></div><div style={{ display: mode === "edit" && isText ? "none" : "block", height: "100%" }}><FileViewer projectId={projectId} path={path} abs={readOnly} sessionId={sessionId} snapshot={snapshot} onLoaded={onLoaded} /></div></>}
    </div>
    {historyOpen ? <aside className={styles.history} aria-label={text("Document history", "文档历史")}><div className={styles.historyTitle}>{text("Manual versions", "手动版本")}</div>{history.map((entry) => <div className={styles.entry} key={entry.version_id}><span>{entry.actor === "user" ? text("You", "你") : entry.actor || text("Version", "版本")}</span><button className={styles.button} onClick={() => void previewVersion(entry, "before")}>{text("Before", "之前")}</button><button className={styles.button} onClick={() => void previewVersion(entry, "after")}>{text("After", "之后")}</button><button className={styles.button} onClick={() => void restoreVersion(entry)}>{text("Restore", "恢复")}</button></div>)}{historyCursor ? <button className={styles.button} onClick={() => void openHistory(historyCursor)}>{text("Load more", "加载更多")}</button> : null}</aside> : null}
  </div>;
}
