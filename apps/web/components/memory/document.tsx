"use client";
import { useEffect, useRef, useState } from "react";
import { EditorPanel } from "./parts";
import { Button } from "@/components/ui/button";
import { memoryDraft } from "./data/autosave";
import { UnifiedDiff } from "@/components/chat/messages/turn-files/unified-diff";
import { useTranslation } from "@/lib/i18n";
import styles from "./memory-page.module.css";
import { useMemoryUrl } from "./data/api";
import type { TopicOrigins } from "./markdown/origin-marks";

interface Revision { revision: string; timestamp: string; message: string }
export function MemoryDocument({ path, url, title, badge, meta, onDelete, onPreviewClick, onSaved, onRemovedChange }: {
  path: string; url: string; title: string; badge?: React.ReactNode; meta: string[];
  /** ``removed``: a save already removed the file, so nothing is left to delete on the server. */
  onDelete?: (target: { removed: boolean }) => void | Promise<void>; onPreviewClick?: (e: React.MouseEvent) => void;
  onSaved?: () => void;
  /** A save removed the file (headings only) or brought it back. */
  onRemovedChange?: () => void;
}) {
  const { text, locale } = useTranslation();
  const api = useMemoryUrl();
  const [draft] = useState(() => memoryDraft(url));
  const [state, setState] = useState(draft.state);
  const [mode, setMode] = useState<"preview" | "edit" | "history">("preview");
  const [entries, setEntries] = useState<Revision[]>([]);
  const [more, setMore] = useState(false);
  const [revision, setRevision] = useState("");
  const [diff, setDiff] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [latest, setLatest] = useState<string | null>(null);
  const [origins, setOrigins] = useState<TopicOrigins | null>(null);
  // Where the saved file and its paragraphs came from, read again after
  // every save, since a save makes a person's paragraphs the file's own.
  useEffect(() => {
    let active = true;
    fetch(url)
      .then(async response => (response.ok ? response.json() : null))
      .then(data => { if (active) setOrigins(data && (data.origin || data.paragraphs) ? { origin: data.origin, paragraphs: data.paragraphs } : null); })
      .catch(() => { if (active) setOrigins(null); });
    return () => { active = false; };
  }, [url, state.base]);
  useEffect(() => {
    const listener = () => setState(draft.state);
    draft.listeners.add(listener);
    void draft.load();
    const leaving = () => { void draft.flush(); };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      leaving();
      if (draft.state.content !== draft.state.base) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("pagehide", leaving);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      draft.listeners.delete(listener);
      window.removeEventListener("pagehide", leaving);
      window.removeEventListener("beforeunload", beforeUnload);
      void draft.flush();
    };
  }, [draft]);
  useEffect(() => {
    if (state.loaded && !state.saving && state.content === state.base) onSaved?.();
  }, [state.base, state.saving, state.loaded, onSaved, state.content]);
  const removed = useRef(state.removed);
  useEffect(() => {
    if (removed.current === state.removed) return;
    removed.current = state.removed;
    onRemovedChange?.();
  }, [state.removed, onRemovedChange]);
  useEffect(() => {
    if (mode !== "history") return;
    let active = true;
    setBusy(true); setError(""); setEntries([]); setRevision(""); setDiff("");
    fetch(api("/api/memory/history?" + new URLSearchParams({ path })))
      .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); return data; })
      .then(data => { if (active) { setEntries(data.entries); setMore(data.has_more); setRevision(data.entries[0]?.revision || ""); } })
      .catch(error => { if (active) setError(String(error)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [api, mode, path, state.base]);
  useEffect(() => {
    if (mode !== "history" || (!revision && latest === null)) return;
    let active = true;
    setDiff(""); setBusy(true); setError("");
    const request = latest !== null
      ? fetch(api("/api/memory/diff"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ before: latest, after: state.content }) })
      : fetch(api("/api/memory/history?" + new URLSearchParams({ path, revision })));
    request.then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); return data; })
      .then(data => { if (active) setDiff(data.diff); })
      .catch(error => { if (active) setError(String(error)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [api, mode, path, revision, state.content, latest]);
  async function older() {
    setBusy(true); setError("");
    try {
      const response = await fetch(api("/api/memory/history?" + new URLSearchParams({ path, offset: String(entries.length) })));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      setEntries(previous => [...previous, ...data.entries]); setMore(data.has_more);
    } catch (error) { setError(String(error)); } finally { setBusy(false); }
  }
  async function reviewLatest() {
    try {
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load memory");
      setLatest(data.content); setMode("history");
    } catch (error) { setError(String(error)); }
  }
  async function restore() {
    if (draft.state.saving || draft.state.content !== draft.state.base) return;
    setRestoring(true); draft.publish({ restoring: true }); setError("");
    try {
      const response = await fetch(api("/api/memory/restore"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, revision, base_content: draft.state.base }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not restore version");
      draft.undoStack.push(draft.state.content);
      draft.redoStack = [];
      draft.editVersion++;
      draft.publish({ content: data.content, base: data.content, error: "", warning: data.warning || "" });
      draft.persist();
      setMode("preview");
    } catch (error) { setError(String(error)); } finally { setRestoring(false); draft.publish({ restoring: false }); }
  }
  const tools = <Button type="button" variant="elevated" aria-pressed={mode === "history"} onClick={() => { setLatest(null); setMode("history"); }}>{text("History", "历史")}</Button>;
  const notices = <>
    {state.error && <div role="alert" className={styles.editorNotice}>
      {state.error} <Button type="button" variant="link" size="xs" className="ml-[10px]" onClick={() => draft.retry()}>{text("Retry", "重试")}</Button>{" "}
      <Button type="button" variant="link" size="xs" className="ml-[10px]" onClick={reviewLatest}>{text("Review latest", "查看最新版本")}</Button>
    </div>}
    {state.warning && <div role="alert" className={styles.editorNotice}>{state.warning}</div>}
  </>;
  return <>
    <EditorPanel title={title} badge={badge} meta={meta} origins={origins}
      state={{ content: state.content, viewMode: mode }}
      onChange={content => draft.edit(content)} onViewMode={setMode} readOnly={restoring || state.restoring}
      onKeyDown={event => {
        if (restoring || state.restoring || event.nativeEvent.isComposing || !(event.metaKey || event.ctrlKey)) return;
        if (event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) draft.redo(); else draft.undo(); }
        else if (event.key.toLowerCase() === "y") { event.preventDefault(); draft.redo(); }
      }}
      onDelete={onDelete && state.loaded && !state.saving && state.content === state.base && !restoring ? () => onDelete({ removed: state.removed }) : undefined}
      onPreviewClick={onPreviewClick} tools={tools} notices={notices} loading={!state.loaded}
      detail={mode === "history" ? <div className={`${styles.historyPanel} flex flex-1 min-h-0 overflow-hidden`}>
        <div className={styles.revisionList}>
          {entries.map(entry => <button key={entry.revision} aria-pressed={revision === entry.revision && latest === null} className={styles.revisionRow} onClick={() => { setLatest(null); setRevision(entry.revision); }}>
            <time>{new Date(entry.timestamp).toLocaleString(locale)}</time>
            <span>{entry.message}</span><code>{entry.revision.slice(0, 8)}</code>
          </button>)}
          {more && <Button type="button" variant="elevated" size="sm" className="mt-2" disabled={busy} onClick={older}>{text("Load older versions", "加载更早版本")}</Button>}
          {!busy && !entries.length && !error && <p>{text("No recorded changes for this file.", "该文件尚无修改记录。")}</p>}
        </div>
        <div className="flex-1 min-w-0 overflow-auto p-[16px]">
          {latest !== null ? <Button type="button" disabled={restoring || state.saving} onClick={() => {
            draft.publish({ base: latest, error: "" }); draft.persist(); setLatest(null); draft.schedule();
          }}>{text("Replace latest with this draft", "用当前草稿替换最新版本")}</Button> :
            revision && <Button type="button" disabled={busy || restoring || state.saving || state.content !== state.base || !!state.error} onClick={restore}>{text("Restore this version", "恢复此版本")}</Button>}
          {error && <p role="alert">{error}</p>}
          {busy ? <p>{text("Loading…", "加载中…")}</p> : diff ? <UnifiedDiff diff={diff} /> : <p>{text("No changes.", "没有修改。")}</p>}
        </div>
      </div> : undefined}
    />
  </>;
}
