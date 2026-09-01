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
 * switches via the shared fileDrafts map, which is mirrored to IndexedDB;
 * the tab strip guards dirty-tab close with a confirm.
 */
import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  idempotencyKeyFor,
  MutationRegistryCapacityError,
  wsMutationRequest,
} from "@/lib/net/ws-request";
import { fileTabId, useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  type FileReadResult,
  cacheFileRead,
  canPersistFileDraft,
  discardFileDraft,
  fileDraftKey,
  fileDrafts,
  filesWsRequest,
  invalidateFileRead,
  loadFileDraft,
  noteFileMtime,
  persistFileDraft,
  rawFileUrl,
  getCachedFileRead,
  useDraftPersistenceError,
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
  status?: string;
  error_code?: string;
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
  const [draftHydrated, setDraftHydrated] = useState(false);
  const [draftPersistError, setDraftPersistError] = useState<string | null>(null);
  const persistentDraftError = useDraftPersistenceError(fileDraftKey(projectId, path));
  // Keyed by the read's own path instead of being reset on path change:
  // on a cache hit the child viewer reports synchronously BEFORE this
  // component's effects would run, so path-keying makes stale entries
  // inert without any effect-ordering dependency.
  const [loaded, setLoaded] = useState<FileReadResult | null>(null);
  const loadedForPath = loaded && loaded.path === path ? loaded : null;
  // Bumped by conflict-Reload so the viewer remounts and refetches.
  const [viewerEpoch, setViewerEpoch] = useState(0);
  const saveControllerRef = useRef<AbortController | null>(null);
  const draftGeneration = useRef(0);
  const draftOperationGeneration = useRef(0);

  const segments = path.split("/");
  const base = (segments[segments.length - 1] || "").toLowerCase();
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1) : "";
  const isMarkdown = ext === "md";

  const tabId = fileTabId(projectId, path);
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
    draftGeneration.current += 1;
    const generation = draftGeneration.current;
    setConflict(false);
    setSaveFailed(false);
    setSaving(false);
    saveControllerRef.current?.abort();
    saveControllerRef.current = null;
    setDraftPersistError(null);
    setDraftHydrated(false);
    setBuffer(null);
    let alive = true;
    void loadFileDraft(projectId, path).then(() => {
      if (alive && generation === draftGeneration.current) setDraftHydrated(true);
    });
    return () => {
      alive = false;
      saveControllerRef.current?.abort();
    };
  }, [projectId, path]);

  // Seed the buffer when a read lands: restore a surviving draft from
  // fileDrafts (keeping ITS baseline+mtime, so a save after an on-disk
  // change still conflicts correctly), else baseline = the fresh read.
  useEffect(() => {
    if (!draftHydrated || !loadedForPath || loadedForPath.content === undefined || loadedForPath.truncated)
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
  }, [draftHydrated, loadedForPath, projectId, path]);

  // Mirror the buffer into the draft-survival map: dirty → upsert,
  // clean (typed back / reverted / saved) → drop. No unmount cleanup —
  // surviving unmount is the point.
  useEffect(() => {
    if (!bufferForPath) return;
    const generation = draftGeneration.current;
    const operationGeneration = ++draftOperationGeneration.current;
    const key = fileDraftKey(projectId, path);
    if (bufferForPath.draft !== bufferForPath.baseline) {
      const draft = {
        draft: bufferForPath.draft,
        baselineContent: bufferForPath.baseline,
        baselineMtime: bufferForPath.baseMtime,
      };
      if (!canPersistFileDraft(key, draft)) {
        setDraftPersistError(text(
          "Local draft storage is full — save, export, or discard another draft first.",
          "本地草稿空间已满——请先保存、导出或丢弃其他草稿。",
        ));
        return;
      }
      void persistFileDraft(projectId, path, draft).then((result) => {
        if (generation !== draftGeneration.current || operationGeneration !== draftOperationGeneration.current) return;
        if (!result.ok) setDraftPersistError(result.message ?? text("Unable to save local draft.", "无法保存本地草稿。"))
        else setDraftPersistError(null);
      });
    } else {
      void discardFileDraft(projectId, path).then((result) => {
        if (generation !== draftGeneration.current || operationGeneration !== draftOperationGeneration.current) return;
        if (!result.ok) setDraftPersistError(result.message ?? text("Unable to discard local draft.", "无法丢弃本地草稿。"));
      });
    }
  }, [bufferForPath, projectId, path, text]);

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
    if (!loadedForPath) return;
    setFlash(false);
  }, [loadedForPath, path]);

  const save = async () => {
    if (saving || conflict) return; // Cmd+S has no disabled state
    const buf = bufferForPath;
    if (!buf || buf.draft === buf.baseline) return; // clean → nothing to save
    const generation = draftGeneration.current;
    setSaving(true);
    setSaveFailed(false);
    const controller = new AbortController();
    saveControllerRef.current = controller;
    const operationPayload = {
      project_id: projectId,
      path,
      content: buf.draft,
      expected_mtime: buf.baseMtime,
    };
    let operationKey: string;
    try {
      operationKey = idempotencyKeyFor("project_file_write", operationPayload);
    } catch (error) {
      setSaving(false);
      setSaveFailed(true);
      if (error instanceof MutationRegistryCapacityError) {
        window.alert(text("Too many file operations are still pending.", "仍有太多文件操作未完成。"));
      }
      return;
    }
    const res = await wsMutationRequest<WriteResult>(
      operationKey,
      (signal) => filesWsRequest<WriteResult>(
        "project_file_write",
        { ...operationPayload, idempotency_key: operationKey },
        "project_file_write_result",
        { signal },
      ),
      { signal: controller.signal },
    );
    if (controller.signal.aborted) {
      setSaving(false);
      return;
    }
    if (generation !== draftGeneration.current) return;
    setSaving(false);
    if (res?.project_id !== projectId || res.path !== path) {
      setSaveFailed(true);
    } else if (res?.ok && res.status !== "error" && res.status !== "conflict"
      && res.status !== "recovery_required") {
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
      const cached = getCachedFileRead(projectId, path);
      noteFileMtime(projectId, path, mtime);
      if (cached)
        cacheFileRead({
          ...cached,
          content: buf.draft,
          mtime,
        });
    } else if (res?.conflict || res?.status === "conflict") {
      setConflict(true);
    } else {
      setSaveFailed(true);
    }
  };

  const revert = async () => {
    const generation = draftGeneration.current;
    const result = await discardFileDraft(projectId, path);
    if (generation !== draftGeneration.current) return;
    if (!result.ok) {
      setDraftPersistError(result.message ?? text("Unable to discard local draft.", "无法丢弃本地草稿。"));
      return;
    }
    setBuffer((prev) =>
      prev && prev.path === path ? { ...prev, draft: prev.baseline } : prev,
    );
    setSaveFailed(false);
  };

  /** Conflict recovery: drop the draft, refetch, re-baseline. */
  const reload = async () => {
    const generation = draftGeneration.current;
    const result = await discardFileDraft(projectId, path);
    if (generation !== draftGeneration.current) return;
    if (!result.ok) {
      setDraftPersistError(result.message ?? text("Unable to discard local draft.", "无法丢弃本地草稿。"));
      return;
    }
    invalidateFileRead(projectId, path);
    setConflict(false);
    setSaveFailed(false);
    setViewerEpoch((e) => e + 1); // remount viewer → refetch → re-seed
  };

  function updateDraft(value: string): void {
    const current = bufferForPath;
    if (!current) return;
    if (value !== current.baseline) {
      const candidate = {
        draft: value,
        baselineContent: current.baseline,
        baselineMtime: current.baseMtime,
      };
      if (!canPersistFileDraft(fileDraftKey(projectId, path), candidate)) {
        setDraftPersistError(text(
          "Local draft storage is full — save, export, or discard another draft first.",
          "本地草稿空间已满——请先保存、导出或丢弃其他草稿。",
        ));
        return;
      }
    }
    setDraftPersistError(null);
    setBuffer((prev) => prev && prev.path === path ? { ...prev, draft: value } : prev);
  }

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
      <div className={styles.fileBody} ref={bodyRef} data-flash={flash ? "1" : "0"}>
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
        ) : draftPersistError || persistentDraftError ? (
          <div className={fileStyles.conflictNote}>{draftPersistError || persistentDraftError}</div>
        ) : null}
        <div className={styles.viewerHost}>
        <FileViewer
          key={viewerEpoch}
          projectId={projectId}
          path={path}
          mdRendered={rendered}
          draft={bufferForPath?.draft}
          onDraftChange={
            bufferForPath
              ? updateDraft
              : undefined
          }
          onLoaded={setLoaded}
        />
        </div>
      </div>
    </div>
  );
}
