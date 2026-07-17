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
  const tabId = fileTabId(projectId, path);
  const setTabDirty = useCenterTabs((s) => s.setTabDirty);
  useEffect(() => {
    // Load gap (no buffer for the CURRENT path yet): leave the dot
    // alone — a reopened dirty tab keeps it until the draft restores.
    if (bufferForPath === null) return;
    setTabDirty(tabId, dirty);
  }, [tabId, dirty, bufferForPath, setTabDirty]);

  const save = async () => {
    if (saving || conflict) return; // Cmd+S has no disabled state
    const buf = bufferForPath;
    if (!buf || buf.draft === buf.baseline) return; // clean → nothing to save
    setSaving(true);
    setSaveFailed(false);
    const res = await filesWsRequest<WriteResult>(
      "project_file_write",
      { project_id: projectId, path, content: buf.draft, expected_mtime: buf.baseMtime },
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
        {dirty ? (
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
        {isMarkdown ? (
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
      <div className={styles.fileBody}>
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
  );
}
