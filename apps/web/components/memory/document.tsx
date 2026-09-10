"use client";
import { useEffect, useState } from "react";
import { EditorPanel } from "./parts";
import { memoryDraft } from "./autosave";
import { UnifiedDiff } from "@/components/chat/messages/unified-diff";
import { useTranslation } from "@/lib/i18n";
import styles from "./memory-page.module.css";

interface Revision { revision: string; timestamp: string; message: string }
export function MemoryDocument({ path, url, title, badge, meta, onDelete, onPreviewClick, onSaved }: {
  path: string; url: string; title: string; badge?: React.ReactNode; meta: string[];
  onDelete?: () => void | Promise<void>; onPreviewClick?: (e: React.MouseEvent) => void;
  onSaved?: () => void;
}) {
  const { text, locale } = useTranslation();
  const [draft] = useState(() => memoryDraft(url));
  const [state, setState] = useState(draft.state);
  const [mode, setMode] = useState<"preview" | "edit" | "changes" | "history">("preview");
  const [entries, setEntries] = useState<Revision[]>([]);
  const [more, setMore] = useState(false);
  const [revision, setRevision] = useState("");
  const [diff, setDiff] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [latest, setLatest] = useState<string | null>(null);
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
  useEffect(() => {
    if (mode !== "history") return;
    let active = true;
    setBusy(true); setError(""); setEntries([]); setRevision(""); setDiff("");
    fetch("/api/memory/history?" + new URLSearchParams({ path }))
      .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); return data; })
      .then(data => { if (active) { setEntries(data.entries); setMore(data.has_more); setRevision(data.entries[0]?.revision || ""); } })
      .catch(error => { if (active) setError(String(error)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [mode, path, state.base]);
  useEffect(() => {
    if (mode !== "changes" && !(mode === "history" && revision)) return;
    let active = true;
    setDiff(""); setBusy(true); setError("");
    const timer = setTimeout(() => {
      const request = mode === "changes"
        ? fetch("/api/memory/diff", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ before: latest ?? state.original, after: state.content }) })
        : fetch("/api/memory/history?" + new URLSearchParams({ path, revision }));
      request.then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); return data; })
        .then(data => { if (active) setDiff(data.diff); })
        .catch(error => { if (active) setError(String(error)); })
        .finally(() => { if (active) setBusy(false); });
    }, 150);
    return () => { active = false; clearTimeout(timer); };
  }, [mode, path, revision, state.content, state.original, latest]);
  async function older() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/memory/history?" + new URLSearchParams({ path, offset: String(entries.length) }));
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
      setLatest(data.content); setMode("changes");
    } catch (error) { setError(String(error)); }
  }
  const tools = <>
    <button className={styles.modeBtn} aria-pressed={mode === "changes"} onClick={() => { setLatest(null); setMode("changes"); }}>{text("Changes", "本次修改")}</button>
    <button className={styles.modeBtn} aria-pressed={mode === "history"} onClick={() => setMode("history")}>{text("History", "历史")}</button>
  </>;
  const notices = <>
    {state.error && <div role="alert" className={styles.editorNotice}>
      {state.error} <button onClick={() => draft.retry()}>{text("Retry", "重试")}</button>{" "}
      <button onClick={reviewLatest}>{text("Review latest", "查看最新版本")}</button>
    </div>}
    {state.warning && <div role="status" className={styles.editorNotice}>{state.warning}</div>}
    {latest !== null && mode === "changes" && <div className={styles.editorNotice}>
      {text("Latest saved version compared with your draft.", "以下比较最新已保存版本与当前草稿。")}
      <button onClick={() => {
        draft.publish({ base: latest, error: "" }); draft.persist(); setLatest(null); draft.schedule();
      }}>{text("Replace latest with this draft", "用当前草稿替换最新版本")}</button>
    </div>}
  </>;
  return <EditorPanel title={title} badge={badge} meta={meta}
    state={{ content: state.content, saving: state.saving, saveStatus: state.loaded && state.content === state.base && !state.warning ? "saved" : "", viewMode: mode }}
    onChange={content => draft.edit(content)} onViewMode={setMode}
    onDelete={state.loaded && !state.saving && state.content === state.base ? onDelete : undefined}
    onPreviewClick={onPreviewClick} tools={tools} notices={notices} loading={!state.loaded}
    detail={mode === "changes" || mode === "history" ? <div className={styles.historyPanel}>
      {mode === "history" && <div className={styles.revisionList}>
        {entries.map(entry => <button key={entry.revision} aria-pressed={revision === entry.revision} className={styles.revisionRow} onClick={() => setRevision(entry.revision)}>
          <time>{new Date(entry.timestamp).toLocaleString(locale)}</time>
          <span>{entry.message}</span><code>{entry.revision.slice(0, 8)}</code>
        </button>)}
        {more && <button disabled={busy} onClick={older}>{text("Load older versions", "加载更早版本")}</button>}
        {!busy && !entries.length && !error && <p>{text("No recorded changes for this file.", "该文件尚无修改记录。")}</p>}
      </div>}
      <div className={styles.historyDiff}>
        {error && <p role="alert">{error}</p>}
        {busy ? <p>{text("Loading…", "加载中…")}</p> : diff ? <UnifiedDiff diff={diff} /> : <p>{text("No changes.", "没有修改。")}</p>}
      </div>
    </div> : undefined}
  />;
}
