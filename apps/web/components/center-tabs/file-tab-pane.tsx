"use client";

/**
 * FileTabPane — center-column content of a file tab: a 40px toolbar
 * (breadcrumb · Save/Revert while dirty · Rendered/Source toggle for
 * markdown · download) over the FileViewer body. Only the ACTIVE file
 * tab mounts one of these; the viewer's read-cache keeps tab switches
 * fast.
 *
 * EDITOR semantics — text files are editable by default, no edit
 * mode: the viewer body is a gutter+textarea whose buffer lives here.
 * The buffer seeds from the file read (or from a surviving fileDrafts
 * entry) and dirty means draft !== baseline. Save (toolbar button or
 * Cmd/Ctrl+S) calls ``project_file_write`` with the baseline read's
 * mtime, so a concurrent on-disk change surfaces as a conflict notice
 * + Reload instead of a silent clobber. Unsaved buffers survive tab
 * switches via the in-memory fileDrafts map (page reload loses them);
 * the tab strip guards dirty-tab close with a confirm.
 */
import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { fileTabId, useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  SideBySideDiff,
  UnifiedDiff,
} from "@/components/chat/messages/unified-diff";
import {
  type FileReadResult,
  fileDraftKey,
  fileDrafts,
  filesWsRequest,
  invalidateFileRead,
  latestFileMtime,
  rawFileUrl,
  readCache,
} from "@/lib/state/files-shared";
import { FileViewer, IMAGE_EXTS } from "@/components/files/file-viewer";
import styles from "./center-tabs.module.css";
import fileStyles from "@/components/files/files-panel.module.css";

interface WriteResult {
  project_id: string;
  path: string;
  ok?: boolean;
  mtime?: number;
  conflict?: boolean;
  error?: string;
}

/** The editor buffer: draft + the read it drifted from. `path` tags
 * which file the buffer belongs to — a single atomic object so a
 * path switch can never pair one file's draft with another's
 * baseline. */
interface EditorBuffer {
  path: string;
  draft: string;
  baseline: string;
  baseMtime: number;
}

/** Which body a file tab shows. "file" is the plain editor (the only
 *  option when the tab carries no per-turn diff context). */
type ViewMode = "unified" | "split" | "file";

/** Cached `turn_file_diff` answer for this tab's turn. */
interface TabDiff {
  loading: boolean;
  diff: string;
  approximate: boolean;
  error?: string | null;
}

