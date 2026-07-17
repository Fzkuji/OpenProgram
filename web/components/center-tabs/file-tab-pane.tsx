"use client";

/**
 * FileTabPane — center-column content of a file tab: a 48px toolbar
 * (breadcrumb · Rendered/Source toggle for markdown · edit · download)
 * over the FileViewer body. Only the ACTIVE file tab mounts one of
 * these; the viewer's read-cache keeps tab switches fast.
 *
 * Edit mode (memory-page pattern, no editor dependency): the body
 * swaps to a full-pane <textarea>; Save calls ``project_file_write``
 * with the mtime from the last read, so a concurrent on-disk change
 * surfaces as a conflict notice + Reload instead of a silent clobber.
 * ponytail: v1 — switching/closing the tab while dirty loses changes.
 */
import { useEffect, useState } from "react";
import { Download, Pencil } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  type FileReadResult,
  filesWsRequest,
  invalidateFileRead,
  rawFileUrl,
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

export function FileTabPane({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
}) {
  const { text } = useTranslation();
  const [rendered, setRendered] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [baseMtime, setBaseMtime] = useState(0);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  // Keyed by the read's own path instead of being reset on path change:
  // on a cache hit the child viewer reports synchronously BEFORE this
  // component's reset effect would run, so a reset-after-report wipes
  // the value and Edit stays disabled forever. Path-keying makes stale
  // entries inert without any effect-ordering dependency.
  const [loaded, setLoaded] = useState<FileReadResult | null>(null);
  const loadedForPath = loaded && loaded.path === path ? loaded : null;
  // Bumped after a save so the viewer remounts and refetches.
  const [viewerEpoch, setViewerEpoch] = useState(0);

  const segments = path.split("/");
  const base = (segments[segments.length - 1] || "").toLowerCase();
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1) : "";
  const isMarkdown = ext === "md";
  // Images/PDF are never text-editable; binary / too-large text reads
  // disable Edit once the viewer's read lands.
  const canEdit =
    !IMAGE_EXTS.has(ext) &&
    ext !== "pdf" &&
    loadedForPath !== null &&
    loadedForPath.content !== undefined;

  // Changing file resets edit state (v1: unsaved changes are lost).
  // `loaded` is deliberately NOT nulled here — path-keying already
  // sidelines stale entries, and nulling would race the child viewer's
  // synchronous cache-hit report (child effects fire before this one).
  useEffect(() => {
    setEditing(false);
    setConflict(false);
    setSaveFailed(false);
  }, [projectId, path]);

  const enterEdit = async () => {
    // Fresh read on entry — its mtime is the optimistic-lock token.
    const res = await filesWsRequest<FileReadResult>(
      "project_file_read",
      { project_id: projectId, path },
      "project_file_read_result",
    );
    if (!res || res.error || res.content === undefined || res.path !== path)
      return;
    setDraft(res.content);
    setBaseMtime(res.mtime);
    setConflict(false);
    setSaveFailed(false);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setSaveFailed(false);
    const res = await filesWsRequest<WriteResult>(
      "project_file_write",
      { project_id: projectId, path, content: draft, expected_mtime: baseMtime },
      "project_file_write_result",
    );
    setSaving(false);
    if (res?.ok) {
      invalidateFileRead(projectId, path);
      setEditing(false);
      setViewerEpoch((e) => e + 1); // remount viewer → refetch
    } else if (res?.conflict) {
      setConflict(true);
    } else {
      setSaveFailed(true);
    }
  };

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
        {editing ? (
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
              onClick={() => {
                setEditing(false);
                setConflict(false);
                setSaveFailed(false);
              }}
            >
              {text("Cancel", "取消")}
            </button>
          </>
        ) : (
          <>
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
            <button
              type="button"
              className={fileStyles.iconBtn}
              onClick={enterEdit}
              disabled={!canEdit}
              title={text("Edit", "编辑")}
            >
              <Pencil size={14} />
            </button>
            <a
              className={styles.downloadBtn}
              href={rawFileUrl(projectId, path)}
              download={segments[segments.length - 1]}
              title={text("Download", "下载")}
            >
              <Download size={14} />
            </a>
          </>
        )}
      </div>
      <div className={styles.fileBody}>
        {editing ? (
          <>
            {conflict ? (
              <div className={fileStyles.conflictNote}>
                {text(
                  "File changed on disk — reload to edit the latest version",
                  "文件在磁盘上已变化——请重新加载后再编辑",
                )}
                <button
                  type="button"
                  className={fileStyles.conflictReload}
                  onClick={enterEdit}
                >
                  {text("Reload", "重新加载")}
                </button>
              </div>
            ) : saveFailed ? (
              <div className={fileStyles.conflictNote}>
                {text("Save failed.", "保存失败。")}
              </div>
            ) : null}
            <textarea
              className={fileStyles.editTextarea}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
            />
          </>
        ) : (
          <FileViewer
            key={viewerEpoch}
            projectId={projectId}
            path={path}
            mdRendered={rendered}
            onLoaded={setLoaded}
          />
        )}
      </div>
    </div>
  );
}
