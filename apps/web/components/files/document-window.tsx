"use client";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { FileViewer } from "./file-viewer";
import { DocumentController } from "@/lib/state/document-controller";
import type { DocumentHistoryEntry, DocumentSnapshot } from "@/lib/state/document-types";
import styles from "./document-window.module.css";

function textSnapshot(snapshot: DocumentSnapshot | null, path: string) {
  if (!snapshot) return null;
  return { project_id: "", path, content: undefined, size: snapshot.bytes.size, mtime: snapshot.mtime ?? 0, revision: snapshot.revision };
}

export function DocumentWindow({ projectId, path, sessionId, readOnly = false }: { projectId: string; path: string; sessionId?: string; readOnly?: boolean }) {
  const { text } = useTranslation();
  const controller = useMemo(() => new DocumentController({ projectId, path, sessionId, readOnly }), [projectId, path, sessionId, readOnly]);
  const [state, setState] = useState(controller.getState());
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Blob | null>(null);
  const [selectedContent, setSelectedContent] = useState<string | null>(null);
  const [content, setContent] = useState<string | undefined>(undefined);
  const [draftText, setDraftText] = useState<string | undefined>(undefined);
  const isText = !/\.(png|jpe?g|gif|webp|svg|ico|pdf)$/i.test(path);
  useEffect(() => { const unsubscribe = controller.subscribe(setState); return () => { unsubscribe(); }; }, [controller]);
  useEffect(() => { void controller.load().catch((error) => setHistoryError(error instanceof Error ? error.message : text("Unable to read document.", "无法读取文件。"))); return () => { void controller.close(); }; }, [controller, text]);
  useEffect(() => { if (state.snapshot && isText) void state.snapshot.bytes.text().then(setContent); }, [state.snapshot]);
  useEffect(() => { if (state.draft && isText) void state.draft.text().then(setDraftText); else setDraftText(undefined); }, [state.draft, isText]);
  const snapshot = state.snapshot ? { project_id: projectId, path, content, size: state.snapshot.bytes.size, mtime: state.snapshot.mtime ?? 0, revision: state.snapshot.revision } : null;
  const onLoaded = (data: { content?: string; revision?: string; mtime?: number; size: number; project_id: string; path: string } | null) => {
    if (data?.content !== undefined && !state.snapshot) controller.hydrate({ bytes: data.content, revision: data.revision ?? "", mtime: data.mtime });
  };
  async function openHistory() { setHistoryOpen((open) => !open); if (!history.length) try { setHistory((await controller.listHistory()).entries); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to load history.", "无法加载历史记录。")); } }
  async function previewVersion(entry: DocumentHistoryEntry) { try { const blob = await controller.historyContent(entry.version_id); setSelected(blob); setSelectedContent(await blob.text()); setHistoryError(null); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to read history.", "无法读取历史版本。")); } }
  async function restoreVersion(entry: DocumentHistoryEntry) { if (!window.confirm(text("Restore this version?", "恢复此版本？"))) return; try { await controller.restore(entry.version_id); setSelected(null); setSelectedContent(null); setMode("preview"); } catch (error) { setHistoryError(error instanceof Error ? error.message : text("Unable to restore history.", "无法恢复历史版本。")); } }
  return <div className={styles.window} data-document-window="true">
    <div className={styles.toolbar} role="toolbar" aria-label={text("Document", "文档")}>
      <span className={styles.title}>{path.split("/").pop()}</span><span className={styles.spacer} />
      <button className={`${styles.button} ${mode === "preview" ? styles.active : ""}`} aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>{text("Preview", "预览")}</button>
      {!readOnly && isText ? <button className={`${styles.button} ${mode === "edit" ? styles.active : ""}`} aria-pressed={mode === "edit"} onClick={() => setMode("edit")}>{text("Edit", "编辑")}</button> : null}
      {!readOnly && <button className={styles.button} aria-expanded={historyOpen} onClick={() => void openHistory()}>{text("History", "历史")}</button>}
    </div>
    {historyError || state.error ? <div className={styles.error} role="alert">{historyError || state.error}</div> : null}
    <div className={styles.body}>
      {selected ? <FileViewer projectId={projectId} path={path} snapshot={{ project_id: projectId, path, content: selectedContent ?? "", size: selected.size, mtime: 0 }} /> : <FileViewer projectId={projectId} path={path} abs={readOnly} sessionId={sessionId} snapshot={snapshot} draft={mode === "edit" ? draftText : undefined} onDraftChange={mode === "edit" ? (value) => controller.update(value) : undefined} onLoaded={onLoaded} />}
    </div>
    {historyOpen ? <aside className={styles.history} aria-label={text("Document history", "文档历史")}><div className={styles.historyTitle}>{text("Manual versions", "手动版本")}</div>{history.map((entry) => <div className={styles.entry} key={entry.version_id}><span>{entry.actor === "user" ? text("You", "你") : entry.actor || text("Version", "版本")}</span><button className={styles.button} onClick={() => void previewVersion(entry)}>{text("Preview", "预览")}</button><button className={styles.button} onClick={() => void restoreVersion(entry)}>{text("Restore", "恢复")}</button></div>)}</aside> : null}
  </div>;
}