export function FileTabPane({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
}) {
  const { text } = useTranslation();
  const [rendered, setRendered] = useState(true);
  const [buffer, setBuffer] = useState<EditorBuffer | null>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  // Keyed by the read's own path instead of being reset on path change:
  // on a cache hit the child viewer reports synchronously BEFORE this
  // component's effects would run, so path-keying makes stale entries
  // inert without any effect-ordering dependency.
  const [loaded, setLoaded] = useState<FileReadResult | null>(null);
  const loadedForPath = loaded && loaded.path === path ? loaded : null;
  // Bumped by conflict-Reload so the viewer remounts and refetches.
  const [viewerEpoch, setViewerEpoch] = useState(0);

  const segments = path.split("/");
  const base = (segments[segments.length - 1] || "").toLowerCase();
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1) : "";
  const isMarkdown = ext === "md";

  // Per-turn diff context, set when the tab was opened from a turn's
  // file-edit card. Read off the tab itself so it survives tab
  // switches without this pane owning the state.
  const tabId = fileTabId(projectId, path);
  const tab = useCenterTabs((s) => s.tabs.find((t) => t.id === tabId));
  const diffSessionId = tab?.diffSessionId;
  const diffMsgId = tab?.diffMsgId;
  const scrollToLine = tab?.scrollToLine;
  const highlightLines = tab?.highlightLines;
  // Images/PDF have no meaningful text diff — those tabs go straight to
  // the existing preview no matter how they were opened.
  const diffable = !IMAGE_EXTS.has(ext) && ext !== "pdf";
  const hasDiff = Boolean(diffSessionId && diffMsgId && diffable);

  const [mode, setMode] = useState<ViewMode>("file");
  const [diff, setDiff] = useState<TabDiff | null>(null);

  // Default to the diff when the tab arrives with a turn context, and
  // back to the plain file when that context is cleared/absent.
  useEffect(() => {
    setMode(hasDiff ? "unified" : "file");
  }, [hasDiff, diffSessionId, diffMsgId, path]);

  // Fetch this turn's diff once per (turn, path).
  useEffect(() => {
    if (!hasDiff) {
      setDiff(null);
      return;
    }
    let alive = true;
    setDiff({ loading: true, diff: "", approximate: false });
    void filesWsRequest<{
      path?: string;
      diff?: string;
      approximate?: boolean;
      error?: string;
    }>(
      "turn_file_diff",
      { session_id: diffSessionId, assistant_msg_id: diffMsgId, path },
      "turn_file_diff_result",
    ).then((res) => {
      if (!alive) return;
      setDiff({
        loading: false,
        diff: res?.diff ?? "",
        approximate: Boolean(res?.approximate),
        error: res?.error ?? (res ? null : text("Not connected", "连接已断开")),
      });
    });
    return () => {
      alive = false;
    };
  }, [hasDiff, diffSessionId, diffMsgId, path, text]);
  // Images/PDF are never text-editable; binary / too-large reads have
  // no content; a truncated read must stay read-only (saving the cut
  // buffer would destroy the file's tail).
  const editableText =
    !IMAGE_EXTS.has(ext) &&
    ext !== "pdf" &&
    loadedForPath !== null &&
    loadedForPath.content !== undefined &&
    !loadedForPath.truncated;
  const bufferForPath =
    editableText && buffer && buffer.path === path ? buffer : null;
  const dirty = bufferForPath !== null && bufferForPath.draft !== bufferForPath.baseline;

  // Changing file resets transient save state (the buffer re-seeds
  // from the new file's read below; the old file's dirty draft is
  // already mirrored in fileDrafts).
  useEffect(() => {
    setConflict(false);
    setSaveFailed(false);
  }, [projectId, path]);

  // Seed the buffer when a read lands: restore a surviving draft from
  // fileDrafts (keeping ITS baseline+mtime, so a save after an on-disk
  // change still conflicts correctly), else baseline = the fresh read.
  useEffect(() => {
    if (!loadedForPath || loadedForPath.content === undefined || loadedForPath.truncated)
      return;
    const saved = fileDrafts.get(fileDraftKey(projectId, path));
    setBuffer(
      saved
        ? {
            path,
            draft: saved.draft,
            baseline: saved.baselineContent,
            baseMtime: saved.baselineMtime,
          }
        : {
            path,
            draft: loadedForPath.content,
            baseline: loadedForPath.content,
            baseMtime: loadedForPath.mtime,
          },
    );
  }, [loadedForPath, projectId, path]);

  // Mirror the buffer into the draft-survival map: dirty → upsert,
  // clean (typed back / reverted / saved) → drop. No unmount cleanup —
  // surviving unmount is the point.
  useEffect(() => {
    if (!bufferForPath) return;
    const key = fileDraftKey(projectId, path);
    if (bufferForPath.draft !== bufferForPath.baseline) {
      fileDrafts.set(key, {
        draft: bufferForPath.draft,
        baselineContent: bufferForPath.baseline,
        baselineMtime: bufferForPath.baseMtime,
      });
    } else {
      fileDrafts.delete(key);
    }
  }, [bufferForPath, projectId, path]);

  // Mirror the unsaved-changes state into the tab strip's dirty dot.
  // NO unmount/path-change cleanup: the draft survives the pane (in
  // fileDrafts), so the dot must too — it's what arms the strip's
  // discard-confirm on close. Save/revert clear it through this same
  // effect; a confirmed discard closes the whole tab.
  const setTabDirty = useCenterTabs((s) => s.setTabDirty);
  useEffect(() => {
    // Load gap (no buffer for the CURRENT path yet): leave the dot
    // alone — a reopened dirty tab keeps it until the draft restores.
    if (bufferForPath === null) return;
    setTabDirty(tabId, dirty);
  }, [tabId, dirty, bufferForPath, setTabDirty]);

  // "Jump to the change": scroll the target line into view once the
  // body exists, and flash it for ~2s so the eye lands on it. Runs in
  // the editor/preview body; diff bodies open at the top of the hunk,
  // which is already the change.
  // ponytail: line height read off the rendered gutter rather than
  // hardcoded, so it survives a font-size change.
  const bodyRef = useRef<HTMLDivElement>(null);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!scrollToLine || mode !== "file" || !loadedForPath) return;
    const host = bodyRef.current;
    if (!host) return;
    const gutter = host.querySelector<HTMLElement>("[data-line-metric]")
      ?? host.querySelector<HTMLElement>("pre, textarea");
    const lineH = gutter
      ? parseFloat(getComputedStyle(gutter).lineHeight) || 0
      : 0;
    if (lineH) {
      const scroller = host.querySelector<HTMLElement>("textarea, pre") ?? host;
      // Put the target a few lines below the top edge instead of flush
      // against it, so its context is visible too.
      scroller.scrollTop = Math.max(0, (scrollToLine - 4) * lineH);
    }
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 2000);
    return () => clearTimeout(t);
  }, [scrollToLine, mode, loadedForPath, path]);

  const save = async () => {
    if (saving || conflict) return; // Cmd+S has no disabled state
    const buf = bufferForPath;
    if (!buf || buf.draft === buf.baseline) return; // clean → nothing to save
    setSaving(true);
    setSaveFailed(false);
    const res = await filesWsRequest<WriteResult>(
      "project_file_write",
      {
        project_id: projectId,
        path,
        content: buf.draft,
        expected_mtime: buf.baseMtime,
        idempotency_key: crypto.randomUUID(),
      },
      "project_file_write_result",
    );
    setSaving(false);
    if (res?.ok) {
      const mtime = res.mtime ?? buf.baseMtime;
      // Re-baseline to what was WRITTEN — keystrokes typed while the
      // write was in flight stay dirty instead of being clobbered.
      setBuffer((prev) =>
        prev && prev.path === path
          ? { ...prev, baseline: buf.draft, baseMtime: mtime }
          : prev,
      );
      // Keep the shared read cache coherent (no remount/refetch): a
      // future viewer mount sees the saved content and fresh mtime.
      const cached = readCache.get(fileDraftKey(projectId, path));
      if (cached)
        readCache.set(fileDraftKey(projectId, path), {
          ...cached,
          content: buf.draft,
          mtime,
        });
      latestFileMtime.set(path, mtime);
    } else if (res?.conflict) {
      setConflict(true);
    } else {
      setSaveFailed(true);
    }
  };

  const revert = () => {
    setBuffer((prev) =>
      prev && prev.path === path ? { ...prev, draft: prev.baseline } : prev,
    );
    setSaveFailed(false);
  };

  /** Conflict recovery: drop the draft, refetch, re-baseline. */
  const reload = () => {
    fileDrafts.delete(fileDraftKey(projectId, path));
    invalidateFileRead(projectId, path);
    setConflict(false);
    setSaveFailed(false);
    setViewerEpoch((e) => e + 1); // remount viewer → refetch → re-seed
  };

  // Cmd/Ctrl+S saves while this pane's tab is the ACTIVE tab and the
  // file is editable text; everywhere else (images/PDF/binary, other
  // tab focused) the browser default stands. save() re-reads the
  // latest buffer via saveRef; its guards absorb repeats in flight.
  const saveRef = useRef(save);
  saveRef.current = save;
  const editableRef = useRef(editableText);
  editableRef.current = editableText;
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key !== "s") return;
      if (useCenterTabs.getState().activeId !== tabId) return;
      if (!editableRef.current) return;
      e.preventDefault();
      void saveRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [tabId]);

  return (
    <div className={styles.filePane}>
      <div className={styles.fileToolbar}>
        <span>
          {segments.map((seg, i) => (
            <span key={i}>
              <span
                className={i === segments.length - 1 ? styles.crumbLast : styles.crumb}
              >
                {seg}
              </span>
              {i < segments.length - 1 ? (
                <span className={styles.crumbSep}>›</span>
              ) : null}
            </span>
          ))}
        </span>
        <span className={styles.toolbarSpacer} />
        {dirty && mode === "file" ? (
          <>
            <button
              type="button"
              className={fileStyles.editSaveBtn}
              onClick={save}
              disabled={saving || conflict}
            >
              {saving ? text("Saving…", "保存中…") : text("Save", "保存")}
            </button>
            <button
              type="button"
              className={fileStyles.editCancelBtn}
              onClick={revert}
            >
              {text("Revert", "还原")}
            </button>
          </>
        ) : null}
        {/* View switch — only when this tab knows which turn it came
            from. Unified / Side-by-side render that turn's diff; the
            third option is the ordinary editor. */}
        {hasDiff ? (
          <span className={styles.mdToggle}>
            <button
              type="button"
              className={`${styles.mdToggleBtn} ${mode === "unified" ? styles.mdToggleActive : ""}`}
              onClick={() => setMode("unified")}
            >
              {text("Unified", "统一视图")}
            </button>
            <button
              type="button"
              className={`${styles.mdToggleBtn} ${mode === "split" ? styles.mdToggleActive : ""}`}
              onClick={() => setMode("split")}
            >
              {text("Side-by-side", "并排对比")}
            </button>
            <button
              type="button"
              className={`${styles.mdToggleBtn} ${mode === "file" ? styles.mdToggleActive : ""}`}
              onClick={() => setMode("file")}
            >
              {text("File", "原文件")}
            </button>
          </span>
        ) : null}
        {isMarkdown && mode === "file" ? (
          <span className={styles.mdToggle}>
            <button
              type="button"
              className={`${styles.mdToggleBtn} ${rendered ? styles.mdToggleActive : ""}`}
              onClick={() => setRendered(true)}
            >
              {text("Rendered", "渲染")}
            </button>
            <button
              type="button"
              className={`${styles.mdToggleBtn} ${!rendered ? styles.mdToggleActive : ""}`}
              onClick={() => setRendered(false)}
            >
              {text("Source", "源码")}
            </button>
          </span>
        ) : null}
        <a
          className={styles.downloadBtn}
          href={rawFileUrl(projectId, path)}
          download={segments[segments.length - 1]}
          title={text("Download", "下载")}
        >
          <Download size={14} />
        </a>
      </div>
      <div className={styles.fileBody} ref={bodyRef} data-flash={flash ? "1" : "0"}>
        {mode !== "file" ? (
          <div className={styles.diffHost}>
            {diff?.approximate ? (
              <div className="file-diff-note">
                {text(
                  "Approximate diff — the file changed after this turn, so unrelated edits may show.",
                  "近似差异——该文件在本轮之后又被改动过，可能混入无关的修改。",
                )}
              </div>
            ) : null}
            {!diff || diff.loading ? (
              <div className="file-diff-empty">
                {text("Loading diff…", "正在加载差异…")}
              </div>
            ) : diff.error ? (
              <div className="file-diff-empty is-error">{diff.error}</div>
            ) : diff.diff ? (
              mode === "split" ? (
                <SideBySideDiff diff={diff.diff} />
              ) : (
                <UnifiedDiff diff={diff.diff} />
              )
            ) : (
              <div className="file-diff-empty">
                {text("No textual changes.", "没有文本改动。")}
              </div>
            )}
          </div>
        ) : null}
        {conflict ? (
          <div className={fileStyles.conflictNote}>
            {text(
              "File changed on disk — reload to edit the latest version",
              "文件在磁盘上已变化——请重新加载后再编辑",
            )}
            <button
              type="button"
              className={fileStyles.conflictReload}
              onClick={reload}
            >
              {text("Reload", "重新加载")}
            </button>
          </div>
        ) : saveFailed ? (
          <div className={fileStyles.conflictNote}>
            {text("Save failed.", "保存失败。")}
          </div>
        ) : null}
        {/* Kept MOUNTED in diff mode (just hidden): it owns the file
            read that seeds the editor buffer, so unmounting it would
            re-fetch and drop an unsaved draft on every mode switch. */}
        <div
          className={mode === "file" ? styles.viewerHost : styles.viewerHidden}
        >
        <FileViewer
          key={viewerEpoch}
          projectId={projectId}
          path={path}
          mdRendered={rendered}
          draft={bufferForPath?.draft}
          onDraftChange={
            bufferForPath
              ? (value) =>
                  setBuffer((prev) =>
                    prev && prev.path === path ? { ...prev, draft: value } : prev,
                  )
              : undefined
          }
          onLoaded={setLoaded}
        />
        </div>
      </div>
    </div>
  );
}
